from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .db import APP_ROOT, DEFAULT_CATEGORIES, connect, init_db, reset_request_db_path, set_request_db_path, transaction
from .demo import DEMO_COOKIE, cleanup_stale_sessions, demo_data_dir, demo_mode_enabled, new_session_id, seed_demo_data, session_db_path, valid_session_id
from .domain import daily_simple_interest_minor, parse_transaction_text, projected_dates
from .schemas import BackupIn, CreditFacilityIn, CreditPaymentIn, LoanBalanceIn, RecordOccurrenceIn, RecurringIn, ResetDataIn, TransactionIn, VoiceIn

app = FastAPI(title="Olos Personal Budget Tracker", version="1.0.0", docs_url="/api/docs", redoc_url=None)
STATIC_DIR = APP_ROOT / "frontend"


@app.middleware("http")
async def isolate_hosted_demo_session(request: Request, call_next):
    """Give each hosted demo browser an opaque, disposable SQLite database."""
    if not demo_mode_enabled():
        return await call_next(request)
    if request.url.path == "/api/health":
        # Hosting probes do not retain cookies. Use one stable, unseeded DB so a
        # new synthetic session file is not created on every health check.
        health_path = demo_data_dir() / "_health.sqlite3"
        token = set_request_db_path(health_path)
        try:
            if not health_path.exists():
                init_db()
            return await call_next(request)
        finally:
            reset_request_db_path(token)
    session_id = valid_session_id(request.cookies.get(DEMO_COOKIE))
    new_session = session_id is None
    session_id = session_id or new_session_id()
    if new_session:
        cleanup_stale_sessions()
    path = session_db_path(session_id)
    token = set_request_db_path(path)
    try:
        if not path.exists():
            init_db()
            seed_demo_data()
        response = await call_next(request)
        if new_session:
            response.set_cookie(DEMO_COOKIE, session_id, max_age=86_400, httponly=True,
                                secure=request.url.scheme == "https", samesite="lax")
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        reset_request_db_path(token)


def row_dict(row):
    result = dict(row)
    for key in ("active", "automated_externally"):
        if key in result: result[key] = bool(result[key])
    return result


@app.on_event("startup")
def startup():
    if not demo_mode_enabled():
        init_db()


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    friendly = {
        "amount_minor": "Enter an amount greater than zero",
        "description": "Enter a description",
        "transaction_date": "Choose a valid date",
        "category": "Choose or enter a category",
        "transaction_type": "Choose a valid transaction type",
        "credit_facility_id": "Choose the account used",
        "payment_method": "Choose a valid payment method",
    }
    errors = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"][1:]) or "form"
        errors.append({"field": field, "message": friendly.get(field, str(error["msg"]).replace("Value error, ", ""))})
    detail = errors[0]["message"] if errors else "Check the entered values"
    return JSONResponse(status_code=422, content={"detail": detail, "errors": errors})


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, exc: Exception):
    if isinstance(exc, HTTPException): raise exc
    return JSONResponse(status_code=500, content={"detail": "The local database could not complete that request. Your previous data was not changed."})


@app.get("/api/health")
def health():
    with connect() as db: db.execute("SELECT 1").fetchone()
    storage = "session-isolated-demo" if demo_mode_enabled() else "local-sqlite"
    return {"status": "ok", "currency": "AUD", "storage": storage}


@app.get("/api/runtime")
def runtime():
    demo = demo_mode_enabled()
    return {"mode": "demo" if demo else "local", "disposable": demo, "currency": "AUD"}


@app.get("/api/categories")
def categories():
    with connect() as db:
        return [row_dict(row) for row in db.execute("SELECT id,name,transaction_type FROM categories ORDER BY transaction_type,name")]


def facility_dict(row, db=None):
    result = row_dict(row)
    result["available_credit_minor"] = None if result["facility_type"] == "fixed_loan" else result["credit_limit_minor"] - result["amount_owed_minor"]
    result["estimated_balance"] = result["facility_type"] == "fixed_loan"
    result["linked_recurring_rule_id"] = None
    if db is not None and result["facility_type"] == "fixed_loan":
        linked = db.execute("SELECT id FROM recurring_rules WHERE linked_fixed_loan_id=? AND active=1 ORDER BY id LIMIT 1", (result["id"],)).fetchone()
        result["linked_recurring_rule_id"] = linked["id"] if linked else None
    return result


