"""Ingestion tests: validation, dedup, quarantine (fail-closed)."""
from conftest import INVOICE_OK, ingest
from docintel.ingestion import content_hash
from docintel.models import IngestStatus


def test_accepts_valid_document(store):
    env = ingest(store, "d1", INVOICE_OK)
    assert env.status == IngestStatus.ACCEPTED
    assert env.content_hash == content_hash(INVOICE_OK)
    assert len(env.content_hash) == 64


def test_duplicate_detected_by_content_hash(store):
    first = ingest(store, "d1", INVOICE_OK)
    second = ingest(store, "d2", INVOICE_OK)
    assert second.status == IngestStatus.DUPLICATE
    assert second.duplicate_of == first.doc_id
    assert store.stats()["duplicates"] == 1


def test_different_content_is_not_duplicate(store):
    ingest(store, "d1", INVOICE_OK)
    other = ingest(store, "d2", INVOICE_OK + "extra line\n")
    assert other.status == IngestStatus.ACCEPTED


def test_empty_content_quarantined(store):
    env = ingest(store, "d1", "   \n")
    assert env.status == IngestStatus.QUARANTINED
    assert env.quarantine_reason == "empty_content"


def test_unsupported_mime_quarantined(store):
    env = ingest(store, "d1", INVOICE_OK, mime="application/octet-stream")
    assert env.status == IngestStatus.QUARANTINED
    assert "unsupported_mime_type" in env.quarantine_reason


def test_corrupt_content_quarantined(store):
    env = ingest(store, "d1", "%%CORRUPT%%" + INVOICE_OK)
    assert env.status == IngestStatus.QUARANTINED
    assert env.quarantine_reason == "corrupt_content"


def test_pdf_mime_accepted(store):
    env = ingest(store, "d1", INVOICE_OK, mime="application/pdf")
    assert env.status == IngestStatus.ACCEPTED


def test_stats_count(store):
    ingest(store, "d1", INVOICE_OK)
    ingest(store, "d2", INVOICE_OK)
    ingest(store, "d3", "")
    stats = store.stats()
    assert stats == {"total": 3, "accepted": 1, "duplicates": 1,
                     "quarantined": 1}
