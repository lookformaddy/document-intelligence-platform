"""API tests (run in the venv where FastAPI is installed)."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from conftest import INVOICE_BAD_TOTAL, INVOICE_OK

import docintel.service as service


@pytest.fixture
def client():
    service.store = service.DocumentStore()
    service.review_queue = service.ReviewQueue()
    service.audit = service.AuditLog()
    service.classifications.clear()
    service.extractions.clear()
    return TestClient(service.app)


def _ingest(client, doc_id, content):
    return client.post("/documents", json={
        "doc_id": doc_id, "source": "test", "mime_type": "text/plain",
        "content": content})


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ingest_classify_extract_flow(client):
    assert _ingest(client, "d1", INVOICE_OK).status_code == 201
    cls = client.post("/documents/d1/classify")
    assert cls.status_code == 200
    assert cls.json()["doc_type"] == "invoice"
    ext = client.post("/documents/d1/extract")
    assert ext.status_code == 200
    body = ext.json()
    assert body["fields"]["vendor"] == "Northwind Office Supplies"
    assert body["reconciliation_passed"] is True
    assert body["needs_review"] is False


def test_duplicate_returns_409(client):
    assert _ingest(client, "d1", INVOICE_OK).status_code == 201
    resp = _ingest(client, "d2", INVOICE_OK)
    assert resp.status_code == 409


def test_quarantine_returns_422(client):
    resp = _ingest(client, "d1", "")
    assert resp.status_code == 422
    assert "quarantined" in resp.json()["detail"]


def test_extract_before_classify_returns_400(client):
    _ingest(client, "d1", INVOICE_OK)
    assert client.post("/documents/d1/extract").status_code == 400


def test_unknown_document_404(client):
    assert client.get("/documents/nope").status_code == 404
    assert client.post("/documents/nope/classify").status_code == 404


def test_bad_invoice_routes_to_review_and_decide(client):
    _ingest(client, "d1", INVOICE_BAD_TOTAL)
    client.post("/documents/d1/classify")
    ext = client.post("/documents/d1/extract")
    assert ext.json()["reconciliation_passed"] is False
    review = client.get("/review")
    assert len(review.json()["pending"]) == 1
    assert review.json()["pending"][0]["reason"] == "reconciliation_failed"
    decide = client.post("/review/d1/decide", json={
        "decision": "corrected", "by": "rev-1",
        "corrections": {"total_amount": "900.00"}})
    assert decide.status_code == 200
    assert decide.json()["status"] == "corrected"
    assert client.get("/review").json()["pending"] == []


def test_audit_verify_endpoint(client):
    _ingest(client, "d1", INVOICE_OK)
    resp = client.get("/audit/verify")
    assert resp.json()["valid"] is True


def test_report_endpoint(client):
    _ingest(client, "d1", INVOICE_OK)
    resp = client.get("/report")
    assert resp.json()["documents"]["accepted"] == 1