def set_loan_link(db, facility_id: int, rule_id: int | None) -> None:
    db.execute("UPDATE recurring_rules SET linked_fixed_loan_id=NULL,updated_at=CURRENT_TIMESTAMP WHERE linked_fixed_loan_id=?", (facility_id,))
    if rule_id is None:
        return
    rule = db.execute("SELECT * FROM recurring_rules WHERE id=? AND active=1", (rule_id,)).fetchone()
    if not rule or rule["transaction_type"] != "bill":
        raise HTTPException(422, "Choose an active recurring bill for this loan")
    other = db.execute("SELECT id FROM recurring_rules WHERE id=? AND linked_fixed_loan_id IS NOT NULL AND linked_fixed_loan_id<>?", (rule_id, facility_id)).fetchone()
    if other:
        raise HTTPException(422, "That recurring bill is already linked to another loan")
    db.execute("UPDATE recurring_rules SET linked_fixed_loan_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (facility_id, rule_id))


def apply_loan_payment(db, facility, amount_minor: int, payment_date: date, transaction_id: int, note: str | None = None) -> int:
    if facility["facility_type"] != "fixed_loan":
        raise HTTPException(422, "This account is not a fixed loan")
    as_of = date.fromisoformat(facility["balance_as_of_date"] or facility["created_at"][:10])
    if payment_date < as_of:
        raise HTTPException(422, f"Payment date cannot be before the loan balance date ({as_of.isoformat()})")
    before = facility["amount_owed_minor"]
    interest = daily_simple_interest_minor(before, facility["annual_rate_basis_points"], (payment_date - as_of).days)
    balance_with_interest = before + interest
    if amount_minor > balance_with_interest:
        raise HTTPException(422, "Payment cannot be more than the estimated loan balance")
    after = balance_with_interest - amount_minor
    db.execute("UPDATE credit_facilities SET amount_owed_minor=?,balance_as_of_date=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (after, payment_date.isoformat(), facility["id"]))
    db.execute("""INSERT INTO loan_balance_events(facility_id,event_type,event_date,balance_before_minor,interest_minor,amount_minor,balance_after_minor,transaction_id,note)
      VALUES (?,'payment',?,?,?,?,?,?,?)""", (facility["id"], payment_date.isoformat(), before, interest, amount_minor, after, transaction_id, note))
    return after


def apply_facility_effect(db, transaction_record, direction: int) -> None:
    transaction_record = dict(transaction_record)
    facility_id = transaction_record.get("credit_facility_id")
    if not facility_id:
        return
    role = transaction_record.get("transaction_role", "ordinary")
    method = transaction_record.get("payment_method")
    if role == "credit_payment":
        delta = -transaction_record["amount_minor"] * direction
    elif transaction_record["transaction_type"] == "expense" and method in {"credit", "pay_later"}:
        delta = transaction_record["amount_minor"] * direction
    else:
        return
    facility = db.execute("SELECT * FROM credit_facilities WHERE id=?", (facility_id,)).fetchone()
    if not facility:
        raise HTTPException(422, "Choose an active credit or pay-later account")
    if direction > 0 and not facility["active"]:
        raise HTTPException(422, "Choose an active credit or pay-later account")
    if role != "credit_payment" and facility["facility_type"] != method:
        raise HTTPException(422, "The selected account does not match the payment method")
    new_owed = facility["amount_owed_minor"] + delta
    if new_owed < 0:
        raise HTTPException(422, "Payment cannot be more than the amount owed")
    if new_owed > facility["credit_limit_minor"]:
        raise HTTPException(422, "This expense is more than the account's available credit")
    db.execute("UPDATE credit_facilities SET amount_owed_minor=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_owed, facility_id))


@app.get("/api/transactions")
def list_transactions(
    limit: int = Query(30, ge=1, le=200),
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    from_date: date | None = None,
    to_date: date | None = None,
    transaction_type: str | None = Query(None, pattern=r"^(expense|income|bill|savings)$"),
    category: str | None = Query(None, min_length=1, max_length=80),
):
    if from_date and to_date and from_date > to_date:
        raise HTTPException(422, "Start date must not be after end date")
    clauses, params = [], []
    if month: clauses.append("substr(transaction_date,1,7)=?"); params.append(month)
    if from_date: clauses.append("transaction_date>=?"); params.append(from_date.isoformat())
    if to_date: clauses.append("transaction_date<=?"); params.append(to_date.isoformat())
    if transaction_type: clauses.append("transaction_type=?"); params.append(transaction_type)
    if category: clauses.append("lower(category)=lower(?)"); params.append(category.strip())
    sql = "SELECT * FROM transactions"
    if clauses: sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY transaction_date DESC, id DESC LIMIT ?"; params.append(limit)
    with connect() as db: return [row_dict(row) for row in db.execute(sql, params)]


@app.get("/api/transactions/{transaction_id}")
def get_transaction(transaction_id: int):
    with connect() as db:
        item = db.execute("SELECT * FROM transactions WHERE id=?", (transaction_id,)).fetchone()
    if not item:
        raise HTTPException(404, "Transaction not found")
    return row_dict(item)


@app.post("/api/transactions", status_code=201)
def create_transaction(item: TransactionIn, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with transaction() as db:
        if idempotency_key:
            existing = db.execute("SELECT resource_id FROM idempotency_keys WHERE key=? AND resource_type='transaction'", (idempotency_key,)).fetchone()
            if existing: return row_dict(db.execute("SELECT * FROM transactions WHERE id=?", (existing[0],)).fetchone())
        values = item.model_dump(mode="json") | {"transaction_role": "ordinary"}
        apply_facility_effect(db, values, 1)
        cursor = db.execute("""INSERT INTO transactions(transaction_type,amount_minor,currency,description,category,transaction_date,note,payment_method,credit_facility_id,transaction_role)
          VALUES (:transaction_type,:amount_minor,:currency,:description,:category,:transaction_date,:note,:payment_method,:credit_facility_id,:transaction_role)""", values)
        if idempotency_key: db.execute("INSERT INTO idempotency_keys(key,resource_type,resource_id) VALUES (?,'transaction',?)", (idempotency_key, cursor.lastrowid))
        return row_dict(db.execute("SELECT * FROM transactions WHERE id=?", (cursor.lastrowid,)).fetchone())


@app.put("/api/transactions/{transaction_id}")
def update_transaction(transaction_id: int, item: TransactionIn):
    with transaction() as db:
        existing = db.execute("SELECT * FROM transactions WHERE id=?", (transaction_id,)).fetchone()
        if not existing: raise HTTPException(404, "Transaction not found")
        if existing["transaction_role"] in {"credit_payment", "loan_payment"}: raise HTTPException(422, "Repayment transactions cannot be edited; reconcile the balance if needed")
        apply_facility_effect(db, existing, -1)
        values = item.model_dump(mode="json") | {"id": transaction_id, "transaction_role": "ordinary"}
        apply_facility_effect(db, values, 1)
        db.execute("""UPDATE transactions SET transaction_type=:transaction_type,amount_minor=:amount_minor,currency=:currency,
          description=:description,category=:category,transaction_date=:transaction_date,note=:note,payment_method=:payment_method,
          credit_facility_id=:credit_facility_id,transaction_role=:transaction_role,updated_at=CURRENT_TIMESTAMP WHERE id=:id""", values)
        return row_dict(db.execute("SELECT * FROM transactions WHERE id=?", (transaction_id,)).fetchone())


@app.delete("/api/transactions/{transaction_id}", status_code=204)
def delete_transaction(transaction_id: int):
    with transaction() as db:
        occurrence = db.execute("SELECT * FROM transactions WHERE id=?", (transaction_id,)).fetchone()
        if not occurrence: raise HTTPException(404, "Transaction not found")
        if occurrence["transaction_role"] == "loan_payment": raise HTTPException(422, "Loan payments are preserved; reconcile the estimated loan balance instead")
        apply_facility_effect(db, occurrence, -1)
        db.execute("DELETE FROM transactions WHERE id=?", (transaction_id,))
        if occurrence["recurring_occurrence_id"]: db.execute("DELETE FROM recurring_occurrences WHERE id=?", (occurrence["recurring_occurrence_id"],))
    return Response(status_code=204)


@app.get("/api/credit-facilities")
def list_credit_facilities():
    with connect() as db:
        return [facility_dict(row, db) for row in db.execute("SELECT * FROM credit_facilities WHERE active=1 ORDER BY name,id")]


@app.post("/api/credit-facilities", status_code=201)
def create_credit_facility(item: CreditFacilityIn):
    values = item.model_dump(mode="json")
    with transaction() as db:
        cursor = db.execute("""INSERT INTO credit_facilities(name,facility_type,credit_limit_minor,amount_owed_minor,annual_rate_basis_points,balance_as_of_date,currency,note)
          VALUES (:name,:facility_type,:credit_limit_minor,:amount_owed_minor,:annual_rate_basis_points,:balance_as_of_date,:currency,:note)""", values)
        if item.facility_type == "fixed_loan":
            db.execute("""INSERT INTO loan_balance_events(facility_id,event_type,event_date,balance_before_minor,balance_after_minor,note)
              VALUES (?,'created',?,0,?,?)""", (cursor.lastrowid, item.balance_as_of_date.isoformat(), item.amount_owed_minor, item.note))
            set_loan_link(db, cursor.lastrowid, item.linked_recurring_rule_id)
        return facility_dict(db.execute("SELECT * FROM credit_facilities WHERE id=?", (cursor.lastrowid,)).fetchone(), db)


@app.put("/api/credit-facilities/{facility_id}")
def update_credit_facility(facility_id: int, item: CreditFacilityIn):
    values = item.model_dump(mode="json") | {"id": facility_id}
    with transaction() as db:
        existing = db.execute("SELECT * FROM credit_facilities WHERE id=? AND active=1", (facility_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Credit facility not found")
        if existing["facility_type"] != item.facility_type:
            raise HTTPException(422, "Account type cannot be changed after creation")
        if item.facility_type == "fixed_loan":
            effective = item.balance_as_of_date
            previous_date = date.fromisoformat(existing["balance_as_of_date"] or existing["created_at"][:10])
            if effective < previous_date:
                raise HTTPException(422, "Rate date cannot be before the current loan balance date")
            owed = existing["amount_owed_minor"]
            if item.annual_rate_basis_points != existing["annual_rate_basis_points"]:
                interest = daily_simple_interest_minor(owed, existing["annual_rate_basis_points"], (effective - previous_date).days)
                adjusted = owed + interest
                db.execute("""INSERT INTO loan_balance_events(facility_id,event_type,event_date,balance_before_minor,interest_minor,balance_after_minor,note)
                  VALUES (?,'rate_change',?,?,?,?,?)""", (facility_id, effective.isoformat(), owed, interest, adjusted, "Interest accrued before rate change"))
                owed = adjusted
            values["amount_owed_minor"] = owed
            set_loan_link(db, facility_id, item.linked_recurring_rule_id)
        db.execute("""UPDATE credit_facilities SET name=:name,credit_limit_minor=:credit_limit_minor,
          amount_owed_minor=:amount_owed_minor,annual_rate_basis_points=:annual_rate_basis_points,balance_as_of_date=:balance_as_of_date,
          currency=:currency,note=:note,updated_at=CURRENT_TIMESTAMP WHERE id=:id""", values)
        return facility_dict(db.execute("SELECT * FROM credit_facilities WHERE id=?", (facility_id,)).fetchone(), db)


@app.delete("/api/credit-facilities/{facility_id}", status_code=204)
def deactivate_credit_facility(facility_id: int):
    with transaction() as db:
        facility = db.execute("SELECT * FROM credit_facilities WHERE id=? AND active=1", (facility_id,)).fetchone()
        if not facility: raise HTTPException(404, "Credit facility not found")
        db.execute("UPDATE recurring_rules SET linked_fixed_loan_id=NULL,updated_at=CURRENT_TIMESTAMP WHERE linked_fixed_loan_id=?", (facility_id,))
        db.execute("UPDATE credit_facilities SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?", (facility_id,))
    return Response(status_code=204)


@app.post("/api/credit-facilities/{facility_id}/payments", status_code=201)
def record_credit_payment(facility_id: int, item: CreditPaymentIn):
    with transaction() as db:
        facility = db.execute("SELECT * FROM credit_facilities WHERE id=? AND active=1", (facility_id,)).fetchone()
        if not facility: raise HTTPException(404, "Credit facility not found")
        role = "loan_payment" if facility["facility_type"] == "fixed_loan" else "credit_payment"
        record = {"transaction_type": "bill", "amount_minor": item.amount_minor, "credit_facility_id": facility_id,
                  "transaction_role": role, "payment_method": "debit"}
        if role == "credit_payment": apply_facility_effect(db, record, 1)
        cursor = db.execute("""INSERT INTO transactions(transaction_type,amount_minor,currency,description,category,transaction_date,note,payment_method,credit_facility_id,transaction_role)
          VALUES ('bill',?,?,?,?,?,?, 'debit',?,?)""",
          (item.amount_minor, facility["currency"], f"{facility['name']} payment", "Loan repayment" if role == "loan_payment" else "Credit repayment", item.transaction_date.isoformat(), item.note, facility_id, role))
        if role == "loan_payment": apply_loan_payment(db, facility, item.amount_minor, item.transaction_date, cursor.lastrowid, item.note)
        return row_dict(db.execute("SELECT * FROM transactions WHERE id=?", (cursor.lastrowid,)).fetchone())


@app.post("/api/credit-facilities/{facility_id}/reconcile")
def reconcile_loan_balance(facility_id: int, item: LoanBalanceIn):
    with transaction() as db:
        facility = db.execute("SELECT * FROM credit_facilities WHERE id=? AND active=1", (facility_id,)).fetchone()
        if not facility: raise HTTPException(404, "Credit facility not found")
        if facility["facility_type"] != "fixed_loan": raise HTTPException(422, "Only fixed loans have an estimated balance")
        previous_date = date.fromisoformat(facility["balance_as_of_date"] or facility["created_at"][:10])
        if item.balance_as_of_date < previous_date: raise HTTPException(422, "Balance date cannot be before the current loan balance date")
        db.execute("UPDATE credit_facilities SET amount_owed_minor=?,balance_as_of_date=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (item.amount_owed_minor, item.balance_as_of_date.isoformat(), facility_id))
        db.execute("""INSERT INTO loan_balance_events(facility_id,event_type,event_date,balance_before_minor,balance_after_minor,note)
          VALUES (?,'reconciliation',?,?,?,?)""", (facility_id, item.balance_as_of_date.isoformat(), facility["amount_owed_minor"], item.amount_owed_minor, item.note))
        return facility_dict(db.execute("SELECT * FROM credit_facilities WHERE id=?", (facility_id,)).fetchone(), db)


@app.get("/api/recurring")
def list_recurring():
    with connect() as db: return [row_dict(row) for row in db.execute("SELECT * FROM recurring_rules ORDER BY active DESC,next_due_date,id")]


@app.post("/api/recurring", status_code=201)
def create_recurring(item: RecurringIn):
    if item.end_date and item.end_date < item.start_date: raise HTTPException(422, "End date must not be before start date")
    values = item.model_dump(mode="json")
    with transaction() as db:
        if item.linked_fixed_loan_id:
            loan = db.execute("SELECT 1 FROM credit_facilities WHERE id=? AND active=1 AND facility_type='fixed_loan'", (item.linked_fixed_loan_id,)).fetchone()
            if not loan or item.transaction_type != "bill": raise HTTPException(422, "Only a recurring bill can link to an active fixed loan")
            if db.execute("SELECT 1 FROM recurring_rules WHERE linked_fixed_loan_id=? AND active=1", (item.linked_fixed_loan_id,)).fetchone(): raise HTTPException(422, "That fixed loan is already linked to another recurring bill")
        cursor = db.execute("""INSERT INTO recurring_rules(transaction_type,amount_minor,currency,description,category,frequency,interval_count,start_date,next_due_date,end_date,active,automated_externally,note,linked_fixed_loan_id)
          VALUES (:transaction_type,:amount_minor,:currency,:description,:category,:frequency,:interval_count,:start_date,:next_due_date,:end_date,:active,:automated_externally,:note,:linked_fixed_loan_id)""", values)
        return row_dict(db.execute("SELECT * FROM recurring_rules WHERE id=?", (cursor.lastrowid,)).fetchone())


@app.put("/api/recurring/{rule_id}")
def update_recurring(rule_id: int, item: RecurringIn):
    if item.end_date and item.end_date < item.start_date: raise HTTPException(422, "End date must not be before start date")
    values = item.model_dump(mode="json") | {"id": rule_id}
    with transaction() as db:
        if not db.execute("SELECT 1 FROM recurring_rules WHERE id=?", (rule_id,)).fetchone(): raise HTTPException(404, "Recurring item not found")
        if item.linked_fixed_loan_id:
            loan = db.execute("SELECT 1 FROM credit_facilities WHERE id=? AND active=1 AND facility_type='fixed_loan'", (item.linked_fixed_loan_id,)).fetchone()
            if not loan or item.transaction_type != "bill": raise HTTPException(422, "Only a recurring bill can link to an active fixed loan")
            if db.execute("SELECT 1 FROM recurring_rules WHERE linked_fixed_loan_id=? AND active=1 AND id<>?", (item.linked_fixed_loan_id, rule_id)).fetchone(): raise HTTPException(422, "That fixed loan is already linked to another recurring bill")
        db.execute("""UPDATE recurring_rules SET transaction_type=:transaction_type,amount_minor=:amount_minor,currency=:currency,description=:description,
          category=:category,frequency=:frequency,interval_count=:interval_count,start_date=:start_date,next_due_date=:next_due_date,end_date=:end_date,active=:active,
          automated_externally=:automated_externally,note=:note,linked_fixed_loan_id=:linked_fixed_loan_id,updated_at=CURRENT_TIMESTAMP WHERE id=:id""", values)
        return row_dict(db.execute("SELECT * FROM recurring_rules WHERE id=?", (rule_id,)).fetchone())


@app.delete("/api/recurring/{rule_id}", status_code=204)
def deactivate_recurring(rule_id: int):
    with transaction() as db:
        changed = db.execute("UPDATE recurring_rules SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?", (rule_id,)).rowcount
        if not changed: raise HTTPException(404, "Recurring item not found")
    return Response(status_code=204)


def occurrence_rows(window_start: date, window_end: date):
    results = []
    with connect() as db:
        rules = db.execute("SELECT * FROM recurring_rules WHERE active=1 AND next_due_date<=? AND (end_date IS NULL OR end_date>=?)", (window_end.isoformat(), window_start.isoformat())).fetchall()
        states = {(row["recurring_rule_id"], row["due_date"]): row_dict(row) for row in db.execute("SELECT * FROM recurring_occurrences WHERE due_date BETWEEN ? AND ?", (window_start.isoformat(), window_end.isoformat()))}
    today = date.today()
    for rule in rules:
        start = date.fromisoformat(rule["start_date"])
        effective_window_start = max(window_start, date.fromisoformat(rule["next_due_date"]))
        end = date.fromisoformat(rule["end_date"]) if rule["end_date"] else None
        for due in projected_dates(start, rule["frequency"], effective_window_start, window_end, end, rule["interval_count"]):
            recorded = states.get((rule["id"], due.isoformat()))
            state = recorded["state"] if recorded else ("due" if due <= today else "upcoming")
            results.append({"rule_id": rule["id"], "due_date": due.isoformat(), "state": state, "occurrence_id": recorded["id"] if recorded else None,
                "transaction_id": recorded["transaction_id"] if recorded else None, "transaction_type": rule["transaction_type"], "amount_minor": rule["amount_minor"],
                "currency": rule["currency"], "description": rule["description"], "category": rule["category"], "automated_externally": bool(rule["automated_externally"])})
    return sorted(results, key=lambda item: (item["due_date"], item["rule_id"]))


@app.get("/api/upcoming")
def upcoming(days: int = Query(30, ge=1, le=366), from_date: date | None = None):
    start = from_date or date.today()
    return occurrence_rows(start, start + timedelta(days=days))


@app.get("/api/calendar/{month}")
def calendar_projection(month: str):
    try:
        year, month_number = map(int, month.split("-"))
        start = date(year, month_number, 1)
        end = date(year, month_number, monthrange(year, month_number)[1])
    except (ValueError, TypeError):
        raise HTTPException(422, "Month must use YYYY-MM")
    return {"month": month, "items": occurrence_rows(start, end)}


@app.post("/api/occurrences/{rule_id}/{due_date}/record", status_code=201)
def record_occurrence(rule_id: int, due_date: date, payload: RecordOccurrenceIn):
    with transaction() as db:
        rule = db.execute("SELECT * FROM recurring_rules WHERE id=?", (rule_id,)).fetchone()
        if not rule or not rule["active"]: raise HTTPException(404, "Active recurring item not found")
        existing = db.execute("SELECT * FROM recurring_occurrences WHERE recurring_rule_id=? AND due_date=?", (rule_id, due_date.isoformat())).fetchone()
        if existing and existing["state"] == "recorded": return row_dict(db.execute("SELECT * FROM transactions WHERE id=?", (existing["transaction_id"],)).fetchone())
        amount = payload.amount_minor or rule["amount_minor"]
        if existing: db.execute("UPDATE recurring_occurrences SET state='recorded' WHERE id=?", (existing["id"],)); occurrence_id = existing["id"]
        else: occurrence_id = db.execute("INSERT INTO recurring_occurrences(recurring_rule_id,due_date,state) VALUES (?,?,'recorded')", (rule_id, due_date.isoformat())).lastrowid
        payment_date = payload.transaction_date or due_date
        role = "loan_payment" if rule["linked_fixed_loan_id"] else "ordinary"
        cursor = db.execute("""INSERT INTO transactions(transaction_type,amount_minor,currency,description,category,transaction_date,note,recurring_occurrence_id,payment_method,credit_facility_id,transaction_role)
          VALUES (?,?,?,?,?,?,?,?,'debit',?,?)""", (rule["transaction_type"], amount, rule["currency"], rule["description"], rule["category"], payment_date.isoformat(), rule["note"], occurrence_id, rule["linked_fixed_loan_id"], role))
        if rule["linked_fixed_loan_id"]:
            loan = db.execute("SELECT * FROM credit_facilities WHERE id=? AND active=1", (rule["linked_fixed_loan_id"],)).fetchone()
            if not loan: raise HTTPException(422, "The linked fixed loan is no longer active")
            apply_loan_payment(db, loan, amount, payment_date, cursor.lastrowid, rule["note"])
        db.execute("UPDATE recurring_occurrences SET transaction_id=? WHERE id=?", (cursor.lastrowid, occurrence_id))
        return row_dict(db.execute("SELECT * FROM transactions WHERE id=?", (cursor.lastrowid,)).fetchone())


@app.post("/api/occurrences/{rule_id}/{due_date}/skip")
def skip_occurrence(rule_id: int, due_date: date):
    with transaction() as db:
        if not db.execute("SELECT 1 FROM recurring_rules WHERE id=?", (rule_id,)).fetchone(): raise HTTPException(404, "Recurring item not found")
        db.execute("INSERT INTO recurring_occurrences(recurring_rule_id,due_date,state) VALUES (?,?,'skipped') ON CONFLICT(recurring_rule_id,due_date) DO UPDATE SET state='skipped',transaction_id=NULL", (rule_id, due_date.isoformat()))
    return {"state": "skipped"}


@app.get("/api/summary/{month}")
def summary(month: str):
    try:
        year, month_number = map(int, month.split("-")); start = date(year, month_number, 1); end = date(year, month_number, monthrange(year, month_number)[1])
    except (ValueError, TypeError): raise HTTPException(422, "Month must use YYYY-MM")
    totals = {"actual_income": 0, "actual_expenses": 0, "actual_bills": 0, "actual_savings": 0, "actual_credit_payments": 0, "actual_loan_payments": 0, "actual_cash_expenses": 0, "actual_credit_funded_expenses": 0}
    mapping = {"income": "actual_income", "expense": "actual_expenses", "bill": "actual_bills", "savings": "actual_savings"}
    with connect() as db:
        credit_payments = db.execute("SELECT COALESCE(SUM(amount_minor),0) FROM transactions WHERE transaction_date BETWEEN ? AND ? AND transaction_role='credit_payment'", (start.isoformat(), end.isoformat())).fetchone()[0]
        totals["actual_credit_payments"] = credit_payments
        totals["actual_loan_payments"] = db.execute("SELECT COALESCE(SUM(amount_minor),0) FROM transactions WHERE transaction_date BETWEEN ? AND ? AND transaction_role='loan_payment'", (start.isoformat(), end.isoformat())).fetchone()[0]
        totals["actual_cash_expenses"] = db.execute("SELECT COALESCE(SUM(amount_minor),0) FROM transactions WHERE transaction_date BETWEEN ? AND ? AND transaction_role='ordinary' AND transaction_type='expense' AND (payment_method IS NULL OR payment_method IN ('cash','debit'))", (start.isoformat(), end.isoformat())).fetchone()[0]
        totals["actual_credit_funded_expenses"] = db.execute("SELECT COALESCE(SUM(amount_minor),0) FROM transactions WHERE transaction_date BETWEEN ? AND ? AND transaction_role='ordinary' AND transaction_type='expense' AND payment_method IN ('credit','pay_later')", (start.isoformat(), end.isoformat())).fetchone()[0]
        for row in db.execute("SELECT transaction_type,SUM(amount_minor) total FROM transactions WHERE transaction_date BETWEEN ? AND ? AND transaction_role='ordinary' GROUP BY transaction_type", (start.isoformat(), end.isoformat())):
            totals[mapping[row["transaction_type"]]] = row["total"]
    remaining = {"income": 0, "bill": 0, "savings": 0}
    for item in occurrence_rows(start, end):
        if item["state"] in {"upcoming", "due"}: remaining[item["transaction_type"]] += item["amount_minor"]
    expected = totals["actual_income"] + remaining["income"] - totals["actual_cash_expenses"] - totals["actual_bills"] - totals["actual_credit_payments"] - totals["actual_loan_payments"] - remaining["bill"] - totals["actual_savings"] - remaining["savings"]
    return {"month": month, **totals, "expected_income": remaining["income"], "bills_remaining": remaining["bill"], "planned_savings_remaining": remaining["savings"], "expected_remaining": expected}


@app.post("/api/parse")
def parse_voice(payload: VoiceIn): return parse_transaction_text(payload.text)


def backup_data():
    tables = ["transactions", "recurring_rules", "recurring_occurrences", "categories", "credit_facilities", "loan_balance_events"]
    with connect() as db:
        data = {table if table != "recurring_occurrences" else "occurrences": [row_dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY id")] for table in tables}
    return {"version": 1, "exported_at": datetime.now().astimezone().isoformat(), "currency": "AUD", **data}


@app.get("/api/export.json")
def export_json():
    return Response(json.dumps(backup_data(), indent=2), media_type="application/json", headers={"Content-Disposition": "attachment; filename=olos-mini-budget-backup.json"})


@app.get("/api/export.csv")
def export_csv():
    output = io.StringIO(); fields = ["id","transaction_type","transaction_role","amount_minor","currency","description","category","transaction_date","payment_method","credit_facility_id","note","recurring_occurrence_id","created_at","updated_at"]
    writer = csv.DictWriter(output, fieldnames=fields); writer.writeheader()
    with connect() as db:
        for row in db.execute("SELECT * FROM transactions ORDER BY transaction_date,id"): writer.writerow({key: row[key] for key in fields})
    return Response(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=olos-mini-budget-transactions.csv"})


@app.post("/api/restore")
def restore_backup(payload: BackupIn, confirm: bool = False):
    if not confirm: raise HTTPException(400, "Restore requires confirm=true and replaces current records")
    if payload.version != 1: raise HTTPException(422, "Unsupported backup version")
    raw = json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode(); digest = hashlib.sha256(raw).hexdigest()
    with transaction() as db:
        if db.execute("SELECT 1 FROM import_history WHERE digest=?", (digest,)).fetchone(): raise HTTPException(409, "This backup has already been restored")
        db.execute("DELETE FROM loan_balance_events"); db.execute("DELETE FROM transactions"); db.execute("DELETE FROM recurring_occurrences"); db.execute("DELETE FROM recurring_rules"); db.execute("DELETE FROM categories"); db.execute("DELETE FROM credit_facilities")
        allowed = {
          "categories": ["id","name","transaction_type"], "recurring_rules": ["id","transaction_type","amount_minor","currency","description","category","frequency","interval_count","start_date","next_due_date","end_date","active","automated_externally","note","linked_fixed_loan_id","created_at","updated_at"],
          "occurrences": ["id","recurring_rule_id","due_date","state","transaction_id","created_at"],
          "credit_facilities": ["id","name","facility_type","credit_limit_minor","amount_owed_minor","annual_rate_basis_points","balance_as_of_date","currency","note","active","created_at","updated_at"],
          "transactions": ["id","transaction_type","amount_minor","currency","description","category","transaction_date","note","recurring_occurrence_id","created_at","updated_at","payment_method","credit_facility_id","transaction_role"],
          "loan_balance_events": ["id","facility_id","event_type","event_date","balance_before_minor","interest_minor","amount_minor","balance_after_minor","transaction_id","note","created_at"]}
        for name in ("categories","credit_facilities","recurring_rules","occurrences","transactions","loan_balance_events"):
            table = "recurring_occurrences" if name == "occurrences" else name
            for record in getattr(payload, name):
                columns = [column for column in allowed[name] if column in record]
                db.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [record[column] for column in columns])
        for kind, names in DEFAULT_CATEGORIES.items():
            db.executemany("INSERT OR IGNORE INTO categories(name,transaction_type) VALUES (?,?)", [(name, kind) for name in names])
        db.execute("INSERT INTO import_history(digest) VALUES (?)", (digest,))
    return {"restored": True, "transactions": len(payload.transactions), "recurring_rules": len(payload.recurring_rules), "credit_facilities": len(payload.credit_facilities)}


@app.post("/api/reset")
def reset_all_data(payload: ResetDataIn):
    # The Literal schema makes this destructive operation impossible without the
    # deliberate, exact RESET phrase. Browser tests use an isolated data folder.
    with transaction() as db:
        db.execute("DELETE FROM loan_balance_events")
        db.execute("DELETE FROM transactions")
        db.execute("DELETE FROM recurring_occurrences")
        db.execute("DELETE FROM recurring_rules")
        db.execute("DELETE FROM credit_facilities")
        db.execute("DELETE FROM idempotency_keys")
        db.execute("DELETE FROM import_history")
        db.execute("DELETE FROM categories")
        if demo_mode_enabled():
            db.execute("DELETE FROM demo_state")
        for kind, names in DEFAULT_CATEGORIES.items():
            db.executemany("INSERT INTO categories(name,transaction_type) VALUES (?,?)", [(name, kind) for name in names])
    seeded = seed_demo_data() if demo_mode_enabled() else False
    return {"reset": True, "demo_reseeded": seeded}


if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
    @app.get("/")
    def index(): return FileResponse(STATIC_DIR / "index.html")
    @app.get("/manifest.webmanifest")
    def manifest(): return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")
    @app.get("/sw.js")
    def service_worker(): return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")
