"""Pytest configuration: put the src layout on the import path so plain
``pytest`` passes from the project root, with or without an install.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

"""Shared synthetic documents used across the test suite.

All content is invented sample text — no real documents, no real
people or companies.
"""
import pytest

from docintel.audit import AuditLog
from docintel.ingestion import DocumentStore
from docintel.review import ReviewQueue

INVOICE_OK = """Vendor: Northwind Office Supplies
Invoice No: INV-2026-1042
Invoice Date: 2026-10-28
Payment Due: 2026-11-27
PO Number: PO-8814
1. Copy paper A4  10 x $12.50 = $125.00
2. Desk chairs  4 x $149.99 = $599.96
Subtotal: $724.96
Tax: $57.99
Total Due: $782.95
"""

INVOICE_BAD_TOTAL = INVOICE_OK.replace("Total Due: $782.95",
                                       "Total Due: $900.00")

CONTRACT = """Master Services Agreement
This agreement is entered into between Acme Retail Group and BrightPath Consulting Services.
Effective Date: 2026-01-15
Expiry Date: 2027-01-14
Governing Law: California
Termination clause. Confidentiality clause. Payment terms clause. Liability clause.
"""

FORM = """Form Title: Vendor Onboarding Application
Applicant Name: Jane Sample
Email: jane.sample@example.com
Phone: +1 415-555-0132
Mailing Address: 12 Example Street, Springfield
Date of Birth: 1990-04-12
Preferred Contact: Email
"""

IDENTITY = """Document Type: Passport
Document Number: X12345678
Full Name: Jane Sample
Date of Birth: 1990-04-12
Expiry Date: 2030-04-11
Issuing Authority: Example State Department
Nationality: Example
"""

GIBBERISH = "blorp snizzle wumpus quux frobnicate zindle\n"


@pytest.fixture
def store():
    return DocumentStore()


@pytest.fixture
def queue():
    return ReviewQueue()


@pytest.fixture
def audit():
    return AuditLog()


def ingest(store, doc_id, content, mime="text/plain"):
    return store.ingest(doc_id, "test", mime, content, "2026-11-08T00:00:00Z")
