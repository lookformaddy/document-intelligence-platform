"""Per-document-type field extraction with value normalization.

Extractors are regex over labelled lines plus small heuristics, each
field carrying its own confidence and a normalized value (money in
integer cents, dates in ISO form). Invoice extraction reconciles the
arithmetic — line items + tax must equal the stated total — and a
failed reconciliation routes the document to human review instead of
being silently trusted.
"""
from __future__ import annotations

import re
from datetime import datetime

from docintel.models import (
    CURRENT_SCHEMA_VERSION,
    PII_FIELDS,
    RECONCILIATION_TOLERANCE_CENTS,
    REQUIRED_FIELDS,
    DocType,
    ExtractedField,
    ExtractionResult,
    LineItem,
)

_MONEY_RE = r"\$?\s*([\d,]+(?:\.\d{2})?)"
_DATE_PATTERNS = (
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "%Y-%m-%d"),
    (re.compile(r"(\d{2})/(\d{2})/(\d{4})"), "%m/%d/%Y"),
    (re.compile(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})",
                re.IGNORECASE), None),
)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def money_to_cents(raw: str) -> int:
    cleaned = raw.replace("$", "").replace(",", "").strip()
    return int(round(float(cleaned) * 100))


def cents_to_str(cents: int) -> str:
    return f"{cents / 100:.2f}"


def normalize_date(raw: str) -> str:
    text = raw.strip()
    for pattern, fmt in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if fmt is not None:
            try:
                return datetime.strptime(match.group(0), fmt).date().isoformat()
            except ValueError:
                continue
        month = _MONTHS[match.group(2).lower()[:3]]
        try:
            return datetime(int(match.group(3)), month,
                            int(match.group(1))).date().isoformat()
        except ValueError:
            continue
    return text


def _label(content: str, *labels: str) -> str | None:
    """First `Label: value` line for any of the given labels."""
    for label in labels:
        match = re.search(
            rf"(?im)^{re.escape(label)}\s*:\s*(.+?)\s*$", content)
        if match:
            return match.group(1).strip()
    return None


def _field(name: str, value: str | None, confidence: float,
           normalized: str = "", snippet: str = "") -> ExtractedField | None:
    if value is None or not str(value).strip():
        return None
    value = str(value).strip()
    return ExtractedField(
        name=name, value=value,
        normalized_value=normalized or value,
        confidence=confidence, is_pii=name in PII_FIELDS,
        source_snippet=snippet or value,
    )


def _add(result: ExtractionResult, fld: ExtractedField | None) -> None:
    if fld is not None:
        result.fields[fld.name] = fld


_LINE_ITEM_RE = re.compile(
    r"(?im)^\s*(?:\d+[.)]\s*)?(.+?)\s+(\d+)\s*[x×]\s*\$?([\d,]+(?:\.\d{2})?)\s*=\s*\$?([\d,]+(?:\.\d{2})?)\s*$")
_SIMPLE_ITEM_RE = re.compile(
    r"(?im)^\s*(?:\d+[.)]\s*)?(.+?)\s+-\s*\$?([\d,]+(?:\.\d{2})?)\s*$")


