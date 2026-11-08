"""Extraction tests: fields, normalization, reconciliation, schemas."""
from conftest import CONTRACT, FORM, IDENTITY, INVOICE_BAD_TOTAL, INVOICE_OK
from docintel.extraction import (
    cents_to_str,
    extract,
    money_to_cents,
    normalize_date,
)
from docintel.models import CURRENT_SCHEMA_VERSION, DocType


def test_invoice_fields_and_normalization():
    result = extract("d1", DocType.INVOICE, INVOICE_OK)
    assert result.schema_version == CURRENT_SCHEMA_VERSION
    assert result.fields["vendor"].value == "Northwind Office Supplies"
    assert result.fields["invoice_number"].value == "INV-2026-1042"
    assert result.fields["invoice_date"].normalized_value == "2026-10-28"
    assert result.fields["due_date"].normalized_value == "2026-11-27"
    assert result.fields["total_amount"].normalized_value == "78295"
    assert len(result.line_items) == 2
    assert result.line_items[0].description == "Copy paper A4"
    assert result.line_items[0].amount_cents == 12500


def test_invoice_reconciliation_passes():
    result = extract("d1", DocType.INVOICE, INVOICE_OK)
    assert result.reconciliation_passed is True
    assert result.needs_review is False
    assert result.missing_required == []


def test_invoice_bad_total_fails_reconciliation():
    result = extract("d1", DocType.INVOICE, INVOICE_BAD_TOTAL)
    assert result.reconciliation_passed is False
    assert result.needs_review is True
    assert "reconciliation_failed" in result.review_reasons


def test_invoice_line_item_mismatch_detected():
    broken = INVOICE_OK.replace("4 x $149.99 = $599.96",
                                "4 x $149.99 = $500.00")
    # Fix the totals so only the line item is wrong.
    broken = broken.replace("Subtotal: $724.96", "Subtotal: $625.00")
    broken = broken.replace("Tax: $57.99", "Tax: $50.00")
    broken = broken.replace("Total Due: $782.95", "Total Due: $675.00")
    result = extract("d1", DocType.INVOICE, broken)
    assert result.reconciliation_passed is False


def test_missing_required_field_flags_review():
    no_vendor = INVOICE_OK.replace(
        "Vendor: Northwind Office Supplies\n", "")
    result = extract("d1", DocType.INVOICE, no_vendor)
    assert "vendor" in result.missing_required
    assert result.needs_review is True


def test_contract_parties_and_clauses():
    result = extract("d1", DocType.CONTRACT, CONTRACT)
    assert result.fields["party_a"].value == "Acme Retail Group"
    assert result.fields["party_b"].value == "BrightPath Consulting Services"
    assert result.fields["effective_date"].normalized_value == "2026-01-15"
    assert result.fields["expiry_date"].normalized_value == "2027-01-14"
    assert "termination" in result.fields["clauses"].value
    assert result.needs_review is False


def test_form_fields_and_pii_flags():
    result = extract("d1", DocType.FORM, FORM)
    assert result.fields["applicant_name"].value == "Jane Sample"
    assert result.fields["applicant_name"].is_pii is True
    assert result.fields["email"].value == "jane.sample@example.com"
    assert result.fields["email"].is_pii is True
    assert result.fields["date_of_birth"].normalized_value == "1990-04-12"
    assert result.fields["preferred_contact"].confidence == 0.7


def test_identity_fields():
    result = extract("d1", DocType.IDENTITY, IDENTITY)
    assert result.fields["document_number"].value == "X12345678"
    assert result.fields["document_number"].is_pii is True
    assert result.fields["full_name"].value == "Jane Sample"
    assert result.fields["expiry_date"].normalized_value == "2030-04-11"
    assert result.needs_review is False


def test_money_and_date_normalization():
    assert money_to_cents("$1,234.56") == 123456
    assert money_to_cents("782.95") == 78295
    assert cents_to_str(78295) == "782.95"
    assert normalize_date("2026-10-28") == "2026-10-28"
    assert normalize_date("10/28/2026") == "2026-10-28"
    assert normalize_date("28 Oct 2026") == "2026-10-28"
    assert normalize_date("not a date") == "not a date"
