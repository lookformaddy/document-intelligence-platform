"""Role-based PII redaction of extracted output and raw text.

Admins see everything; analysts see PII masked except for a trailing
hint (last 4 characters where a value is long enough); restricted
roles see PII replaced entirely. Non-PII fields are never touched.
Detection is pattern-based (email, phone, SSN-like, card-like,
passport-like) so it also works on free text, not just labelled
fields.
"""
from __future__ import annotations

import re

from docintel.models import ExtractedField, Role

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("PHONE", re.compile(r"\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("PASSPORT", re.compile(r"\b[A-Z]{1,2}\d{6,9}\b")),
]


def mask_value(value: str, role: Role) -> str:
    if role == Role.ADMIN:
        return value
    if role == Role.RESTRICTED:
        return "[REDACTED]"
    # Analyst: keep a trailing hint so values stay distinguishable.
    if len(value) > 6:
        return "****" + value[-4:]
    return "****"


def redact_text(text: str, role: Role) -> str:
    if role == Role.ADMIN:
        return text
    redacted = text
    for label, pattern in _PATTERNS:
        if role == Role.RESTRICTED:
            redacted = pattern.sub(f"[REDACTED:{label}]", redacted)
        else:
            def _mask(match: re.Match[str]) -> str:
                value = match.group(0)
                return "****" + value[-4:] if len(value) > 6 else "****"
            redacted = pattern.sub(_mask, redacted)
    return redacted


def redact_fields(fields: dict[str, ExtractedField],
                  role: Role) -> dict[str, str]:
    """Extracted fields rendered for a role: PII masked per role."""
    out: dict[str, str] = {}
    for name, fld in fields.items():
        out[name] = mask_value(fld.value, role) if fld.is_pii else fld.value
    return out


def detect_pii(text: str) -> list[str]:
    """Labels of the PII patterns present in free text."""
    return [label for label, pattern in _PATTERNS if pattern.search(text)]
