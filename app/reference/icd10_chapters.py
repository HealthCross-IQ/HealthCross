"""Maps an ICD-10 diagnosis code to its broad chapter, using the same
chapter labels as app/reference/diagnosis_classification.py so a raw
per-claim ledger's line-level ICD-10 codes (e.g. "J454", "F418") can reuse
that module's chronic/non-chronic classification, which was originally
keyed to DHA claims-report style pre-aggregated groupings rather than raw
codes.

Chapters are contiguous letter+number spans (e.g. C00-D49 for Neoplasms),
so comparison is done on the (letter, number) tuple - this correctly
handles a span crossing letters (A00-B99, S00-T98) since Python compares
tuples lexicographically.
"""
import re
from typing import Optional, Tuple

# (start, end, chapter label) - label matches an existing
# DIAGNOSIS_CLASSIFICATION key where the chapter already has an entry
# there; otherwise it's a new chapter added to that table.
_ICD10_CHAPTERS = [
    ("A00", "B99", "certain infectious and parasitic diseases"),
    ("C00", "D49", "neoplasms"),
    ("D50", "D89", "diseases of blood and blood-forming organs"),
    ("E00", "E90", "endocrine, nutritional, metabolic, immunity"),
    ("F01", "F99", "mental and behavioural disorders"),
    ("G00", "G99", "diseases of nervous system and sense organs"),
    ("H00", "H95", "diseases of nervous system and sense organs"),
    ("I00", "I99", "diseases of circulatory system"),
    ("J00", "J99", "diseases of respiratory system"),
    ("K00", "K95", "diseases of digestive system"),
    ("L00", "L99", "diseases of the skin and subcutaneous tissue"),
    ("M00", "M99", "diseases of musculoskeletal system and tissues"),
    ("N00", "N99", "diseases of genitourinary system"),
    ("O00", "O99", "pregnancy, childbirth and the puerperium"),
    ("P00", "P96", "certain conditions originating in the perinatal period"),
    ("Q00", "Q99", "congenital malformations"),
    ("R00", "R99", "symptoms, signs and ill-defined conditions"),
    ("S00", "T98", "injury, poisoning and external causes"),
    ("Z00", "Z99", "factors influencing health status"),
]


def _code_key(code_prefix: str) -> Tuple[str, int]:
    return (code_prefix[0], int(code_prefix[1:]))


def icd10_chapter(code: Optional[str]) -> Optional[str]:
    """Return the broad chapter label for an ICD-10 code, or None if the
    code doesn't parse as a letter + 2-digit prefix (e.g. blank/malformed)."""
    if not code:
        return None
    match = re.match(r"^([A-Za-z])(\d{1,2})", str(code).strip())
    if not match:
        return None
    key = (match.group(1).upper(), int(match.group(2)))
    for low, high, label in _ICD10_CHAPTERS:
        if _code_key(low) <= key <= _code_key(high):
            return label
    return None
