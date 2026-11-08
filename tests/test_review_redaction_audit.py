"""Review queue, redaction, and audit-chain tests."""
import pytest

from conftest import FORM, INVOICE_BAD_TOTAL, INVOICE_OK
from docintel.audit import AuditLog
from docintel.extraction import extract
from docintel.models import DocType, ReviewReason, ReviewStatus, Role
from docintel.redaction import (
    detect_pii,
    mask_value,
    redact_fields,
    redact_text,
)
from docintel.review import ReviewQueue, route_to_review


def _bad_invoice_item(queue):
    result = extract("d1", DocType.INVOICE, INVOICE_BAD_TOTAL)
    item = route_to_review("d1", DocType.INVOICE, extraction=result,
                           reason=ReviewReason.RECONCILIATION_FAILED,
                           detail="totals do not reconcile")
    return queue.enqueue(item)


def test_enqueue_and_pending(queue):
    _bad_invoice_item(queue)
    assert len(queue.pending()) == 1
    assert queue.stats()["total"] == 1


def test_accept_decision(queue):
    _bad_invoice_item(queue)
    item = queue.decide("d1", ReviewStatus.ACCEPTED, by="rev-1",
                        at="2026-11-08T01:00:00Z", note="verified manually")
    assert item.status == ReviewStatus.ACCEPTED
    assert item.decision_by == "rev-1"
    assert queue.pending() == []


def test_correct_decision_applies_overrides_and_stats(queue):
    _bad_invoice_item(queue)
    item = queue.decide("d1", ReviewStatus.CORRECTED, by="rev-1",
                        at="2026-11-08T01:00:00Z",
                        corrections={"total_amount": "900.00"})
    assert item.status == ReviewStatus.CORRECTED
    assert item.extraction.fields["total_amount"].value == "900.00"
    assert queue.correction_stats["total_amount"] == 1


def test_correct_without_corrections_rejected(queue):
    _bad_invoice_item(queue)
    with pytest.raises(ValueError, match="requires at least one correction"):
        queue.decide("d1", ReviewStatus.CORRECTED, by="rev-1",
                     at="2026-11-08T01:00:00Z")


def test_double_decision_rejected(queue):
    _bad_invoice_item(queue)
    queue.decide("d1", ReviewStatus.ACCEPTED, by="rev-1",
                 at="2026-11-08T01:00:00Z")
    with pytest.raises(ValueError, match="already decided"):
        queue.decide("d1", ReviewStatus.REJECTED, by="rev-2",
                     at="2026-11-08T02:00:00Z")


def test_unknown_document_decision_raises(queue):
    with pytest.raises(KeyError):
        queue.decide("nope", ReviewStatus.ACCEPTED, by="rev-1",
                     at="2026-11-08T01:00:00Z")


def test_reject_decision(queue):
    _bad_invoice_item(queue)
    item = queue.decide("d1", ReviewStatus.REJECTED, by="rev-1",
                        at="2026-11-08T01:00:00Z", note="unreadable scan")
    assert item.status == ReviewStatus.REJECTED
    assert queue.stats()["decisions"] == {"rejected": 1}


def test_redaction_by_role():
    result = extract("d1", DocType.FORM, FORM)
    admin = redact_fields(result.fields, Role.ADMIN)
    assert admin["email"] == "jane.sample@example.com"
    restricted = redact_fields(result.fields, Role.RESTRICTED)
    assert restricted["email"] == "[REDACTED]"
    assert restricted["form_title"] == "Vendor Onboarding Application"
    analyst = redact_fields(result.fields, Role.ANALYST)
    assert analyst["email"].endswith(".com")
    assert analyst["email"].startswith("****")
    assert analyst["form_title"] == "Vendor Onboarding Application"


def test_mask_value_short_and_long():
    assert mask_value("abc", Role.ANALYST) == "****"
    assert mask_value("jane.sample@example.com", Role.ANALYST).endswith(".com")
    assert mask_value("anything", Role.RESTRICTED) == "[REDACTED]"
    assert mask_value("anything", Role.ADMIN) == "anything"


def test_redact_text_patterns():
    text = "Contact jane.sample@example.com or +1 415-555-0132, SSN 123-45-6789."
    redacted = redact_text(text, Role.RESTRICTED)
    assert "jane.sample@example.com" not in redacted
    assert "415-555-0132" not in redacted
    assert "123-45-6789" not in redacted
    assert redact_text(text, Role.ADMIN) == text
    assert detect_pii(text) == ["EMAIL", "PHONE", "SSN"]
    assert detect_pii("nothing sensitive here") == []


def test_audit_chain_verifies(audit):
    audit.append("2026-11-08T00:00:00Z", "ingest", "ingested", "d1")
    audit.append("2026-11-08T00:01:00Z", "clf", "classified", "d1")
    audit.append("2026-11-08T00:02:00Z", "rev-1", "review_accepted", "d1")
    ok, detail = audit.verify()
    assert ok is True
    assert "3 entries" in detail


def test_audit_tamper_detected(audit):
    audit.append("2026-11-08T00:00:00Z", "ingest", "ingested", "d1")
    audit.append("2026-11-08T00:01:00Z", "clf", "classified", "d1")
    audit.entries[0].detail = "forged"
    ok, detail = audit.verify()
    assert ok is False
    assert "seq 1" in detail


def test_audit_reorder_detected(audit):
    audit.append("2026-11-08T00:00:00Z", "ingest", "ingested", "d1")
    audit.append("2026-11-08T00:01:00Z", "clf", "classified", "d1")
    audit.entries[0], audit.entries[1] = audit.entries[1], audit.entries[0]
    ok, _ = audit.verify()
    assert ok is False


def test_audit_for_document(audit):
    audit.append("2026-11-08T00:00:00Z", "ingest", "ingested", "d1")
    audit.append("2026-11-08T00:01:00Z", "ingest", "ingested", "d2")
    assert len(audit.for_document("d1")) == 1
