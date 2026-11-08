# Document Intelligence Platform

An **enterprise document intelligence platform** for document
intake: invoices, contracts, forms, and identity documents arrive
as envelopes, are classified and field-extracted deterministically,
and anything uncertain — low confidence, unreconciled arithmetic,
missing required fields — routes to a human review queue instead of
being silently trusted. PII is redacted by role, every step is
written to a hash-chained audit log, and extraction schemas are
versioned so downstream consumers can tell current output from
stale output. All documents in the demo and tests are synthetic;
no real customer documents are involved.

Most document pipelines optimize for throughput and quietly pass
bad extractions downstream, where a wrong invoice total becomes a
wrong payment and a misclassified contract becomes a missed
obligation. This platform optimizes for trust at intake: the
pipeline is fail-closed at every stage, and the review queue is a
first-class output, not an afterthought.

## What it does

- **Ingestion (fail-closed)** — envelopes carry source metadata,
  MIME type, and content. Validation enforces a MIME allow-list
  (`text/plain`, `application/pdf`, `text/csv`), a 5 MB cap, and
  non-empty content; anything else is quarantined with a reason.
  A SHA-256 content hash dedups byte-identical re-submissions
  (409 on the API) instead of processing the same document twice.
- **Classification** — deterministic keyword scoring per type
  (invoice / contract / form / identity / other), one count per
  keyword so long documents cannot inflate their score. Confidence
  is the winner's share of the top-two scores; below 0.60 the
  document routes to review.
- **Field extraction** — per-type extractors with per-field
  confidence and value normalization (money to integer cents,
  dates to ISO). Invoice: vendor, number, dates, PO number, line
  items, subtotal/tax/total. Contract: parties, effective/expiry
  dates, governing law, clause inventory. Form: named fields plus
  generic `Label: value` capture. Identity: document number,
  name, dates, issuing authority.
- **Arithmetic reconciliation** — line items + tax must equal the
  stated total (within 1 cent), and each line's `qty × unit price`
  must equal its amount. A failure routes to review with the exact
  discrepancy in the detail string.
- **Human review queue** — low-confidence, unreconciled, or
  incomplete documents wait here. Reviewers accept, correct
  (field-level overrides applied to the extraction), or reject,
  with actor and timestamp recorded. Decisions are final;
  corrections feed per-field statistics so extractor weaknesses
  are measurable instead of anecdotal.
- **Versioned extraction schemas** — every result names its
  schema version (current: `1.1`); required fields are declared
  per type and missing ones are reported, not silently omitted.
- **Role-based redaction** — admins see everything, analysts see
  PII masked to a trailing hint, restricted roles see PII fully
  replaced. Pattern detection (email, phone, SSN-like, card-like,
  passport-like) redacts PII in free text too.
- **Audit trail** — SHA-256 hash-chained, append-only; `verify()`
  replays the chain and reports the first broken link (tamper,
  reorder, or deletion).
- **FastAPI service + CLI**, deterministic synthetic demo (no
  network calls, no API keys).

## Architecture

See [docs/architecture.md](docs/architecture.md).

```
ingestion (validate, dedup, quarantine) ──▶ classification (keyword scores)
        │                                            │
        ▼                                            ▼
   quarantine                              extraction (per-type, normalized)
        │                                            │
        ▼                                            ▼
  audit log ◀── review queue (accept / correct / reject) ──▶ role-redacted output
```

## How to run

```bash
pip install -r requirements.txt
make test          # pytest suite (core tests run on stdlib only)
make demo          # end-to-end demo: 12 synthetic docs → classify → extract → review → redact → audit
uvicorn docintel.service:app --host 0.0.0.0 --port 8000   # with PYTHONPATH=src
```

CLI:

```bash
PYTHONPATH=src python -m docintel.cli ingest --doc-id inv-001 --text "Vendor: ..."
PYTHONPATH=src python -m docintel.cli classify --doc-id inv-001
PYTHONPATH=src python -m docintel.cli extract --doc-id inv-001 --role restricted
PYTHONPATH=src python -m docintel.cli report
```

## API surface

| Method | Endpoint | Purpose |
| ------ | -------- | ------- |
| `GET` | `/health` | Liveness + document/review counts |
| `POST` | `/documents` | Ingest (duplicate → 409, quarantined → 422) |
| `GET` | `/documents/{id}` | Envelope status + hash |
| `POST` | `/documents/{id}/classify` | Keyword classification; low confidence → review |
| `POST` | `/documents/{id}/extract` | Per-type extraction, role-redacted (`?role=`) |
| `GET` | `/review` | Pending review queue + correction stats |
| `POST` | `/review/{id}/decide` | Accept / correct / reject with actor |
| `GET` | `/audit/verify` | Hash-chain verification |
| `GET` | `/report` | Ingestion, review, and extraction counts |

## Key design decisions and trade-offs

- **Deterministic rules over a learned model.** Intake
  classification and extraction must be explainable to an auditor
  and reproducible in a test: a keyword table and labelled-line
  regex are. The trade-off is coverage — unusual layouts fall to
  review instead of being guessed at — which is the right trade
  for a control plane; a learned extractor can sit behind the same
  interface later, with the confidence and reconciliation contracts
  kept.
- **Fail-closed at every stage.** Quarantine, review routing, and
  redaction all default to the safe outcome. A pipeline that fails
  open manufactures false assurance and real financial exposure.
- **Reconciliation as a gate, not a report.** An invoice whose
  arithmetic does not add up is not a low-confidence extraction;
  it is an untrusted one, and it waits for a human. Totals are
  computed in integer cents so floating-point rounding can never
  manufacture or hide a discrepancy.
- **Review corrections feed extractor stats.** Every correction is
  counted per field, so the team can see which fields the
  extractor gets wrong instead of fixing documents one at a time
  forever.
- **Versioned schemas, declared required fields.** A consumer that
  reads an extraction can check the schema version and the missing
  list instead of guessing whether a field was absent or merely
  unextracted.
- **Pure-stdlib core, FastAPI at the edge.** All decision logic is
  testable without a server, a database, or a network. The service
  is a thin wrapper, so the API can never become a second source of
  truth. The trade-off is in-memory stores: persistence (a real
  database, plus durable audit storage) is the explicit next step.

## What it demonstrates

Enterprise document understanding end to end: fail-closed
ingestion with dedup and quarantine, deterministic classification
with a review threshold, per-type field extraction with value
normalization and arithmetic reconciliation, a human review queue
with audited decisions and correction statistics, versioned
extraction schemas, role-based PII redaction, and a hash-chained
audit log — built as a typed, tested, enterprise-shaped Python
codebase with a FastAPI service, CLI, Docker image, and CI
definition.
