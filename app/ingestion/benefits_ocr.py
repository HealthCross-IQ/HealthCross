"""OCR-based fallback for table-of-benefits PDFs that are scanned images -
no extractable text or table lines for the normal pdfplumber-based parser
(app/ingestion/benefits_pdf.py) to work with at all.

OCR is meaningfully less reliable than the text-based parser: real-world
scans produce misread digits (the same figure can OCR as "29,400" on one
pass and "29,440" on another), garbled table headers, and column content
that bleeds together. Rather than pretend precision this technique doesn't
have, this module:
  - extracts full per-page OCR text (reliable, always returned) for a human
    to search/verify against the source PDF,
  - does best-effort label-anchored value extraction for a curated set of
    fields, returning EVERY distinct value found near a label rather than
    guessing which plan tier/column it belongs to - OCR reading order
    across side-by-side table columns isn't trustworthy enough to assign
    confidently,
  - never silently presents a single number as ground truth when the
    surrounding text is ambiguous.

Always spot-check numbers this module produces against the source document.
"""
import os
import re
import shutil
from typing import Any, BinaryIO, Dict, List, Optional

import pdfplumber
import pytesseract

# pytesseract shells out to a `tesseract` command on PATH by default. The
# official Windows installer doesn't reliably add itself to PATH, and asking
# a non-technical user to edit their Windows PATH by hand is its own source
# of errors - so fall back to Tesseract's own default install locations (or
# an explicit TESSERACT_CMD env var override) before giving up.
_WINDOWS_DEFAULT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


def _configure_tesseract_cmd() -> None:
    if shutil.which("tesseract"):
        return
    env_override = os.environ.get("TESSERACT_CMD")
    for candidate in ([env_override] if env_override else []) + _WINDOWS_DEFAULT_PATHS:
        if candidate and os.path.isfile(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            return


_configure_tesseract_cmd()

# label -> regex to search for in the flattened OCR text of the whole document
_FIELD_LABEL_PATTERNS = {
    "annual_limit": r"Indemnity Limit",
    "area_of_cover": r"Basic Territory for Elective\s*&\s*Emergency treatment",
    # Negative lookbehind excludes the maternity-specific "Outpatient
    # Ante/Post Natal Consultation Deductible / Coinsurance" row, which
    # shares this same wording for a different (and usually differently
    # valued) benefit elsewhere in the document.
    "deductible": r"(?<!Natal )Consultation Deductible\s*/?\s*Coinsurance",
    "pre_existing_chronic_limit": r"Pre-existing conditions",
    "maternity_limit": r"Normal Delivery",
    "dental": r"Dental Benefit",
    "optical": r"Optical Benefit",
    "coinsurance": r"Outpatient Co-Insurance|(?<!Natal )Consultation Deductible\s*/?\s*Coinsurance",
    "alternative_or_complementary_treatment": r"Alternative Medicine Co-Insurance|Enhanced Alternative Medicine",
    "pharmacy_limit_and_coinsurance": r"Prescribed Pharmaceuticals",
    "health_screening_wellness": r"Health Check\s*/?\s*Wellness Package|Wellness Package|Health Screening",
}

# The original pattern only matched amounts with at least one comma group
# (e.g. "5,520,000/-"), silently missing smaller AED amounts with no comma
# at all (e.g. "AED 50/-") - the first alternative below covers those too,
# as long as "AED" is present to distinguish a real amount from a stray
# small number; the second alternative keeps the original comma-grouped
# match for amounts that appear with no "AED" prefix at all.
_MONEY_RE = re.compile(r"AED\s*[\d]{1,3}(?:,\d{3})*(?:/-)?|[\d]{1,3}(?:,\d{3})+(?:/-)?")
# A co-insurance/deductible value is often "20% up to a maximum of AED
# 50/-" rather than a bare amount - tried first so the rate isn't dropped
# in favor of just the capped AED figure.
_PCT_MONEY_RE = re.compile(r"\d{1,3}%[^%\d]{0,40}?(?:" + _MONEY_RE.pattern + r")")
_COVERED_RE = re.compile(r"\bNot Covered\b|\bCovered\b", re.IGNORECASE)
_WORD_RE = re.compile(r"\S+")


def is_scanned_pdf(file: BinaryIO) -> bool:
    """True if none of the PDF's pages have any extractable text at all -
    i.e. every page is a raster image with no underlying text layer."""
    file.seek(0)
    try:
        with pdfplumber.open(file) as pdf:
            has_text = any((page.extract_text() or "").strip() for page in pdf.pages[:5])
    finally:
        file.seek(0)
    return not has_text


def ocr_pdf_pages(file: BinaryIO, resolution: int = 300) -> List[str]:
    """Returns OCR'd text for each page, in page order."""
    pages_text = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            image = page.to_image(resolution=resolution).original
            pages_text.append(pytesseract.image_to_string(image))
    return pages_text


def _nearby_text_value(window_text: str, max_words: int = 8) -> Optional[str]:
    """Fallback for a nearby value that's neither a currency amount nor a
    Covered/Not Covered flag - e.g. area of cover's "Worldwide Exc (USA)".
    Two side-by-side plan columns sharing the same value (common when a
    benefit doesn't differ between tiers) OCRs as that phrase repeated
    twice back-to-back, so this collapses an exact immediate repeat down
    to a single copy rather than reporting the duplicate.
    """
    words = _WORD_RE.findall(window_text)[:max_words]
    if not words:
        return None
    for split in range(1, len(words)):
        first_half = " ".join(words[:split])
        second_half = " ".join(words[split : split * 2])
        if second_half and first_half == second_half:
            return first_half
    return " ".join(words)


def _nearby_values(flat_text: str, label_pattern: str, window: int = 200) -> List[str]:
    values: List[str] = []
    for match in re.finditer(label_pattern, flat_text, re.IGNORECASE):
        window_text = flat_text[match.end() : match.end() + window]
        pct_money = _PCT_MONEY_RE.findall(window_text)
        if pct_money:
            values.extend(m.strip() for m in pct_money)
            continue
        money = _MONEY_RE.findall(window_text)
        if money:
            values.extend(m.strip() for m in money)
            continue
        covered = _COVERED_RE.search(window_text)
        if covered:
            values.append(covered.group(0))
            continue
        text_value = _nearby_text_value(window_text)
        if text_value:
            values.append(text_value)

    seen = set()
    unique = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def build_ocr_benefit_summary(pages_text: List[str]) -> Dict[str, str]:
    """Best-effort standard-field summary from OCR'd text.

    A field with exactly one distinct value nearby is reported as-is. A
    field with multiple distinct values is reported as all of them,
    joined, with an explicit note that column order couldn't be trusted -
    the caller should treat that as "needs manual verification", not as a
    confident multi-tier breakdown.
    """
    flat = " ".join(" ".join(page.split()) for page in pages_text)
    summary: Dict[str, str] = {}
    for field, pattern in _FIELD_LABEL_PATTERNS.items():
        values = _nearby_values(flat, pattern)
        if not values:
            continue
        if len(values) == 1:
            summary[field] = values[0]
        else:
            summary[field] = (
                " / ".join(values) + " (multiple values found near this label - verify against the source PDF)"
            )
    return summary


def parse_benefits_pdf_ocr(file: BinaryIO, filename: str) -> Dict[str, Any]:
    pages_text = ocr_pdf_pages(file)
    return {
        "summary": build_ocr_benefit_summary(pages_text),
        "raw_ocr_text": "\n\n".join(f"--- page {i + 1} ---\n{text}" for i, text in enumerate(pages_text)),
    }