def extract_invoice(doc_id: str, content: str) -> ExtractionResult:
    result = ExtractionResult(doc_id=doc_id, doc_type=DocType.INVOICE,
                              schema_version=CURRENT_SCHEMA_VERSION)
    _add(result, _field("vendor", _label(content, "Vendor", "From", "Billed by"),
                        0.95))
    _add(result, _field("invoice_number",
                        _label(content, "Invoice No", "Invoice Number",
                               "Invoice #"), 0.98))
    raw_date = _label(content, "Invoice Date", "Date")
    if raw_date:
        _add(result, _field("invoice_date", raw_date, 0.95,
                            normalize_date(raw_date)))
    raw_due = _label(content, "Payment Due", "Due Date")
    if raw_due:
        _add(result, _field("due_date", raw_due, 0.95,
                            normalize_date(raw_due)))
    _add(result, _field("po_number", _label(content, "PO Number", "PO #"),
                        0.9))
    for m in _LINE_ITEM_RE.finditer(content):
        desc, qty, unit, amount = m.groups()
        result.line_items.append(LineItem(
            description=desc.strip(), quantity=int(qty),
            unit_price_cents=money_to_cents(unit),
            amount_cents=money_to_cents(amount)))
    if not result.line_items:
        for m in _SIMPLE_ITEM_RE.finditer(content):
            desc, amount = m.groups()
            if desc.strip().lower() in ("subtotal", "tax", "total",
                                        "total due"):
                continue
            result.line_items.append(LineItem(
                description=desc.strip(), quantity=1,
                unit_price_cents=money_to_cents(amount),
                amount_cents=money_to_cents(amount)))
    amounts: dict[str, int] = {}
    for key, label in (("subtotal", "Subtotal"), ("tax", "Tax"),
                       ("total_amount", "Total Due"), ("total_amount", "Total")):
        raw = _label(content, label)
        if raw:
            amounts[key] = money_to_cents(raw)
    for key, cents in amounts.items():
        _add(result, _field(key, cents_to_str(cents), 0.95,
                            str(cents)))
    # Arithmetic reconciliation: line items + tax == total.
    if "total_amount" in result.fields and result.line_items:
        items_total = sum(item.amount_cents for item in result.line_items)
        tax = amounts.get("tax", 0)
        expected = items_total + tax
        stated = amounts["total_amount"]
        diff = abs(expected - stated)
        result.reconciliation_passed = diff <= RECONCILIATION_TOLERANCE_CENTS
        result.reconciliation_detail = (
            f"line_items={cents_to_str(items_total)} tax={cents_to_str(tax)} "
            f"expected={cents_to_str(expected)} stated={cents_to_str(stated)} "
            f"diff_cents={diff}")
    else:
        result.reconciliation_passed = None
        result.reconciliation_detail = "insufficient data to reconcile"
    # A line item whose qty x unit price disagrees with its amount is
    # also a reconciliation failure, even if the totals happen to add up.
    for item in result.line_items:
        if item.expected_cents != item.amount_cents:
            result.reconciliation_passed = False
            result.reconciliation_detail += (
                f"; line item mismatch: {item.description} "
                f"{item.quantity}x{cents_to_str(item.unit_price_cents)} "
                f"!= {cents_to_str(item.amount_cents)}")
            break
    return _finalize(result)


def extract_contract(doc_id: str, content: str) -> ExtractionResult:
    result = ExtractionResult(doc_id=doc_id, doc_type=DocType.CONTRACT,
                              schema_version=CURRENT_SCHEMA_VERSION)
    parties = re.search(
        r"(?i)between\s+(.+?)\s+and\s+(.+?)(?:,|\n|$)", content)
    if parties:
        _add(result, _field("party_a", parties.group(1).strip().rstrip("."), 0.9))
        _add(result, _field("party_b", parties.group(2).strip().rstrip("."), 0.9))
    raw_eff = _label(content, "Effective Date")
    if raw_eff:
        _add(result, _field("effective_date", raw_eff, 0.95,
                            normalize_date(raw_eff)))
    raw_exp = _label(content, "Expiry Date", "Expiration Date", "End Date")
    if raw_exp:
        _add(result, _field("expiry_date", raw_exp, 0.95,
                            normalize_date(raw_exp)))
    _add(result, _field("governing_law",
                        _label(content, "Governing Law", "Jurisdiction"),
                        0.9))
    clauses: list[str] = []
    for clause in ("termination", "confidentiality", "payment terms",
                   "liability", "non-disclosure", "intellectual property"):
        if re.search(rf"(?i)\b{re.escape(clause)}\b", content):
            clauses.append(clause)
    if clauses:
        _add(result, _field("clauses", ", ".join(clauses), 0.85,
                            ", ".join(clauses)))
    _add(result, _field("agreement_title",
                        _label(content, "Agreement Title", "Title"), 0.9))
    return _finalize(result)


