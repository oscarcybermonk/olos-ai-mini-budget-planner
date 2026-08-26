from __future__ import annotations

from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

TransactionType = Literal["expense", "income", "bill", "savings"]
RecurringType = Literal["income", "bill", "savings"]
Frequency = Literal["weekly", "fortnightly", "monthly", "yearly"]


class TransactionIn(BaseModel):
    transaction_type: TransactionType
    amount_minor: int = Field(gt=0, le=1_000_000_000_00)
    currency: str = Field(default="AUD", pattern=r"^[A-Z]{3}$")
    description: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=80)
    transaction_date: date
    note: str | None = Field(default=None, max_length=1000)
    @field_validator("description", "category")
    @classmethod
    def strip_required(cls, value: str) -> str:
        if not value.strip(): raise ValueError("must not be blank")
        return value.strip()


class RecurringIn(BaseModel):
    transaction_type: RecurringType
    amount_minor: int = Field(gt=0, le=1_000_000_000_00)
    currency: str = Field(default="AUD", pattern=r"^[A-Z]{3}$")
    description: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=80)
    frequency: Frequency
    start_date: date
    next_due_date: date
    end_date: date | None = None
    active: bool = True
    automated_externally: bool = False
    note: str | None = Field(default=None, max_length=1000)
    @field_validator("description", "category")
    @classmethod
    def strip_recurring(cls, value: str) -> str:
        if not value.strip(): raise ValueError("must not be blank")
        return value.strip()


class VoiceIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class RecordOccurrenceIn(BaseModel):
    amount_minor: int | None = Field(default=None, gt=0)
    transaction_date: date | None = None


class BackupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    exported_at: str | None = None
    currency: str = "AUD"
    transactions: list[dict]
    recurring_rules: list[dict]
    occurrences: list[dict] = []
    categories: list[dict] = []
