"""Shared helpers used by both the finance ingestion parsers and the
reconciliation reports.
"""
import re
from typing import Any, Optional


def normalize_doc_no(value: Any) -> Optional[str]:
    """Normalizes a QIC document number to one canonical form so
    PaymentTrackerEntry.doc_no joins cleanly against QicSoaLine.doc_no
    despite the two systems formatting the same document differently -
    the payment tracker writes "128-93727", a QIC SOA export writes
    "128 - 93727". Both become "128-93727": collapse any whitespace around
    a hyphen, and strip other stray whitespace.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    text = re.sub(r"\s*-\s*", "-", text)
    return text
