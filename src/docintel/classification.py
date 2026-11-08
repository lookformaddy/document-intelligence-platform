"""Deterministic rule + keyword-scoring document classifier.

No model, no network: each document type owns a weighted keyword
table, the text is lower-cased and scored, and the winner's
confidence is its share of the top-two scores (with a floor so a
single strong signal is not overstated). Below the review threshold
the document routes to the human review queue instead of being
auto-processed.
"""
from __future__ import annotations

import re

from docintel.models import (
    REVIEW_CONFIDENCE_THRESHOLD,
    ClassificationResult,
    DocType,
)

# keyword -> weight per document type. Weights are deliberately
# skewed toward label lines ("Invoice No:") over generic words.
KEYWORDS: dict[DocType, dict[str, float]] = {
    DocType.INVOICE: {
        "invoice": 3.0, "invoice no": 2.0, "invoice number": 2.0,
        "billed to": 2.0, "payment due": 2.0, "subtotal": 2.0,
        "total due": 2.0, "line item": 1.5, "po number": 1.5,
        "vendor": 1.5, "amount due": 1.5, "tax": 1.0, "remit": 1.0,
        "quantity": 1.0, "unit price": 1.5,
    },
    DocType.CONTRACT: {
        "agreement": 3.0, "whereas": 2.5, "parties": 2.0,
        "governed by": 2.0, "effective date": 1.5, "termination": 1.5,
        "clause": 1.5, "confidentiality": 1.5, "hereby agree": 2.0,
        "signature": 1.0, "jurisdiction": 1.5, "liability": 1.0,
        "non-disclosure": 1.5, "term of this agreement": 2.0,
    },
    DocType.FORM: {
        "application form": 3.0, "please complete": 2.0,
        "applicant": 2.0, "form title": 2.0, "check all that apply": 2.0,
        "date of birth": 1.0, "mailing address": 1.5,
        "please select": 1.0, "field": 1.0, "submit this form": 2.0,
        "section": 1.0, "marital status": 1.5,
    },
    DocType.IDENTITY: {
        "passport": 3.0, "driver's license": 3.0, "driving licence": 3.0,
        "national id": 3.0, "identity document": 3.0,
        "document number": 2.0, "place of birth": 2.0,
        "date of birth": 1.0, "expiry date": 1.5, "issuing authority": 2.0,
        "holder": 1.5, "sex": 1.0,
    },
}


def _score(text: str, keywords: dict[str, float]) -> float:
    lowered = text.lower()
    total = 0.0
    for keyword, weight in keywords.items():
        # Whole-phrase match; count each keyword once per document so
        # a long document cannot inflate its score by repetition.
        if re.search(r"\b" + re.escape(keyword) + r"\b", lowered):
            total += weight
    return total


def classify(doc_id: str, content: str) -> ClassificationResult:
    scores = {doc_type: _score(content, kws)
              for doc_type, kws in KEYWORDS.items()}
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0].value))
    top_type, top_score = ranked[0]
    second_score = ranked[1][1]
    if top_score <= 0:
        return ClassificationResult(
            doc_id=doc_id, doc_type=DocType.OTHER, confidence=0.0,
            scores={t.value: s for t, s in scores.items()},
            needs_review=True, reason="no_matching_keywords",
        )
    if second_score > 0:
        confidence = top_score / (top_score + second_score)
    else:
        confidence = min(1.0, 0.5 + top_score / 12.0)
    needs_review = confidence < REVIEW_CONFIDENCE_THRESHOLD
    return ClassificationResult(
        doc_id=doc_id, doc_type=top_type,
        confidence=round(confidence, 4),
        scores={t.value: s for t, s in scores.items()},
        needs_review=needs_review,
        reason="low_confidence" if needs_review else "",
    )
