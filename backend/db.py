from __future__ import annotations

import os
import sqlite3
from contextvars import ContextVar, Token
from contextlib import contextmanager
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("OLOS_HACKATHON_DATA_DIR", APP_ROOT / ".hackathon-runtime" / "data"))
DB_PATH = DATA_DIR / "olos-mini-budget-hackathon.sqlite3"
_REQUEST_DB_PATH: ContextVar[Path | None] = ContextVar("olos_hackathon_request_db_path", default=None)

DEFAULT_CATEGORIES = {
    "expense": ["Groceries", "Dining", "Fuel", "Transport", "Housing", "Utilities", "Phone/Internet", "Subscriptions", "Health", "Shopping", "Entertainment", "Travel", "Work", "Business", "Olos-AI", "Other"],
    "bill": ["Housing", "Utilities", "Phone/Internet", "Subscriptions", "Health", "Other"],
    "income": ["Salary", "Other Income"],
    "savings": ["Savings"],
}


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def current_db_path() -> Path:
    return _REQUEST_DB_PATH.get() or DB_PATH


def set_request_db_path(path: Path) -> Token:
    return _REQUEST_DB_PATH.set(path)


def reset_request_db_path(token: Token) -> None:
    _REQUEST_DB_PATH.reset(token)


def connect() -> sqlite3.Connection:
    path = current_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def transaction():
    db = connect()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    with transaction() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS categories (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, transaction_type TEXT NOT NULL,
          UNIQUE(name, transaction_type)
        );
        CREATE TABLE IF NOT EXISTS recurring_rules (
          id INTEGER PRIMARY KEY, transaction_type TEXT NOT NULL CHECK(transaction_type IN ('income','bill','savings')),
          amount_minor INTEGER NOT NULL CHECK(amount_minor > 0), currency TEXT NOT NULL DEFAULT 'AUD',
          description TEXT NOT NULL, category TEXT NOT NULL, frequency TEXT NOT NULL
          CHECK(frequency IN ('weekly','fortnightly','monthly','yearly')),
          interval_count INTEGER NOT NULL DEFAULT 1 CHECK(interval_count BETWEEN 1 AND 120),
          start_date TEXT NOT NULL, next_due_date TEXT NOT NULL, end_date TEXT,
          active INTEGER NOT NULL DEFAULT 1, automated_externally INTEGER NOT NULL DEFAULT 0,
          note TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS recurring_occurrences (
          id INTEGER PRIMARY KEY, recurring_rule_id INTEGER NOT NULL REFERENCES recurring_rules(id),
          due_date TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('recorded','skipped')),
          transaction_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(recurring_rule_id, due_date)
        );
        CREATE TABLE IF NOT EXISTS credit_facilities (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL,
          facility_type TEXT NOT NULL CHECK(facility_type IN ('credit','pay_later','fixed_loan')),
          credit_limit_minor INTEGER,
          amount_owed_minor INTEGER NOT NULL DEFAULT 0 CHECK(amount_owed_minor >= 0),
          annual_rate_basis_points INTEGER, balance_as_of_date TEXT,
          currency TEXT NOT NULL DEFAULT 'AUD', note TEXT,
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK((facility_type='fixed_loan' AND credit_limit_minor IS NULL) OR (facility_type IN ('credit','pay_later') AND credit_limit_minor > 0))
        );
        CREATE TABLE IF NOT EXISTS transactions (
          id INTEGER PRIMARY KEY, transaction_type TEXT NOT NULL CHECK(transaction_type IN ('expense','income','bill','savings')),
          amount_minor INTEGER NOT NULL CHECK(amount_minor > 0), currency TEXT NOT NULL DEFAULT 'AUD',
          description TEXT NOT NULL, category TEXT NOT NULL, transaction_date TEXT NOT NULL,
          note TEXT, recurring_occurrence_id INTEGER REFERENCES recurring_occurrences(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS idempotency_keys (
          key TEXT PRIMARY KEY, resource_type TEXT NOT NULL, resource_id INTEGER NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS import_history (
          digest TEXT PRIMARY KEY, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);
        CREATE INDEX IF NOT EXISTS idx_rules_due ON recurring_rules(next_due_date, active);
        """)
        facility_sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='credit_facilities'").fetchone()[0]
        if "fixed_loan" not in facility_sql:
            db.commit()
            db.execute("PRAGMA foreign_keys=OFF")
            db.executescript("""
            BEGIN;
            CREATE TABLE credit_facilities_new (
              id INTEGER PRIMARY KEY, name TEXT NOT NULL,
              facility_type TEXT NOT NULL CHECK(facility_type IN ('credit','pay_later','fixed_loan')),
              credit_limit_minor INTEGER, amount_owed_minor INTEGER NOT NULL DEFAULT 0 CHECK(amount_owed_minor >= 0),
              annual_rate_basis_points INTEGER, balance_as_of_date TEXT,
              currency TEXT NOT NULL DEFAULT 'AUD', note TEXT, active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              CHECK((facility_type='fixed_loan' AND credit_limit_minor IS NULL) OR (facility_type IN ('credit','pay_later') AND credit_limit_minor > 0))
            );
            INSERT INTO credit_facilities_new(id,name,facility_type,credit_limit_minor,amount_owed_minor,currency,note,active,created_at,updated_at)
              SELECT id,name,facility_type,credit_limit_minor,amount_owed_minor,currency,note,active,created_at,updated_at FROM credit_facilities;
            DROP TABLE credit_facilities;
            ALTER TABLE credit_facilities_new RENAME TO credit_facilities;
            COMMIT;
            """)
            db.execute("PRAGMA foreign_keys=ON")
        transaction_columns = {row["name"] for row in db.execute("PRAGMA table_info(transactions)")}
        if "payment_method" not in transaction_columns:
            db.execute("ALTER TABLE transactions ADD COLUMN payment_method TEXT")
        if "credit_facility_id" not in transaction_columns:
            db.execute("ALTER TABLE transactions ADD COLUMN credit_facility_id INTEGER REFERENCES credit_facilities(id)")
        if "transaction_role" not in transaction_columns:
            db.execute("ALTER TABLE transactions ADD COLUMN transaction_role TEXT NOT NULL DEFAULT 'ordinary'")
        recurring_columns = {row["name"] for row in db.execute("PRAGMA table_info(recurring_rules)")}
        if "interval_count" not in recurring_columns:
            db.execute("ALTER TABLE recurring_rules ADD COLUMN interval_count INTEGER NOT NULL DEFAULT 1 CHECK(interval_count BETWEEN 1 AND 120)")
        if "linked_fixed_loan_id" not in recurring_columns:
            db.execute("ALTER TABLE recurring_rules ADD COLUMN linked_fixed_loan_id INTEGER REFERENCES credit_facilities(id)")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS loan_balance_events (
          id INTEGER PRIMARY KEY, facility_id INTEGER NOT NULL REFERENCES credit_facilities(id),
          event_type TEXT NOT NULL CHECK(event_type IN ('created','payment','reconciliation','rate_change')),
          event_date TEXT NOT NULL, balance_before_minor INTEGER NOT NULL,
          interest_minor INTEGER NOT NULL DEFAULT 0, amount_minor INTEGER,
          balance_after_minor INTEGER NOT NULL, transaction_id INTEGER REFERENCES transactions(id),
          note TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_loan_events_facility_date ON loan_balance_events(facility_id,event_date,id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_rule_per_loan ON recurring_rules(linked_fixed_loan_id) WHERE linked_fixed_loan_id IS NOT NULL AND active=1;
        """)
        for kind, names in DEFAULT_CATEGORIES.items():
            db.executemany("INSERT OR IGNORE INTO categories(name, transaction_type) VALUES (?, ?)", [(name, kind) for name in names])
