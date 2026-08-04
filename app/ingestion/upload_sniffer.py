"""Best-effort auto-detection of which upload slot a dropped file belongs
in (Census / Benefits / Claims / Quote / Claims ledger) - powers the
case workspace's single drag-drop zone, which lets a broker drop every
file at once instead of picking the right slot for each one by hand.

This is deliberately a GUESS, never a silent commit: the caller always
shows the detected kind back to the user for confirmation before
actually uploading anything (see the "Quick upload" review step in
app/static/index.html), so a wrong guess here costs a dropdown change,
not a mis-filed upload.

Spreadsheet files are sniffed by which canonical column-alias set the
header row matches best (these alias sets are already disjoint enough
in practice - a census's age/gender/nationality columns vs. a claims
ledger's patient_id/diagnosis_code/final_amount columns share almost no
vocabulary). PDFs are sniffed by the same literal text markers the real
parsers themselves key off (see app/ingestion/claims_report.py and
app/ingestion/quote_pdf.py), falling back to "benefits" as the single
most common case-level PDF upload when nothing else matches.
"""
from typing import BinaryIO, Dict, List, Optional

import pandas as pd
import pdfplumber

from app.ingestion.claims import CLAIMS_ALIASES
from app.ingestion.claims_ledger import CLAIMS_LEDGER_ALIASES
from app.ingestion.census import CENSUS_ALIASES
from app.ingestion.column_mapping import map_columns

# A spreadsheet's kind is decided by which of these alias sets its header
# row matches the MOST columns against - checked in this order only to
# break exact ties (claims ledger's patient_id/diagnosis_code is a more
# specific, rarer vocabulary than a plain claims sheet's, so it's given
# first refusal on a tie rather than the more generic set swallowing it).
_SPREADSHEET_ALIAS_SETS = [
    ("census", CENSUS_ALIASES),
    ("claims-ledger", CLAIMS_LEDGER_ALIASES),
    ("claims", CLAIMS_ALIASES),
]

# Below this many matched canonical columns, a spreadsheet's kind is too
# uncertain to guess at all - e.g. a near-empty or malformed file matching
# only one alias by coincidence.
_MIN_CONFIDENT_COLUMN_MATCHES = 3


def _sniff_spreadsheet(file: BinaryIO, filename: str) -> Dict[str, Optional[str]]:
    lower_name = filename.lower()
    try:
        if lower_name.endswith(".csv"):
            df = pd.read_csv(file, nrows=5)
        else:
            df = pd.read_excel(file, nrows=5, engine="calamine")
    except Exception:
        return {"detected_kind": None, "confidence": "low", "reason": "Could not read this file as a spreadsheet."}

    best_kind, best_count = None, 0
    for kind, alias_map in _SPREADSHEET_ALIAS_SETS:
        mapped = map_columns(df, alias_map)
        matched = sum(1 for canonical in alias_map if canonical in mapped.columns)
        if matched > best_count:
            best_kind, best_count = kind, matched

    if best_kind is None or best_count < _MIN_CONFIDENT_COLUMN_MATCHES:
        return {
            "detected_kind": None,
            "confidence": "low",
            "reason": f"Only recognized {best_count} column(s) - not enough to guess confidently.",
        }
    confidence = "high" if best_count >= 5 else "low"
    return {
        "detected_kind": best_kind,
        "confidence": confidence,
        "reason": f"Recognized {best_count} column(s) matching a {best_kind} file's usual layout.",
    }


def _sniff_pdf(file: BinaryIO, filename: str) -> Dict[str, Optional[str]]:
    try:
        with pdfplumber.open(file) as pdf:
            sample_pages = pdf.pages[:2]
            text = " ".join((page.extract_text() or "") for page in sample_pages)
    except Exception:
        return {"detected_kind": None, "confidence": "low", "reason": "Could not read this PDF's first pages."}

    upper_text = text.upper()
    if "HEALTH INSURANCE CLAIMS RECORD" in upper_text or "DHA MANDATED FORMAT" in upper_text:
        return {
            "detected_kind": "claims",
            "confidence": "high",
            "reason": "Found the DHA claims utilization report's own header text.",
        }
    if "FULL CATEGORY PREMIUM CALCULATION" in upper_text:
        return {
            "detected_kind": "quote",
            "confidence": "high",
            "reason": "Found the category-premium quote table's own header text.",
        }
    if not text.strip():
        # A scanned/image-only PDF - the existing per-endpoint OCR fallback
        # already handles this fine once uploaded as Benefits (the far more
        # common scanned upload at case level); flagged as low-confidence
        # since a scanned claims report or quote would look the same here.
        return {
            "detected_kind": "benefits",
            "confidence": "low",
            "reason": "This PDF appears to be a scanned image - couldn't check its text for a more specific match.",
        }
    return {
        "detected_kind": "benefits",
        "confidence": "low",
        "reason": "Didn't match a known claims-report or quote layout - most case-level PDFs are a table of benefits.",
    }


def sniff_upload_kind(file: BinaryIO, filename: str) -> Dict[str, Optional[str]]:
    """Returns {"detected_kind", "confidence", "reason"}. detected_kind is
    one of "census"/"benefits"/"claims"/"claims-ledger"/"quote", or None
    when nothing matched with any confidence at all - the caller always
    shows this back to the user to confirm or correct before uploading,
    never commits directly off of it.
    """
    file.seek(0)
    try:
        lower_name = filename.lower()
        if lower_name.endswith((".xlsx", ".xls", ".csv")):
            return _sniff_spreadsheet(file, filename)
        if lower_name.endswith(".pdf"):
            return _sniff_pdf(file, filename)
        return {"detected_kind": None, "confidence": "low", "reason": f"Unrecognized file type for {filename!r}."}
    finally:
        file.seek(0)
