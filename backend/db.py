from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("OLOS_BUDGET_DATA_DIR", APP_ROOT / "data"))
DB_PATH = DATA_DIR / "olos-mini-budget.sqlite3"

DEFAULT_CATEGORIES = {
    "expense": ["Groceries", "Dining", "Fuel", "Transport", "Housing", "Utilities", "Phone/Internet", "Subscriptions", "Health", "Shopping", "Entertainment", "Travel", "Other"],
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


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10, factory=ClosingConnection)
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
        for kind, names in DEFAULT_CATEGORIES.items():
            db.executemany("INSERT OR IGNORE INTO categories(name, transaction_type) VALUES (?, ?)", [(name, kind) for name in names])
