"""Ingestion: validate, hash, dedup, and quarantine document envelopes.

Fail-closed: anything unreadable, oversized, of an unsupported type,
or empty is quarantined with a reason instead of entering the
pipeline. A byte-identical re-submission is a duplicate, not a new
document.
"""
from __future__ import annotations

import hashlib

from docintel.models import (
    ALLOWED_MIME_TYPES,
    MAX_DOCUMENT_BYTES,
    DocumentEnvelope,
    IngestStatus,
)

# Marker used by the synthetic fixtures to simulate a corrupt file.
CORRUPT_MARKER = "%%CORRUPT%%"


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class DocumentStore:
    """In-memory envelope store with dedup and quarantine."""

    def __init__(self) -> None:
        self.documents: dict[str, DocumentEnvelope] = {}
        self._by_hash: dict[str, str] = {}
        self.quarantine: list[DocumentEnvelope] = []

    def ingest(
        self,
        doc_id: str,
        source: str,
        mime_type: str,
        content: str,
        received_at: str,
    ) -> DocumentEnvelope:
        digest = content_hash(content)
        envelope = DocumentEnvelope(
            doc_id=doc_id,
            source=source,
            mime_type=mime_type,
            content=content,
            received_at=received_at,
            content_hash=digest,
        )
        reason = self._quarantine_reason(envelope)
        if reason:
            envelope.status = IngestStatus.QUARANTINED
            envelope.quarantine_reason = reason
            self.quarantine.append(envelope)
            self.documents[doc_id] = envelope
            return envelope
        if digest in self._by_hash:
            envelope.status = IngestStatus.DUPLICATE
            envelope.duplicate_of = self._by_hash[digest]
            self.documents[doc_id] = envelope
            return envelope
        self._by_hash[digest] = doc_id
        self.documents[doc_id] = envelope
        return envelope

    @staticmethod
    def _quarantine_reason(envelope: DocumentEnvelope) -> str:
        if not envelope.content.strip():
            return "empty_content"
        if len(envelope.content.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            return "oversize"
        if envelope.mime_type not in ALLOWED_MIME_TYPES:
            return f"unsupported_mime_type:{envelope.mime_type}"
        if CORRUPT_MARKER in envelope.content:
            return "corrupt_content"
        return ""

    def get(self, doc_id: str) -> DocumentEnvelope | None:
        return self.documents.get(doc_id)

    def accepted(self) -> list[DocumentEnvelope]:
        return [d for d in self.documents.values()
                if d.status == IngestStatus.ACCEPTED]

    def stats(self) -> dict[str, int]:
        return {
            "total": len(self.documents),
            "accepted": len(self.accepted()),
            "duplicates": sum(
                1 for d in self.documents.values()
                if d.status == IngestStatus.DUPLICATE),
            "quarantined": len(self.quarantine),
        }
