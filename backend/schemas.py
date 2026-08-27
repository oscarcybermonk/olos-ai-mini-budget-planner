from __future__ import annotations

from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TransactionType = Literal["expense", "income", "bill", "savings"]
RecurringType = Literal["income", "bill", "savings"]
Frequency = Literal["weekly", "fortnightly", "monthly", "yearly"]
PaymentMethod = Literal["cash", "debit", "credit", "pay_later"]


class TransactionIn(BaseModel):
    transaction_type: TransactionType
    amount_minor: int = Field(gt=0, le=1_000_000_000_00)
    currency: str = Field(default="AUD", pattern=r"^[A-Z]{3}$")
    description: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=80)
    transaction_date: date
    note: str | None = Field(default=None, max_length=1000)
    payment_method: PaymentMethod | None = None
    credit_facility_id: int | None = Field(default=None, gt=0)
    @field_validator("description", "category")
    @classmethod
    def strip_required(cls, value: str) -> str:
        if not value.strip(): raise ValueError("must not be blank")
        return value.strip()
    @model_validator(mode="after")
    def validate_payment_method(self):
        if self.transaction_type == "expense":
            self.payment_method = self.payment_method or "debit"
            if self.payment_method in {"credit", "pay_later"} and not self.credit_facility_id:
                raise ValueError("Choose the credit or pay-later account used")
            if self.payment_method in {"cash", "debit"} and self.credit_facility_id:
                raise ValueError("Cash and debit expenses cannot use a credit facility")
        elif self.payment_method or self.credit_facility_id:
            raise ValueError("Payment method is only available for expenses")
        return self


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


class CreditFacilityIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    facility_type: Literal["credit", "pay_later"]
    credit_limit_minor: int = Field(gt=0, le=1_000_000_000_00)
    amount_owed_minor: int = Field(default=0, ge=0, le=1_000_000_000_00)
    currency: str = Field(default="AUD", pattern=r"^[A-Z]{3}$")
    note: str | None = Field(default=None, max_length=1000)
    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        if not value.strip(): raise ValueError("must not be blank")
        return value.strip()
    @model_validator(mode="after")
    def validate_balance(self):
        if self.amount_owed_minor > self.credit_limit_minor:
            raise ValueError("Amount owed cannot exceed the credit limit")
        return self


class CreditPaymentIn(BaseModel):
    amount_minor: int = Field(gt=0, le=1_000_000_000_00)
    transaction_date: date
    note: str | None = Field(default=None, max_length=1000)


class BackupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    exported_at: str | None = None
    currency: str = "AUD"
    transactions: list[dict]
    recurring_rules: list[dict]
    occurrences: list[dict] = []
    categories: list[dict] = []
    credit_facilities: list[dict] = []
