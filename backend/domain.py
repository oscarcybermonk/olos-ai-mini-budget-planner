from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable


def money_to_minor(value: str | Decimal) -> int:
    try:
        amount = Decimal(str(value).replace("$", "").replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError("Enter a valid amount") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Amount must be greater than zero")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def advance_day(day: date, frequency: str) -> date:
    if frequency == "weekly":
        return day + timedelta(days=7)
    if frequency == "fortnightly":
        return day + timedelta(days=14)
    months = 12 if frequency == "yearly" else 1
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def occurrence_at(anchor: date, frequency: str, index: int) -> date:
    if frequency == "weekly": return anchor + timedelta(days=7 * index)
    if frequency == "fortnightly": return anchor + timedelta(days=14 * index)
    months = (12 if frequency == "yearly" else 1) * index
    month_index = anchor.month - 1 + months
    year, month = anchor.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def projected_dates(start: date, frequency: str, window_start: date, window_end: date, end: date | None = None) -> Iterable[date]:
    guard = 0; current = start
    while current < window_start:
        guard += 1
        if guard > 10000:
            raise ValueError("Recurring schedule is too large")
        current = occurrence_at(start, frequency, guard)
    while current <= window_end and (end is None or current <= end):
        yield current
        guard += 1; current = occurrence_at(start, frequency, guard)


NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000,
}


def words_to_number(words: str) -> Decimal | None:
    tokens = re.findall(r"[a-z]+", words.lower())
    if not tokens or any(token not in NUMBER_WORDS and token not in {"dollar", "dollars", "and", "cent", "cents"} for token in tokens):
        return None
    dollars_tokens, cents_tokens = tokens, []
    if "dollar" in tokens or "dollars" in tokens:
        marker = tokens.index("dollar") if "dollar" in tokens else tokens.index("dollars")
        dollars_tokens, cents_tokens = tokens[:marker], tokens[marker + 1:]
    def total(parts: list[str]) -> int:
        subtotal = result = 0
        for token in parts:
            if token in {"and", "cent", "cents"}: continue
            value = NUMBER_WORDS[token]
            if value == 100: subtotal = max(1, subtotal) * value
            elif value == 1000: result += max(1, subtotal) * value; subtotal = 0
            else: subtotal += value
        return result + subtotal
    dollars = total(dollars_tokens)
    cents = total(cents_tokens)
    if not dollars and not cents: return None
    return Decimal(dollars) + Decimal(cents) / 100


def parse_transaction_text(text: str) -> dict:
    raw = " ".join(text.strip().split())
    lower = raw.lower()
    kind = "expense"
    if re.search(r"\b(got paid|received|income)\b", lower): kind = "income"
    elif re.search(r"\b(saved|savings|put .* into savings)\b", lower): kind = "savings"
    elif re.search(r"\bpaid\b", lower): kind = "bill"
    numeric = re.search(r"(?:\$\s*)?(\d[\d,]*(?:\.\d{1,2})?)", lower)
    amount = Decimal(numeric.group(1).replace(",", "")) if numeric else None
    if amount is None:
        phrase = re.search(r"(?:cost|paid|received|saved|put|spent)\s+(.+?)(?:\s+(?:on|for|into)\s+|$)", lower)
        amount = words_to_number(phrase.group(1)) if phrase else words_to_number(lower)
    amount_minor = money_to_minor(amount) if amount is not None else None
    cleaned = re.sub(r"(?:\$\s*)?\d[\d,]*(?:\.\d{1,2})?", " ", lower)
    cleaned = re.sub(r"\b(i|and|it|cost|bought|purchased|spent|paid|got|received|saved|put|dollars?|cents?|for|on|into|savings|was|me)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    desc = cleaned.title()
    category = "Other"
    category_map = {"fuel": "Fuel", "lunch": "Dining", "dinner": "Dining", "groceries": "Groceries", "electricity": "Utilities", "rent": "Housing", "salary": "Salary"}
    for word, suggestion in category_map.items():
        if word in lower: category = suggestion; break
    if kind == "income" and category == "Other": category = "Other Income"
    if kind == "savings": category = "Savings"
    return {"transaction_type": kind, "amount_minor": amount_minor, "description": desc, "category": category, "transcript": raw, "needs_review": amount_minor is None or not desc}
