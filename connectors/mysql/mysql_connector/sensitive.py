"""Recognise columns and values that probably hold personal or secret data, and mask them.

Detection is deliberately cautious: when a column looks sensitive its values are masked or
suppressed everywhere (samples, top values, min/max). A model never receives them unmasked.
"""

import re
from decimal import Decimal
from typing import Any

NAME_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("credential", re.compile(r"pass(word|wd)?$|^pwd|secret|token|api[_-]?key|private[_-]?key|salt$|hash$", re.I)),
    ("email", re.compile(r"e[_-]?mail", re.I)),
    ("phone", re.compile(r"phone|mobile|\bcell\b|^tel(ephone)?$|fax", re.I)),
    ("id_number", re.compile(r"\bssn\b|^sin$|social[_-]?(sec|ins)|passport|national[_-]?id|tax[_-]?id|\bnin\b|licen[cs]e[_-]?(no|num)", re.I)),
    ("financial", re.compile(r"card[_-]?(no|num)|cc[_-]?num|\biban\b|account[_-]?(no|num)|\bcvv\b|routing[_-]?num", re.I)),
    ("birth_date", re.compile(r"birth|\bdob\b", re.I)),
    ("name", re.compile(r"^(first|last|full|middle|sur|given|family)[_-]?name$|^[fl]name$|^surname$", re.I)),
    ("address", re.compile(r"street|address|postal|zip[_-]?code|^zip$", re.I)),
    ("network", re.compile(r"ip[_-]?addr|^ip$|mac[_-]?addr", re.I)),
]

VALUE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")),
    ("card", re.compile(r"^(?:\d[ -]?){13,19}$")),
    ("id_number", re.compile(r"^\d{3}-\d{2}-\d{4}$")),
    ("phone", re.compile(r"^\+?[\d\s().-]{9,20}$")),
]

MASK = {
    "email": "[email]",
    "phone": "[phone]",
    "credential": "[secret]",
    "id_number": "[id]",
    "financial": "[financial]",
    "birth_date": "[date of birth]",
    "name": "[name]",
    "address": "[address]",
    "network": "[address]",
    "card": "[card]",
}


def classify_name(column: str) -> str | None:
    for kind, pattern in NAME_RULES:
        if pattern.search(column):
            return kind
    return None


def luhn(digits: str) -> bool:
    total, flip = 0, False
    for char in reversed(digits):
        number = int(char)
        if flip:
            number = number * 2 - 9 if number * 2 > 9 else number * 2
        total += number
        flip = not flip
    return total % 10 == 0


def classify_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    for kind, pattern in VALUE_RULES:
        if pattern.match(text):
            digits = re.sub(r"\D", "", text)
            if kind == "card" and not luhn(digits):
                continue  # just a long number; later rules may still apply
            if kind == "phone" and not 9 <= len(digits) <= 15:
                continue
            return kind
    return None


def mask_cell(value: Any, column_kind: str | None) -> Any:
    """A cell safe to show: masked when its column is sensitive or the value itself looks personal."""
    if value is None:
        return None
    kind = column_kind or classify_value(value)
    if kind:
        return MASK.get(kind, "[masked]")
    if isinstance(value, bytes):
        return f"<binary {len(value)} bytes>"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, str) and len(value) > 200:
        return value[:200] + "…"
    if isinstance(value, (int, float, bool, str)):
        return value
    return str(value)
