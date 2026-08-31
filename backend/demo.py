from __future__ import annotations

import os
import tempfile
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

from .db import transaction

DEMO_COOKIE = "olos_demo_session"


def demo_mode_enabled() -> bool:
    return os.environ.get("OLOS_DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


def demo_data_dir() -> Path:
    configured = os.environ.get("OLOS_DEMO_DATA_DIR")
    return Path(configured) if configured else Path(tempfile.gettempdir()) / "olos-budget-demo"


def valid_session_id(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return None


def new_session_id() -> str:
    return str(uuid.uuid4())


def session_db_path(session_id: str) -> Path:
    # UUID normalization above makes path traversal impossible.
    return demo_data_dir() / f"{session_id}.sqlite3"


def cleanup_stale_sessions(max_age_hours: int = 24) -> None:
    """Remove only expired disposable DB files from the configured demo directory."""
    directory = demo_data_dir()
    if not directory.exists():
        return
    cutoff = time.time() - max_age_hours * 3600
    for path in directory.glob("*.sqlite3"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except FileNotFoundError:
            pass


def seed_demo_data() -> bool:
    """Seed one small synthetic story once for the current demo-session DB."""
    today = date.today()
    with transaction() as db:
        db.execute("CREATE TABLE IF NOT EXISTS demo_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        claimed = db.execute("INSERT OR IGNORE INTO demo_state(key,value) VALUES ('seeded','1')").rowcount
        if not claimed:
            return False
        db.executemany(
            """INSERT INTO transactions(transaction_type,amount_minor,currency,description,category,transaction_date,note,payment_method,transaction_role)
               VALUES (?,?,?,?,?,?,?,?, 'ordinary')""",
            [
                ("income", 320000, "AUD", "Demo salary", "Salary", today.isoformat(), "Synthetic hackathon data", None),
                ("expense", 8640, "AUD", "Demo groceries", "Groceries", today.isoformat(), "Synthetic hackathon data", "debit"),
                ("savings", 40000, "AUD", "Demo savings transfer", "Savings", today.isoformat(), "Synthetic hackathon data", None),
            ],
        )
        db.execute(
            """INSERT INTO credit_facilities(name,facility_type,credit_limit_minor,amount_owed_minor,annual_rate_basis_points,currency,note)
               VALUES ('Demo everyday card','credit',500000,72500,1999,'AUD','Synthetic hackathon account')"""
        )
        loan_id = db.execute(
            """INSERT INTO credit_facilities(name,facility_type,credit_limit_minor,amount_owed_minor,annual_rate_basis_points,balance_as_of_date,currency,note)
               VALUES ('Demo car loan','fixed_loan',NULL,1250000,825,?,'AUD','Estimated synthetic balance')""",
            (today.isoformat(),),
        ).lastrowid
        db.execute(
            """INSERT INTO loan_balance_events(facility_id,event_type,event_date,balance_before_minor,balance_after_minor,note)
               VALUES (?,'created',?,0,1250000,'Synthetic hackathon balance')""",
            (loan_id, today.isoformat()),
        )
        recurring = [
            ("income", 320000, "Demo payday", "Salary", "fortnightly", 1, today + timedelta(days=7), None),
            ("bill", 14280, "Demo electricity", "Utilities", "monthly", 1, today + timedelta(days=4), None),
            ("savings", 40000, "Demo savings plan", "Savings", "monthly", 1, today + timedelta(days=10), None),
            ("bill", 27500, "Demo car repayment", "Loan repayment", "monthly", 1, today + timedelta(days=12), loan_id),
        ]
        for kind, amount, description, category, frequency, interval, due, linked_loan in recurring:
            db.execute(
                """INSERT INTO recurring_rules(transaction_type,amount_minor,currency,description,category,frequency,interval_count,start_date,next_due_date,active,automated_externally,note,linked_fixed_loan_id)
                   VALUES (?,?, 'AUD',?,?,?,?,?,?,1,0,'Synthetic hackathon schedule',?)""",
                (kind, amount, description, category, frequency, interval, due.isoformat(), due.isoformat(), linked_loan),
            )
    return True
