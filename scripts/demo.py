"""Deterministic end-to-end demo: ingest a synthetic document set,
classify and extract each one, route failures to human review, decide
one review item, redact for a restricted role, and verify the audit
chain. Prints verifiable numbers.

All data is synthetic; no network calls, no API keys. Run with:
    PYTHONPATH=src python scripts/demo.py
"""
from __future__ import annotations

from docintel.audit import AuditLog
from docintel.classification import classify
from docintel.extraction import extract
from docintel.ingestion import DocumentStore
from docintel.models import (
    DocType,
    ReviewReason,
    ReviewStatus,
    Role,
)
from docintel.redaction import redact_fields
from docintel.review import ReviewQueue, route_to_review

NOW = "2026-11-08T09:00:00Z"


def _invoice(number: str, total: str, tax: str = "57.99") -> str:
    return (
        "Vendor: Northwind Office Supplies\n"
        f"Invoice No: {number}\nInvoice Date: 2026-10-28\n"
        "Payment Due: 2026-11-27\nPO Number: PO-8814\n"
        "1. Copy paper A4  10 x $12.50 = $125.00\n"
        "2. Desk chairs  4 x $149.99 = $599.96\n"
        "Subtotal: $724.96\n"
        f"Tax: ${tax}\nTotal Due: ${total}\n"
    )


def _contract(party_a: str, party_b: str) -> str:
    return (
        "Master Services Agreement\nThis agreement is entered into between "
        f"{party_a} and {party_b}.\n"
        "Effective Date: 2026-01-15\nExpiry Date: 2027-01-14\n"
        "Governing Law: California\nTermination clause. Confidentiality "
        "clause. Payment terms clause. Liability clause.\n"
    )


def _form(name: str, email: str) -> str:
    return (
        "Form Title: Vendor Onboarding Application\n"
        f"Applicant Name: {name}\nEmail: {email}\n"
        "Phone: +1 415-555-0132\nMailing Address: 12 Example Street, Springfield\n"
        "Date of Birth: 1990-04-12\nPreferred Contact: Email\n"
    )


def _identity(number: str, name: str) -> str:
    return (
        "Document Type: Passport\n"
        f"Document Number: {number}\nFull Name: {name}\n"
        "Date of Birth: 1990-04-12\nExpiry Date: 2030-04-11\n"
        "Issuing Authority: Example State Department\nNationality: Example\n"
    )


# 12 synthetic documents: 4 invoices (1 with a wrong total), 3
# contracts, 3 forms, 2 identity documents.
DOCUMENTS: list[tuple[str, str, DocType]] = [
    ("inv-01", _invoice("INV-2026-1042", "782.95"), DocType.INVOICE),
    ("inv-02", _invoice("INV-2026-1043", "782.95"), DocType.INVOICE),
    ("inv-03", _invoice("INV-2026-1044", "782.95"), DocType.INVOICE),
    ("inv-04", _invoice("INV-2026-1045", "900.00"), DocType.INVOICE),
    ("con-01", _contract("Acme Retail Group", "BrightPath Consulting"),
     DocType.CONTRACT),
    ("con-02", _contract("Harbor Logistics Co", "Summit Advisory"),
     DocType.CONTRACT),
    ("con-03", _contract("Pinecrest Foods", "Northgate Legal"),
     DocType.CONTRACT),
    ("frm-01", _form("Jane Sample", "jane.sample@example.com"), DocType.FORM),
    ("frm-02", _form("Alex Rivera", "alex.rivera@example.com"), DocType.FORM),
    ("frm-03", _form("Priya Nair", "priya.nair@example.com"), DocType.FORM),
    ("id-01", _identity("X12345678", "Jane Sample"), DocType.IDENTITY),
    ("id-02", _identity("Y87654321", "Alex Rivera"), DocType.IDENTITY),
]


def main() -> None:
    store = DocumentStore()
    queue = ReviewQueue()
    audit = AuditLog()

    correct = 0
    reconciled = 0
    invoices = 0
    for doc_id, content, expected in DOCUMENTS:
        env = store.ingest(doc_id, "demo-intake", "text/plain", content, NOW)
        audit.append(NOW, "demo", "ingested", doc_id, env.status.value)
        result = classify(doc_id, env.content)
        audit.append(NOW, "demo", "classified", doc_id,
                     f"{result.doc_type.value} conf={result.confidence}")
        if result.doc_type == expected:
            correct += 1
        if result.needs_review:
            queue.enqueue(route_to_review(
                doc_id, result.doc_type, reason=ReviewReason.LOW_CONFIDENCE,
                detail=f"confidence {result.confidence}"))
        extracted = extract(doc_id, result.doc_type, env.content)
        audit.append(NOW, "demo", "extracted", doc_id,
                     f"reconciliation={extracted.reconciliation_passed}")
        if result.doc_type == DocType.INVOICE:
            invoices += 1
            if extracted.reconciliation_passed:
                reconciled += 1
        if extracted.needs_review:
            reason = (ReviewReason.RECONCILIATION_FAILED
                      if extracted.reconciliation_passed is False
                      else ReviewReason.MISSING_REQUIRED_FIELD)
            queue.enqueue(route_to_review(
                doc_id, result.doc_type, extraction=extracted,
                reason=reason, detail="; ".join(extracted.review_reasons)))

    # One duplicate and one corrupt document: both must fail closed.
    store.ingest("inv-01-dup", "demo-intake", "text/plain",
                 DOCUMENTS[0][1], NOW)
    store.ingest("bad-01", "demo-intake", "text/plain",
                 "%%CORRUPT%% unreadable bytes", NOW)

    # Human review: correct the wrong invoice total, accept the rest.
    pending = queue.pending()
    for item in pending:
        if item.doc_id == "inv-04":
            queue.decide(item.doc_id, ReviewStatus.CORRECTED, by="demo-reviewer",
                         at=NOW, corrections={"total_amount": "782.95"},
                         note="verified against PO-8814")
            audit.append(NOW, "demo-reviewer", "review_corrected", "inv-04")
    accuracy = correct / len(DOCUMENTS)
    print("Document Intelligence Platform — demo")
    print(f"documents ingested: {len(DOCUMENTS)}")
    print(f"classification accuracy: {correct}/{len(DOCUMENTS)} "
          f"({accuracy:.1%})")
    print(f"invoice reconciliation pass rate: {reconciled}/{invoices} "
          f"({reconciled / invoices:.1%})")
    print(f"ingestion stats: {store.stats()}")
    print(f"review items routed: {len(queue.items)} "
          f"(pending after decisions: {len(queue.pending())})")
    print(f"correction stats: {dict(queue.correction_stats)}")
    # Redaction: the same form extraction, two roles.
    form = extract("frm-01", DocType.FORM, DOCUMENTS[7][1])
    print("restricted-role view of frm-01: "
          f"{redact_fields(form.fields, Role.RESTRICTED)}")
    print("analyst-role view of frm-01 email: "
          f"{redact_fields(form.fields, Role.ANALYST)['email']}")
    ok, detail = audit.verify()
    print(f"audit chain valid: {ok} ({detail})")


if __name__ == "__main__":
    main()
