"""Classification tests: deterministic keyword scoring."""
from conftest import CONTRACT, FORM, GIBBERISH, IDENTITY, INVOICE_OK
from docintel.classification import classify
from docintel.models import DocType


def test_invoice_classified_with_confidence():
    result = classify("d1", INVOICE_OK)
    assert result.doc_type == DocType.INVOICE
    assert result.confidence >= 0.60
    assert result.needs_review is False


def test_contract_classified():
    result = classify("d1", CONTRACT)
    assert result.doc_type == DocType.CONTRACT
    assert result.needs_review is False


def test_form_classified():
    result = classify("d1", FORM)
    assert result.doc_type == DocType.FORM
    assert result.needs_review is False


def test_identity_classified():
    result = classify("d1", IDENTITY)
    assert result.doc_type == DocType.IDENTITY
    assert result.needs_review is False


def test_gibberish_routes_to_review():
    result = classify("d1", GIBBERISH)
    assert result.doc_type == DocType.OTHER
    assert result.confidence == 0.0
    assert result.needs_review is True


def test_ambiguous_text_has_low_confidence():
    # One invoice keyword and one contract keyword: neither dominates.
    text = "Invoice received. This agreement covers the payment."
    result = classify("d1", text)
    assert result.needs_review is True
    assert result.confidence < 0.60


def test_scores_are_deterministic():
    first = classify("d1", INVOICE_OK)
    second = classify("d1", INVOICE_OK)
    assert first.scores == second.scores
    assert first.confidence == second.confidence
