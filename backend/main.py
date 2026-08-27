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

from .db import APP_ROOT, DB_PATH, connect, init_db, transaction
from .domain import parse_transaction_text, projected_dates
from .schemas import BackupIn, CreditFacilityIn, CreditPaymentIn, RecordOccurrenceIn, RecurringIn, TransactionIn, VoiceIn

app = FastAPI(title="Olos-AI Mini Budget Planner", version="1.0.0", docs_url="/api/docs", redoc_url=None)
STATIC_DIR = APP_ROOT / "frontend"


def row_dict(row):
    result = dict(row)
    for key in ("active", "automated_externally"):
        if key in result: result[key] = bool(result[key])
    return result


@app.on_event("startup")
def startup():
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
    return {"status": "ok", "currency": "AUD", "database": str(DB_PATH)}


@app.get("/api/categories")
def categories():
    with connect() as db:
        return [row_dict(row) for row in db.execute("SELECT id,name,transaction_type FROM categories ORDER BY transaction_type,name")]


def facility_dict(row):
    result = row_dict(row)
    result["available_credit_minor"] = result["credit_limit_minor"] - result["amount_owed_minor"]
    return result


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
    facility = db.execute("SELECT * FROM credit_facilities WHERE id=? AND active=1", (facility_id,)).fetchone()
    if not facility:
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
def list_transactions(limit: int = Query(30, ge=1, le=200), month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$")):
    sql, params = "SELECT * FROM transactions", []
    if month: sql += " WHERE substr(transaction_date,1,7)=?"; params.append(month)
    sql += " ORDER BY transaction_date DESC, id DESC LIMIT ?"; params.append(limit)
    with connect() as db: return [row_dict(row) for row in db.execute(sql, params)]


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
        if existing["transaction_role"] == "credit_payment": raise HTTPException(422, "Credit payments cannot be edited; delete and record a new payment")
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
        apply_facility_effect(db, occurrence, -1)
        db.execute("DELETE FROM transactions WHERE id=?", (transaction_id,))
        if occurrence["recurring_occurrence_id"]: db.execute("DELETE FROM recurring_occurrences WHERE id=?", (occurrence["recurring_occurrence_id"],))
    return Response(status_code=204)


@app.get("/api/credit-facilities")
def list_credit_facilities():
    with connect() as db:
        return [facility_dict(row) for row in db.execute("SELECT * FROM credit_facilities WHERE active=1 ORDER BY name,id")]


@app.post("/api/credit-facilities", status_code=201)
def create_credit_facility(item: CreditFacilityIn):
    values = item.model_dump(mode="json")
    with transaction() as db:
        cursor = db.execute("""INSERT INTO credit_facilities(name,facility_type,credit_limit_minor,amount_owed_minor,currency,note)
          VALUES (:name,:facility_type,:credit_limit_minor,:amount_owed_minor,:currency,:note)""", values)
        return facility_dict(db.execute("SELECT * FROM credit_facilities WHERE id=?", (cursor.lastrowid,)).fetchone())


@app.put("/api/credit-facilities/{facility_id}")
def update_credit_facility(facility_id: int, item: CreditFacilityIn):
    values = item.model_dump(mode="json") | {"id": facility_id}
    with transaction() as db:
        if not db.execute("SELECT 1 FROM credit_facilities WHERE id=? AND active=1", (facility_id,)).fetchone():
            raise HTTPException(404, "Credit facility not found")
        db.execute("""UPDATE credit_facilities SET name=:name,facility_type=:facility_type,credit_limit_minor=:credit_limit_minor,
          amount_owed_minor=:amount_owed_minor,currency=:currency,note=:note,updated_at=CURRENT_TIMESTAMP WHERE id=:id""", values)
        return facility_dict(db.execute("SELECT * FROM credit_facilities WHERE id=?", (facility_id,)).fetchone())


@app.delete("/api/credit-facilities/{facility_id}", status_code=204)
def deactivate_credit_facility(facility_id: int):
    with transaction() as db:
        facility = db.execute("SELECT * FROM credit_facilities WHERE id=? AND active=1", (facility_id,)).fetchone()
        if not facility: raise HTTPException(404, "Credit facility not found")
        if facility["amount_owed_minor"]:
            raise HTTPException(409, "Record the remaining balance before removing this account")
        if db.execute("SELECT 1 FROM transactions WHERE credit_facility_id=? LIMIT 1", (facility_id,)).fetchone():
            raise HTTPException(409, "An account with transaction history cannot be removed")
        db.execute("UPDATE credit_facilities SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?", (facility_id,))
    return Response(status_code=204)


@app.post("/api/credit-facilities/{facility_id}/payments", status_code=201)
def record_credit_payment(facility_id: int, item: CreditPaymentIn):
    with transaction() as db:
        facility = db.execute("SELECT * FROM credit_facilities WHERE id=? AND active=1", (facility_id,)).fetchone()
        if not facility: raise HTTPException(404, "Credit facility not found")
        record = {"transaction_type": "bill", "amount_minor": item.amount_minor, "credit_facility_id": facility_id,
                  "transaction_role": "credit_payment", "payment_method": "debit"}
        apply_facility_effect(db, record, 1)
        cursor = db.execute("""INSERT INTO transactions(transaction_type,amount_minor,currency,description,category,transaction_date,note,payment_method,credit_facility_id,transaction_role)
          VALUES ('bill',?,?,?,?,?,?, 'debit',?,'credit_payment')""",
          (item.amount_minor, facility["currency"], f"{facility['name']} payment", "Credit repayment", item.transaction_date.isoformat(), item.note, facility_id))
        return row_dict(db.execute("SELECT * FROM transactions WHERE id=?", (cursor.lastrowid,)).fetchone())


@app.get("/api/recurring")
def list_recurring():
    with connect() as db: return [row_dict(row) for row in db.execute("SELECT * FROM recurring_rules ORDER BY active DESC,next_due_date,id")]


@app.post("/api/recurring", status_code=201)
def create_recurring(item: RecurringIn):
    if item.end_date and item.end_date < item.start_date: raise HTTPException(422, "End date must not be before start date")
    values = item.model_dump(mode="json")
    with transaction() as db:
        cursor = db.execute("""INSERT INTO recurring_rules(transaction_type,amount_minor,currency,description,category,frequency,start_date,next_due_date,end_date,active,automated_externally,note)
          VALUES (:transaction_type,:amount_minor,:currency,:description,:category,:frequency,:start_date,:next_due_date,:end_date,:active,:automated_externally,:note)""", values)
        return row_dict(db.execute("SELECT * FROM recurring_rules WHERE id=?", (cursor.lastrowid,)).fetchone())


@app.put("/api/recurring/{rule_id}")
def update_recurring(rule_id: int, item: RecurringIn):
    if item.end_date and item.end_date < item.start_date: raise HTTPException(422, "End date must not be before start date")
    values = item.model_dump(mode="json") | {"id": rule_id}
    with transaction() as db:
        if not db.execute("SELECT 1 FROM recurring_rules WHERE id=?", (rule_id,)).fetchone(): raise HTTPException(404, "Recurring item not found")
        db.execute("""UPDATE recurring_rules SET transaction_type=:transaction_type,amount_minor=:amount_minor,currency=:currency,description=:description,
          category=:category,frequency=:frequency,start_date=:start_date,next_due_date=:next_due_date,end_date=:end_date,active=:active,
          automated_externally=:automated_externally,note=:note,updated_at=CURRENT_TIMESTAMP WHERE id=:id""", values)
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
        for due in projected_dates(start, rule["frequency"], effective_window_start, window_end, end):
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
        cursor = db.execute("""INSERT INTO transactions(transaction_type,amount_minor,currency,description,category,transaction_date,note,recurring_occurrence_id)
          VALUES (?,?,?,?,?,?,?,?)""", (rule["transaction_type"], amount, rule["currency"], rule["description"], rule["category"], (payload.transaction_date or due_date).isoformat(), rule["note"], occurrence_id))
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
    totals = {"actual_income": 0, "actual_expenses": 0, "actual_bills": 0, "actual_savings": 0, "actual_credit_payments": 0, "actual_cash_expenses": 0, "actual_credit_funded_expenses": 0}
    mapping = {"income": "actual_income", "expense": "actual_expenses", "bill": "actual_bills", "savings": "actual_savings"}
    with connect() as db:
        credit_payments = db.execute("SELECT COALESCE(SUM(amount_minor),0) FROM transactions WHERE transaction_date BETWEEN ? AND ? AND transaction_role='credit_payment'", (start.isoformat(), end.isoformat())).fetchone()[0]
        totals["actual_credit_payments"] = credit_payments
        totals["actual_cash_expenses"] = db.execute("SELECT COALESCE(SUM(amount_minor),0) FROM transactions WHERE transaction_date BETWEEN ? AND ? AND transaction_role='ordinary' AND transaction_type='expense' AND (payment_method IS NULL OR payment_method IN ('cash','debit'))", (start.isoformat(), end.isoformat())).fetchone()[0]
        totals["actual_credit_funded_expenses"] = db.execute("SELECT COALESCE(SUM(amount_minor),0) FROM transactions WHERE transaction_date BETWEEN ? AND ? AND transaction_role='ordinary' AND transaction_type='expense' AND payment_method IN ('credit','pay_later')", (start.isoformat(), end.isoformat())).fetchone()[0]
        for row in db.execute("SELECT transaction_type,SUM(amount_minor) total FROM transactions WHERE transaction_date BETWEEN ? AND ? AND transaction_role='ordinary' GROUP BY transaction_type", (start.isoformat(), end.isoformat())):
            totals[mapping[row["transaction_type"]]] = row["total"]
    remaining = {"income": 0, "bill": 0, "savings": 0}
    for item in occurrence_rows(start, end):
        if item["state"] in {"upcoming", "due"}: remaining[item["transaction_type"]] += item["amount_minor"]
    expected = totals["actual_income"] + remaining["income"] - totals["actual_cash_expenses"] - totals["actual_bills"] - totals["actual_credit_payments"] - remaining["bill"] - totals["actual_savings"] - remaining["savings"]
    return {"month": month, **totals, "expected_income": remaining["income"], "bills_remaining": remaining["bill"], "planned_savings_remaining": remaining["savings"], "expected_remaining": expected}


@app.post("/api/parse")
def parse_voice(payload: VoiceIn): return parse_transaction_text(payload.text)


def backup_data():
    tables = ["transactions", "recurring_rules", "recurring_occurrences", "categories", "credit_facilities"]
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
        db.execute("DELETE FROM transactions"); db.execute("DELETE FROM recurring_occurrences"); db.execute("DELETE FROM recurring_rules"); db.execute("DELETE FROM categories"); db.execute("DELETE FROM credit_facilities")
        allowed = {
          "categories": ["id","name","transaction_type"], "recurring_rules": ["id","transaction_type","amount_minor","currency","description","category","frequency","start_date","next_due_date","end_date","active","automated_externally","note","created_at","updated_at"],
          "occurrences": ["id","recurring_rule_id","due_date","state","transaction_id","created_at"],
          "credit_facilities": ["id","name","facility_type","credit_limit_minor","amount_owed_minor","currency","note","active","created_at","updated_at"],
          "transactions": ["id","transaction_type","amount_minor","currency","description","category","transaction_date","note","recurring_occurrence_id","created_at","updated_at","payment_method","credit_facility_id","transaction_role"]}
        for name in ("categories","recurring_rules","credit_facilities","occurrences","transactions"):
            table = "recurring_occurrences" if name == "occurrences" else name
            for record in getattr(payload, name):
                columns = [column for column in allowed[name] if column in record]
                db.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [record[column] for column in columns])
        db.execute("INSERT INTO import_history(digest) VALUES (?)", (digest,))
    return {"restored": True, "transactions": len(payload.transactions), "recurring_rules": len(payload.recurring_rules), "credit_facilities": len(payload.credit_facilities)}


if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
    @app.get("/")
    def index(): return FileResponse(STATIC_DIR / "index.html")
    @app.get("/manifest.webmanifest")
    def manifest(): return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")
    @app.get("/sw.js")
    def service_worker(): return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")