def extract_form(doc_id: str, content: str) -> ExtractionResult:
    result = ExtractionResult(doc_id=doc_id, doc_type=DocType.FORM,
                              schema_version=CURRENT_SCHEMA_VERSION)
    _add(result, _field("form_title",
                        _label(content, "Form Title", "Application Form"),
                        0.95))
    for name, labels in (
            ("applicant_name", ("Applicant Name", "Full Name", "Name")),
            ("email", ("Email", "Email Address")),
            ("phone", ("Phone", "Phone Number", "Telephone")),
            ("address", ("Mailing Address", "Address")),
            ("date_of_birth", ("Date of Birth", "DOB"))):
        raw = _label(content, *labels)
        if raw is None:
            continue
        normalized = normalize_date(raw) if name == "date_of_birth" else raw
        _add(result, _field(name, raw, 0.9, normalized))
    # Any other `Label: value` lines become generic named fields.
    for match in re.finditer(r"(?im)^([A-Za-z][A-Za-z ]{2,30}):\s*(.+?)\s*$",
                             content):
        key = match.group(1).strip().lower().replace(" ", "_")
        if key in result.fields or key in (
                "form_title", "applicant_name", "email", "phone", "address",
                "date_of_birth", "mailing_address"):
            continue
        if match.group(2).strip() in (
                f.value for f in result.fields.values()):
            continue  # same line already captured under another label
        _add(result, _field(key, match.group(2), 0.7))
    return _finalize(result)


def extract_identity(doc_id: str, content: str) -> ExtractionResult:
    result = ExtractionResult(doc_id=doc_id, doc_type=DocType.IDENTITY,
                              schema_version=CURRENT_SCHEMA_VERSION)
    _add(result, _field("document_type",
                        _label(content, "Document Type"), 0.95))
    _add(result, _field("document_number",
                        _label(content, "Document Number", "Passport No",
                               "License No", "ID Number"), 0.98))
    _add(result, _field("full_name",
                        _label(content, "Full Name", "Holder Name", "Name"),
                        0.95))
    raw_dob = _label(content, "Date of Birth", "DOB")
    if raw_dob:
        _add(result, _field("date_of_birth", raw_dob, 0.95,
                            normalize_date(raw_dob)))
    raw_exp = _label(content, "Expiry Date", "Expiration Date")
    if raw_exp:
        _add(result, _field("expiry_date", raw_exp, 0.95,
                            normalize_date(raw_exp)))
    _add(result, _field("issuing_authority",
                        _label(content, "Issuing Authority", "Issued By"),
                        0.9))
    _add(result, _field("nationality", _label(content, "Nationality"), 0.9))
    return _finalize(result)


def extract_other(doc_id: str, content: str) -> ExtractionResult:
    result = ExtractionResult(doc_id=doc_id, doc_type=DocType.OTHER,
                              schema_version=CURRENT_SCHEMA_VERSION)
    _add(result, _field("snippet", content.strip()[:200], 0.5))
    return _finalize(result)


_EXTRACTORS = {
    DocType.INVOICE: extract_invoice,
    DocType.CONTRACT: extract_contract,
    DocType.FORM: extract_form,
    DocType.IDENTITY: extract_identity,
    DocType.OTHER: extract_other,
}


def extract(doc_id: str, doc_type: DocType, content: str) -> ExtractionResult:
    return _EXTRACTORS[doc_type](doc_id, content)


def _finalize(result: ExtractionResult) -> ExtractionResult:
    result.missing_required = [
        name for name in REQUIRED_FIELDS[result.doc_type]
        if name not in result.fields]
    if result.missing_required:
        result.review_reasons.append(
            "missing_required_field:" + ",".join(result.missing_required))
    if result.reconciliation_passed is False:
        result.review_reasons.append("reconciliation_failed")
    result.needs_review = bool(result.review_reasons)
    return result
