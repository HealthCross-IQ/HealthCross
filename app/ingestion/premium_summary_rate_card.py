"""Parses a QIC/broker "Premium Summary" export (e.g.
Premium_summary_<company>.xls) - the rate-card grid an insurer issues
alongside a policy, priced by Category x Age Band x Gender, plus its own
header block of acquisition-cost fee percentages (Brokerage/TPA/HC).

Used to bulk-fill each census member's own existing_annual_rate on the
Member Rates screen (app/api/routes_analysis.py's
import_member_rate_card) by matching the case's own census rows against
this rate card via category/gender/age, instead of typing well over a
hundred rates in by hand. Layout is discovered rather than assumed at a
fixed row/column, since different exports pad the header block
differently.
"""
import re
from typing import BinaryIO, Dict, List, Optional, Tuple

import pandas as pd

_CATEGORY_GENDER_RE = re.compile(r"category\s+(\S+)\s+(male|female)", re.IGNORECASE)
_AGE_RANGE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")
_AGE_PLUS_RE = re.compile(r"^\s*(\d+)\s*\+\s*$")

# The file's own header block spells these out in plain English next to
# a fraction (e.g. "Brokerage in %" -> 0.125), not lined up with the
# rate grid's columns - matched by label text wherever it falls, since
# its row/column position varies between exports.
_FEE_LABEL_TO_CASE_FIELD = {
    "brokerage in %": "commission_pct",
    "tpa fee in aed": "tpa_fee_pct",  # mislabeled unit in the source file - it's actually a fraction like the others
    "health cross": "hc_fee_pct",
}


def _normalize_label(value: object) -> str:
    """Lowercased, whitespace-collapsed label text - real exports carry
    non-breaking spaces (\\xa0) and embedded newlines inside header
    labels (e.g. "Health\\xa0CROSS ", "Gross Premium in  AED\\n"), which
    .strip().lower() alone doesn't remove, so a literal dict-key match
    against a normal space would silently miss them."""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def parse_age_band_range(label: str) -> Optional[Tuple[int, int]]:
    """"0-17" -> (0, 17), "60+" -> (60, 999). None for anything else, so
    an unrecognized band is skipped rather than mis-binned."""
    if not isinstance(label, str):
        return None
    match = _AGE_RANGE_RE.match(label)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = _AGE_PLUS_RE.match(label)
    if match:
        return int(match.group(1)), 999
    return None


def _find_rate_table_header(df: pd.DataFrame) -> Optional[Tuple[int, int, int, int]]:
    """Scans for the row carrying "Category"/"Age Band"/"Gross Premium"
    column labels - returns (header_row, category_col, age_band_col,
    premium_col), or None if no such row is found."""
    for row_idx in range(len(df)):
        category_col = age_band_col = premium_col = None
        for col_idx in range(df.shape[1]):
            cell = df.iat[row_idx, col_idx]
            if pd.isna(cell):
                continue
            label = _normalize_label(cell)
            if label == "category":
                category_col = col_idx
            elif label.startswith("age band"):
                age_band_col = col_idx
            elif label.startswith("gross premium"):
                premium_col = col_idx
        if category_col is not None and age_band_col is not None and premium_col is not None:
            return row_idx, category_col, age_band_col, premium_col
    return None


def _extract_fee_pcts(df: pd.DataFrame) -> Dict[str, float]:
    fees: Dict[str, float] = {}
    for row_idx in range(len(df)):
        for col_idx in range(df.shape[1] - 1):
            cell = df.iat[row_idx, col_idx]
            if pd.isna(cell):
                continue
            label = _normalize_label(cell)
            field = _FEE_LABEL_TO_CASE_FIELD.get(label)
            if not field or field in fees:
                continue
            value_cell = df.iat[row_idx, col_idx + 1]
            if pd.isna(value_cell):
                continue
            try:
                fees[field] = float(value_cell)
            except (TypeError, ValueError):
                continue
    return fees


def parse_premium_summary_rate_card(file: BinaryIO) -> dict:
    """Returns {"rates": [{"category", "gender", "age_low", "age_high",
    "premium"}, ...], "fees": {"tpa_fee_pct"/"commission_pct"/
    "hc_fee_pct": float}} - fees is whatever subset the file's header
    actually states, never invented.
    """
    df = pd.read_excel(file, header=None)

    header = _find_rate_table_header(df)
    if header is None:
        raise ValueError(
            "Could not find the rate table (looking for a row with Category/Age Band/Gross Premium columns)"
        )
    header_row, category_col, age_band_col, premium_col = header

    rates = []
    for row_idx in range(header_row + 1, len(df)):
        category_cell = df.iat[row_idx, category_col]
        if pd.isna(category_cell):
            break  # the grid ends at the first blank Category cell
        match = _CATEGORY_GENDER_RE.search(str(category_cell))
        if not match:
            continue
        age_range = parse_age_band_range(df.iat[row_idx, age_band_col])
        premium_cell = df.iat[row_idx, premium_col]
        if age_range is None or pd.isna(premium_cell):
            continue
        try:
            premium = float(premium_cell)
        except (TypeError, ValueError):
            continue
        rates.append({
            "category": match.group(1).upper(),
            "gender": "M" if match.group(2).lower() == "male" else "F",
            "age_low": age_range[0],
            "age_high": age_range[1],
            "premium": premium,
        })

    if not rates:
        raise ValueError("Rate table header found, but no rate rows parsed underneath it")

    return {"rates": rates, "fees": _extract_fee_pcts(df)}


def lookup_rate(rates: List[dict], category: Optional[str], gender: Optional[str], age: Optional[int]) -> Optional[float]:
    """The census's own category/gender/age decide the match - None
    (not a fallback rate) whenever any of them is missing or doesn't
    fall inside any parsed row, so a gap is left visible rather than
    silently filled with a wrong number."""
    if not category or gender not in ("M", "F") or age is None:
        return None
    normalized_category = str(category).strip().upper()
    for rate in rates:
        if rate["category"] != normalized_category or rate["gender"] != gender:
            continue
        if rate["age_low"] <= age <= rate["age_high"]:
            return rate["premium"]
    return None
