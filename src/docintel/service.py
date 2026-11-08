"""FastAPI surface for the document intelligence platform.

Thin HTTP wrapper over the pure-stdlib core: it owns the in-memory
stores, stamps timestamps, and translates domain errors into HTTP
responses (ValueError -> 400, unknown document -> 404, duplicate ->
409, quarantined -> 422). All decision logic lives in the core, so
the API adds no second source of truth.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from docintel.audit import AuditLog
from docintel.classification import classify
from docintel.extraction import extract
from docintel.ingestion import DocumentStore
from docintel.models import (
    CURRENT_SCHEMA_VERSION,
    IngestStatus,
    ReviewReason,
    ReviewStatus,
    Role,
)
from docintel.redaction import redact_fields
from docintel.review import ReviewQueue, route_to_review

ACTOR = os.environ.get("DOCINTEL_ACTOR", "api")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


app = FastAPI(title="Document Intelligence Platform", version="1.0.0")

store = DocumentStore()
review_queue = ReviewQueue()
audit = AuditLog()
classifications: dict[str, dict] = {}
extractions: dict[str, dict] = {}


class IngestRequest(BaseModel):
    doc_id: str
    source: str = "upload"
    mime_type: str = "text/plain"
    content: str


class ReviewDecisionRequest(BaseModel):
    decision: str  # accepted | corrected | rejected
    by: str = "reviewer"
    corrections: dict[str, str] | None = None
    note: str = ""


def _extraction_view(result, role: Role) -> dict:
    return {
        "doc_id": result.doc_id,
        "doc_type": result.doc_type.value,
        "schema_version": result.schema_version,
        "fields": redact_fields(result.fields, role),
        "field_confidence": {n: f.confidence
                             for n, f in result.fields.items()},
        "line_items": [vars(item) for item in result.line_items],
        "reconciliation_passed": result.reconciliation_passed,
        "reconciliation_detail": result.reconciliation_detail,
        "missing_required": result.missing_required,
        "needs_review": result.needs_review,
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "documents": store.stats(),
            "review_pending": len(review_queue.pending()),
            "schema_version": CURRENT_SCHEMA_VERSION}


@app.post("/documents", status_code=201)
def ingest_document(req: IngestRequest) -> dict:
    envelope = store.ingest(req.doc_id, req.source, req.mime_type,
                            req.content, _now())
    if envelope.status == IngestStatus.DUPLICATE:
        raise HTTPException(
            status_code=409,
            detail=f"duplicate of {envelope.duplicate_of}")
    if envelope.status == IngestStatus.QUARANTINED:
        audit.append(_now(), ACTOR, "quarantined", req.doc_id,
                     envelope.quarantine_reason)
        raise HTTPException(
            status_code=422,
            detail=f"quarantined: {envelope.quarantine_reason}")
    audit.append(_now(), ACTOR, "ingested", req.doc_id, req.source)
    return {"doc_id": envelope.doc_id, "status": envelope.status.value,
            "content_hash": envelope.content_hash}


@app.get("/documents/{doc_id}")
def get_document(doc_id: str) -> dict:
    envelope = store.get(doc_id)
    if envelope is None:
        raise HTTPException(status_code=404, detail="document not found")
    return {"doc_id": envelope.doc_id, "source": envelope.source,
            "mime_type": envelope.mime_type,
            "status": envelope.status.value,
            "quarantine_reason": envelope.quarantine_reason,
            "duplicate_of": envelope.duplicate_of,
            "content_hash": envelope.content_hash}


@app.post("/documents/{doc_id}/classify")
def classify_document(doc_id: str) -> dict:
    envelope = store.get(doc_id)
    if envelope is None:
        raise HTTPException(status_code=404, detail="document not found")
    if envelope.status != IngestStatus.ACCEPTED:
        raise HTTPException(
            status_code=422,
            detail=f"cannot classify document in status {envelope.status.value}")
    result = classify(doc_id, envelope.content)
    classifications[doc_id] = {
        "doc_type": result.doc_type.value, "confidence": result.confidence,
        "scores": result.scores, "needs_review": result.needs_review}
    audit.append(_now(), ACTOR, "classified", doc_id,
                 f"{result.doc_type.value} conf={result.confidence}")
    if result.needs_review:
        review_queue.enqueue(route_to_review(
            doc_id, result.doc_type,
            reason=ReviewReason.LOW_CONFIDENCE,
            detail=f"confidence {result.confidence} below threshold"))
    return classifications[doc_id]


@app.post("/documents/{doc_id}/extract")
def extract_document(doc_id: str, role: Role = Role.ANALYST) -> dict:
    envelope = store.get(doc_id)
    if envelope is None:
        raise HTTPException(status_code=404, detail="document not found")
    classified = classifications.get(doc_id)
    if classified is None:
        raise HTTPException(
            status_code=400, detail="classify the document first")
    from docintel.models import DocType
    result = extract(doc_id, DocType(classified["doc_type"]),
                     envelope.content)
    extractions[doc_id] = _extraction_view(result, Role.ADMIN)
    audit.append(_now(), ACTOR, "extracted", doc_id,
                 f"schema {result.schema_version} "
                 f"reconciliation={result.reconciliation_passed}")
    if result.needs_review:
        reason = (ReviewReason.RECONCILIATION_FAILED
                  if result.reconciliation_passed is False
                  else ReviewReason.MISSING_REQUIRED_FIELD)
        review_queue.enqueue(route_to_review(
            doc_id, result.doc_type, extraction=result, reason=reason,
            detail="; ".join(result.review_reasons)))
    return _extraction_view(result, role)


@app.get("/review")
def list_review() -> dict:
    return {"pending": [
        {"doc_id": i.doc_id, "doc_type": i.doc_type.value,
         "reason": i.reason.value, "detail": i.detail,
         "status": i.status.value}
        for i in review_queue.pending()],
        "stats": review_queue.stats()}


@app.post("/review/{doc_id}/decide")
def decide_review(doc_id: str, req: ReviewDecisionRequest) -> dict:
    try:
        decision = ReviewStatus(req.decision)
    except ValueError:
        raise HTTPException(status_code=400,
                            detail=f"invalid decision: {req.decision}")
    try:
        item = review_queue.decide(doc_id, decision, req.by, _now(),
                                   corrections=req.corrections,
                                   note=req.note)
    except KeyError:
        raise HTTPException(status_code=404, detail="review item not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit.append(_now(), req.by, f"review_{decision.value}", doc_id,
                 req.note)
    return {"doc_id": item.doc_id, "status": item.status.value,
            "corrections": item.corrections}


@app.get("/audit/verify")
def verify_audit() -> dict:
    ok, detail = audit.verify()
    return {"valid": ok, "detail": detail,
            "entries": len(audit.entries)}


@app.get("/report")
def report() -> dict:
    return {"documents": store.stats(),
            "review": review_queue.stats(),
            "classifications": len(classifications),
            "extractions": len(extractions),
            "schema_version": CURRENT_SCHEMA_VERSION}
