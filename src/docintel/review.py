"""Human review queue: fail-closed routing and audited decisions.

Anything low-confidence, unreconciled, or missing a required field
lands here. A reviewer may accept, correct (field-level overrides,
which feed extractor improvement stats), or reject. Every decision
is recorded with actor and timestamp; the queue never auto-approves.
"""
from __future__ import annotations

from collections import Counter

from docintel.models import (
    ReviewItem,
    ReviewReason,
    ReviewStatus,
)


class ReviewQueue:
    def __init__(self) -> None:
        self.items: dict[str, ReviewItem] = {}
        # field name -> number of times reviewers corrected it.
        self.correction_stats: Counter[str] = Counter()
        self.decision_counts: Counter[str] = Counter()

    def enqueue(self, item: ReviewItem) -> ReviewItem:
        self.items[item.doc_id] = item
        return item

    def pending(self) -> list[ReviewItem]:
        return [i for i in self.items.values()
                if i.status == ReviewStatus.PENDING]

    def get(self, doc_id: str) -> ReviewItem | None:
        return self.items.get(doc_id)

    def decide(self, doc_id: str, decision: ReviewStatus,
               by: str, at: str, corrections: dict[str, str] | None = None,
               note: str = "") -> ReviewItem:
        item = self.items.get(doc_id)
        if item is None:
            raise KeyError(f"no review item for document {doc_id}")
        if item.status != ReviewStatus.PENDING:
            raise ValueError(
                f"review item {doc_id} already decided: {item.status.value}")
        if decision not in (ReviewStatus.ACCEPTED, ReviewStatus.CORRECTED,
                            ReviewStatus.REJECTED):
            raise ValueError(f"invalid review decision: {decision.value}")
        if decision == ReviewStatus.CORRECTED and not corrections:
            raise ValueError(
                "a corrected decision requires at least one correction")
        item.status = decision
        item.decision_by = by
        item.decision_at = at
        item.note = note
        if corrections:
            item.corrections = dict(corrections)
            for name, value in corrections.items():
                self.correction_stats[name] += 1
                if item.extraction is not None:
                    item.extraction.fields[name].value = value
                    item.extraction.fields[name].normalized_value = value
        self.decision_counts[decision.value] += 1
        return item

    def stats(self) -> dict[str, object]:
        return {
            "total": len(self.items),
            "pending": len(self.pending()),
            "decisions": dict(self.decision_counts),
            "most_corrected_fields": self.correction_stats.most_common(5),
        }


def route_to_review(doc_id: str, doc_type, extraction=None,
                    reason: ReviewReason = ReviewReason.LOW_CONFIDENCE,
                    detail: str = "") -> ReviewItem:
    return ReviewItem(doc_id=doc_id, doc_type=doc_type, reason=reason,
                      detail=detail, extraction=extraction)
