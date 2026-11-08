"""CLI for the document intelligence platform.

Commands: ingest, classify, extract, review (list/decide), report.
Operates on one in-memory run over synthetic sample documents, or on
a text file passed to ``ingest --file``.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from docintel.audit import AuditLog
from docintel.classification import classify
from docintel.extraction import extract
from docintel.ingestion import DocumentStore
from docintel.models import IngestStatus, ReviewReason, ReviewStatus, Role
from docintel.redaction import redact_fields
from docintel.review import ReviewQueue, route_to_review

SAMPLES = {
    "inv-001": (
        "Vendor: Northwind Office Supplies\nInvoice No: INV-2026-1042\n"
        "Invoice Date: 2026-10-28\nPayment Due: 2026-11-27\nPO Number: PO-8814\n"
        "1. Copy paper A4  10 x $12.50 = $125.00\n"
        "2. Desk chairs  4 x $149.99 = $599.96\n"
        "Subtotal: $724.96\nTax: $57.99\nTotal Due: $782.95\n"),
    "con-001": (
        "Master Services Agreement\nThis agreement is entered into between "
        "Acme Retail Group and BrightPath Consulting Services.\n"
        "Effective Date: 2026-01-15\nExpiry Date: 2027-01-14\n"
        "Governing Law: California\nTermination clause. Confidentiality "
        "clause. Payment terms clause. Liability clause.\n"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docintel")
    sub = parser.add_subparsers(dest="command", required=True)
    p_ing = sub.add_parser("ingest")
    p_ing.add_argument("--doc-id", required=True)
    p_ing.add_argument("--file")
    p_ing.add_argument("--text", default="")
    p_ing.add_argument("--mime-type", default="text/plain")
    p_ing.add_argument("--source", default="cli")
    p_cls = sub.add_parser("classify")
    p_cls.add_argument("--doc-id", required=True)
    p_ext = sub.add_parser("extract")
    p_ext.add_argument("--doc-id", required=True)
    p_ext.add_argument("--role", default="analyst",
                       choices=["admin", "analyst", "restricted"])
    sub.add_parser("review")
    p_dec = sub.add_parser("decide")
    p_dec.add_argument("--doc-id", required=True)
    p_dec.add_argument("--decision", required=True,
                       choices=["accepted", "corrected", "rejected"])
    p_dec.add_argument("--by", default="reviewer")
    sub.add_parser("report")
    args = parser.parse_args(argv)

    store = DocumentStore()
    queue = ReviewQueue()
    audit = AuditLog()
    for doc_id, text in SAMPLES.items():
        store.ingest(doc_id, "sample", "text/plain", text, _now())

    if args.command == "ingest":
        content = args.text
        if args.file:
            content = open(args.file, encoding="utf-8").read()
        env = store.ingest(args.doc_id, args.source, args.mime_type,
                           content, _now())
        print(json.dumps({"doc_id": env.doc_id, "status": env.status.value,
                          "hash": env.content_hash,
                          "quarantine_reason": env.quarantine_reason},
                         indent=2))
        return 0
    if args.command == "classify":
        env = store.get(args.doc_id)
        if env is None or env.status != IngestStatus.ACCEPTED:
            print(f"document {args.doc_id} not available for classification",
                  file=sys.stderr)
            return 1
        result = classify(args.doc_id, env.content)
        print(json.dumps({"doc_type": result.doc_type.value,
                          "confidence": result.confidence,
                          "needs_review": result.needs_review,
                          "scores": result.scores}, indent=2))
        return 0
    if args.command == "extract":
        env = store.get(args.doc_id)
        if env is None or env.status != IngestStatus.ACCEPTED:
            print(f"document {args.doc_id} not available for extraction",
                  file=sys.stderr)
            return 1
        result = classify(args.doc_id, env.content)
        extracted = extract(args.doc_id, result.doc_type, env.content)
        print(json.dumps({
            "doc_type": extracted.doc_type.value,
            "schema_version": extracted.schema_version,
            "fields": redact_fields(extracted.fields,
                                    Role(args.role)),
            "reconciliation_passed": extracted.reconciliation_passed,
            "reconciliation_detail": extracted.reconciliation_detail,
            "missing_required": extracted.missing_required}, indent=2))
        return 0
    if args.command == "review":
        print(json.dumps(queue.stats(), indent=2, default=str))
        return 0
    if args.command == "decide":
        print("decide requires a live review queue; use the API or demo",
              file=sys.stderr)
        return 1
    if args.command == "report":
        print(json.dumps({"documents": store.stats(),
                          "review": queue.stats(),
                          "audit": audit.verify()[1]}, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
