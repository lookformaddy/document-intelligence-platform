"""Domain models for the document intelligence platform.

Dataclasses only — no I/O, no decisions. Timestamps are explicit
arguments so tests stay deterministic. All content handled here is
synthetic sample text; no real customer documents are involved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DocType(str, Enum):
    INVOICE = "invoice"
    CONTRACT = "contract"
    FORM = "form"
    IDENTITY = "identity"
    OTHER = "other"


class IngestStatus(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    QUARANTINED = "quarantined"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    CORRECTED = "corrected"
    REJECTED = "rejected"


class ReviewReason(str, Enum):
    LOW_CONFIDENCE = "low_confidence"
    RECONCILIATION_FAILED = "reconciliation_failed"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    MANUAL = "manual"


class Role(str, Enum):
    ADMIN = "admin"
    ANALYST = "analyst"
    RESTRICTED = "restricted"


# Schema versions, newest last. Extraction results always name the
# schema version that produced them, so a downstream consumer can
# tell whether a field set is current or stale.
SCHEMA_VERSIONS = ("1.0", "1.1")
CURRENT_SCHEMA_VERSION = "1.1"

REQUIRED_FIELDS: dict[DocType, tuple[str, ...]] = {
    DocType.INVOICE: ("vendor", "invoice_number", "total_amount"),
    DocType.CONTRACT: ("party_a", "party_b", "effective_date"),
    DocType.FORM: ("form_title",),
    DocType.IDENTITY: ("document_number", "full_name"),
    DocType.OTHER: (),
}

# Fields that carry personal data and must be redacted for restricted roles.
PII_FIELDS = {
    "full_name", "email", "phone", "phone_number", "date_of_birth",
    "document_number", "address", "mailing_address", "national_id",
    "passport_number", "applicant_name",
}

ALLOWED_MIME_TYPES = {"text/plain", "application/pdf", "text/csv"}
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
# Below this classification confidence a document routes to review.
REVIEW_CONFIDENCE_THRESHOLD = 0.60
# Invoice reconciliation tolerance in cents.
RECONCILIATION_TOLERANCE_CENTS = 1


@dataclass
class DocumentEnvelope:
    """A document as ingested: raw text plus source metadata."""

    doc_id: str
    source: str
    mime_type: str
    content: str
    received_at: str
    content_hash: str = ""
    status: IngestStatus = IngestStatus.ACCEPTED
    quarantine_reason: str = ""
    duplicate_of: str = ""


@dataclass
class ClassificationResult:
    doc_id: str
    doc_type: DocType
    confidence: float
    scores: dict[str, float] = field(default_factory=dict)
    needs_review: bool = False
    reason: str = ""


@dataclass
class ExtractedField:
    name: str
    value: str
    normalized_value: str = ""
    confidence: float = 1.0
    is_pii: bool = False
    source_snippet: str = ""


@dataclass
class LineItem:
    description: str
    quantity: int
    unit_price_cents: int
    amount_cents: int

    @property
    def expected_cents(self) -> int:
        return self.quantity * self.unit_price_cents


@dataclass
class ExtractionResult:
    doc_id: str
    doc_type: DocType
    schema_version: str
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    line_items: list[LineItem] = field(default_factory=list)
    reconciliation_passed: bool | None = None
    reconciliation_detail: str = ""
    missing_required: list[str] = field(default_factory=list)
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)


@dataclass
class ReviewItem:
    doc_id: str
    doc_type: DocType
    reason: ReviewReason
    detail: str
    status: ReviewStatus = ReviewStatus.PENDING
    extraction: ExtractionResult | None = None
    decision_by: str = ""
    decision_at: str = ""
    corrections: dict[str, str] = field(default_factory=dict)
    note: str = ""


@dataclass
class AuditEntry:
    seq: int
    timestamp: str
    actor: str
    action: str
    doc_id: str
    detail: str
    prev_hash: str
    entry_hash: str
