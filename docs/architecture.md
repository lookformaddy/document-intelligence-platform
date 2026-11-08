# Architecture — Document Intelligence Platform

## Diagram (described)

```
                        ┌─────────────────────┐
                        │  FastAPI service     │
                        │ (docintel/service.py)│
                        │  thin HTTP wrapper   │
                        └──────┬──────────────┘
                               │ owns stores, stamps time,
                               │ maps errors -> HTTP codes
        ┌──────────────┬───────┼───────────────┬──────────────┐
        ▼              ▼       ▼               ▼              ▼
  ┌───────────┐ ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
  │ ingestion │ │classificat│ │ extraction│ │ review  │ │redaction│
  │ .py       │ │ ion.py    │ │ .py      │ │ .py     │ │ .py     │
  │ validate, │ │ keyword   │ │ per-type │ │ queue,  │ │ role-   │
  │ dedup,    │ │ scoring + │ │ extract, │ │ accept/ │ │ based   │
  │ quarantine│ │ threshold │ │ reconcile│ │ correct/│ │ PII mask│
  └───────────┘ └───────────┘ └──────────┘ │ reject  │ └──────────┘
        │              │           │       └──────────┘       │
        ▼              ▼           ▼            │              ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  models.py — dataclasses (envelopes, classifications, fields, │
  │  line items, review items, audit entries). No I/O, no         │
  │  decisions. Schema versions + required-field tables live here. │
  └────────────────────────────────────────────────────────────────┘
        │              │           │            │              │
        ▼              ▼           ▼            ▼              ▼
  ┌──────────────┐ ┌───────────┐ ┌───────────────────────────────┐
  │ audit.py     │ │ cli.py /  │ │  scripts/demo.py              │
  │ hash-chained │ │ scripts   │ │  deterministic synthetic demo │
  │ log          │ │ entry pts │ │                               │
  └──────────────┘ └───────────┘ └───────────────────────────────┘
```

All decision logic lives in the pure-stdlib core (`ingestion`,
`classification`, `extraction`, `review`, `redaction`, `audit`).
The FastAPI service and the CLI are entry points only: they own the
in-memory stores, stamp timestamps, and translate domain errors into
HTTP responses. There is exactly one implementation of every rule, so
the API can never drift from the core.

## Module responsibilities

| Module | Responsibility |
| ------ | -------------- |
| `models.py` | Domain dataclasses and enums; schema versions (`1.0`, `1.1`), required-field tables, PII field set, review threshold, reconciliation tolerance. No I/O; timestamps are explicit arguments, which keeps tests deterministic. |
| `ingestion.py` | Envelope validation (MIME allow-list, 5 MB cap, non-empty), SHA-256 content hash, dedup by hash, quarantine with a reason for anything unreadable, oversized, unsupported, or corrupt. |
| `classification.py` | Deterministic keyword scoring per document type; confidence is the winner's share of the top-two scores; below 0.60 the document routes to review instead of being auto-processed. |
| `extraction.py` | Per-type extractors (invoice / contract / form / identity) with per-field confidence and value normalization (money to integer cents, dates to ISO). Invoice arithmetic is reconciled — line items + tax must equal the stated total — and per-line `qty × unit price` is checked too. Missing required fields and failed reconciliation route to review. |
| `review.py` | Human review queue: enqueue, accept / correct / reject with actor + timestamp, field-level corrections applied to the extraction, and correction statistics per field so extractor weaknesses are measurable. A decision is final — re-deciding raises. |
| `redaction.py` | Role-based PII masking (admin sees all, analyst sees a trailing hint, restricted sees `[REDACTED]`), plus pattern detection (email, phone, SSN-like, card-like, passport-like) for free text. |
| `audit.py` | Append-only SHA-256 hash-chained log; `verify()` replays the chain and reports the first broken link (tamper, reorder, or deletion). |
| `service.py` | FastAPI app; in-memory stores; domain errors → HTTP (ValueError → 400, unknown document → 404, duplicate → 409, quarantined → 422). |
| `cli.py` | `ingest`, `classify`, `extract`, `review`, `decide`, `report` commands over the core with synthetic samples. |

## Data flow: intake to reviewed extraction

1. **Ingest** — `POST /documents` validates the envelope; duplicates
   are rejected (409), corrupt/unsupported/empty documents are
   quarantined (422), everything else is accepted and hashed.
2. **Classify** — `POST /documents/{id}/classify` scores keywords;
   low-confidence documents are enqueued for review.
3. **Extract** — `POST /documents/{id}/extract` runs the per-type
   extractor under the current schema version (`1.1`), normalizes
   values, and reconciles invoice arithmetic. Failures enqueue for
   review; nothing unreconciled is ever presented as trusted.
4. **Review** — `POST /review/{id}/decide` records accept / correct /
   reject with actor and timestamp; corrections update the extraction
   and feed per-field correction stats.
5. **Serve** — extraction output is rendered through the caller's
   role: PII is masked for analysts and fully redacted for
   restricted roles.
6. **Audit** — every step appends to the hash-chained log;
   `/audit/verify` proves the chain.

## Invariants

- A document that fails validation never enters the pipeline; it is
  quarantined with a reason.
- A byte-identical re-submission is a duplicate, never a second
  document.
- Classification below the 0.60 threshold never auto-processes.
- An invoice whose arithmetic does not reconcile is never presented
  as a trusted extraction; it waits in the review queue.
- A review decision is final and always carries an actor and a
  timestamp.
- Extracted output for a restricted role contains no PII values.
- The audit chain verifies end to end, or the first broken link is
  named.
