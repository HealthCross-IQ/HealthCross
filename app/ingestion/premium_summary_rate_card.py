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

# "Category A Male" was the only spelling accepted, so "Category A -
# Male" - the one MPH's card uses, and the commoner of the two - parsed
# as nothing and the grid came back empty. Insurers write this at least
# five ways, and the separator is never the meaningful part: the
# category token and the gender word are.
_CATEGORY_GENDER_RE = re.compile(
    r"(?:categor(?:y|ies)|cat)?\s*[:\-]?\s*([A-Za-z0-9]+)\s*[-\u2013\u2014(/,:]?\s*(male|female)\b",
    re.IGNORECASE,
)
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


#: Header labels, as insurers actually write them. Only "Category",
#: "Age Band" and "Gross Premium" were accepted, and a card writing
#: "Age Group" or "Annual Premium" - the same grid under different
#: words - reported that it had no rate table at all. All three still
#: have to appear on one row, which is what keeps a benefits sheet with
#: a stray "Premium" column from matching.
_CATEGORY_LABELS = ("category", "categories", "cat", "class", "plan", "tier", "band")
_AGE_LABELS = ("age band", "age bands", "age group", "age range", "age", "ages")
_PREMIUM_LABELS = ("gross premium", "annual premium", "premium", "gross rate",
                   "annual rate", "rate", "gross")


def _label_matches(label: str, options: tuple) -> bool:
    return any(label == o or label.startswith(o + " ") or label.startswith(o + " in ")
               or label.startswith(o + "(") or label.startswith(o + " (")
               for o in options)


def _is_category_label(label: str) -> bool:
    return _label_matches(label, _CATEGORY_LABELS)


def _is_age_band_label(label: str) -> bool:
    return _label_matches(label, _AGE_LABELS)


def _is_premium_label(label: str) -> bool:
    return _label_matches(label, _PREMIUM_LABELS)


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
            if _is_category_label(label):
                category_col = col_idx
            elif _is_age_band_label(label):
                age_band_col = col_idx
            elif _is_premium_label(label):
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
    try:
        # Every sheet, not just the first. An insurer's quote workbook
        # puts the benefits table on the front tab and the premium grid
        # on another, so reading sheet one and giving up reported "no
        # rate table" on a file that plainly contains one. Sheets are
        # tried in order and the first with a readable grid wins, so a
        # single-sheet card behaves exactly as before.
        sheets = pd.read_excel(file, header=None, sheet_name=None)
    except Exception as e:  # noqa: BLE001 - pandas raises many shapes
        # pandas raises ValueError for an unreadable file too, so an
        # opaque "Excel file format cannot be determined" reached the
        # page verbatim. Naming the file as the problem, rather than the
        # engine, is what tells the reader to check what they attached.
        raise ValueError(
            f"Could not read that workbook - it does not open as an Excel file "
            f"({type(e).__name__}: {e}). Check the attachment is the .xlsx the insurer sent."
        )

    # A Plan Details export is a benefits SPEC and carries no premiums at
    # all - it is priced against HealthCross's own rate card by the
    # "Import and quote" control, not read as a rate card here. The two
    # files look alike enough that one lands in the other's box, and a
    # generic "could not find the rate table" sends the reader looking
    # for a layout problem in a file that simply has no prices in it.
    for name, frame in sheets.items():
        labels = {_normalize_label(c) for c in frame.head(3).values.ravel() if not pd.isna(c)}
        if {"benefit name", "benefit value"} <= labels:
            raise ValueError(
                f"That is a Plan Details file - a list of benefits per category, with no premiums "
                f"in it (sheet \"{name}\"). It is priced against the HealthCross rate card by "
                f"\"Import the offer from the pricing tool\" on the New Business Quote tab, which "
                f"reads exactly this layout. This box takes an insurer's PRICE grid instead."
            )

    df = None
    header = None
    for frame in sheets.values():
        found = _find_rate_table_header(frame)
        if found is not None:
            df, header = frame, found
            break
    if header is None:
        df = next(iter(sheets.values())) if sheets else pd.DataFrame()
        # Naming what WAS in the file turns "it did not work" into
        # something an underwriter can act on - most often the sheet is
        # a quote or a census rather than a Premium Summary, and the
        # first few rows say so immediately.
        # Which sheets were looked at, and what the most likely one
        # actually held. A workbook whose premium grid is on a tab this
        # importer cannot read is a different problem from a file that
        # is not a rate card at all, and the sheet names separate them.
        seen = []
        for row_idx in range(min(len(df), 12)):
            cells = [str(c).strip() for c in df.iloc[row_idx].tolist()
                     if not pd.isna(c) and str(c).strip()]
            if cells:
                seen.append(" | ".join(cells[:6]))
        # Where the grid might be, if it is anywhere. Reporting only the
        # first five rows is useless on a sheet whose rate table sits
        # below a page of benefits, so this reports every row that names
        # a premium or an age - which is what the header row must do.
        candidates = []
        for frame in sheets.values():
            for row_idx in range(len(frame)):
                labels = [_normalize_label(c) for c in frame.iloc[row_idx].tolist()
                          if not pd.isna(c)]
                if any(_is_premium_label(x) or _is_age_band_label(x) for x in labels):
                    candidates.append(f"row {row_idx + 1}: "
                                      + " | ".join(x for x in labels if x)[:110])
                if len(candidates) >= 4:
                    break
        raise ValueError(
            "Could not find the rate table on any sheet. This importer needs one row carrying a "
            "category column, an age-band column and a premium column together. "
            f"Sheets read: {', '.join(map(str, sheets)) or '(none)'}. "
            f"The first sheet reads: " + (" // ".join(seen[:5]) if seen else "(empty)")
            + (". Rows mentioning a premium or an age: " + " // ".join(candidates)
               if candidates else ". No row anywhere in the file mentions a premium or an age band, "
               "so this workbook does not appear to contain a rate grid at all.")
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
        raise ValueError(
            f"Found the rate table header on row {header_row + 1}, but no rate rows parsed "
            f"underneath it. Rows need a Category cell naming a category and a gender "
            f"(for example \"Category A - Male\"), an age band, and a numeric premium."
        )

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
