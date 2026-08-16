"""Tests for app/ingestion/premium_summary_rate_card.py - a small
synthetic spreadsheet built in-test (openpyxl) mirroring the real QIC
"Premium Summary" export's sparse layout (header block in one column
pair, the rate grid starting a few rows further down), which is
commercially sensitive and not committed to the repo.
"""
import io

import openpyxl
import pytest

from app.ingestion.premium_summary_rate_card import (
    lookup_rate,
    parse_age_band_range,
    parse_premium_summary_rate_card,
)


def _write_xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _sample_rows():
    return [
        ["Product", "QIC Dubai - Main Account", None, None, None, None, None, None, None, "Broker", "NASCO DUBAI"],
        ["Policy Holder", "SERVICEPLAN MIDDLE EAST FZ-LLC", None, None, None, None, None, None, None, "Brokerage in %", 0.125],
        [None, None, None, None, None, None, None, None, None, "TPA Fee in AED", 0.065],
        [None, None, None, None, None, None, None, None, None, "Health CROSS", 0.065],
        [None],
        ["New Business Premium 2025"],
        ["Category", "Age Band", "Gross Premium in AED"],
        ["Category A Male", "0-17", 9117],
        ["Category A Male", "18-40", 11664],
        ["Category A Male", "41-59", 23653],
        ["Category A Male", "60+", 40152],
        ["Category A Female", "0-17", 9116],
        ["Category A Female", "18-40", 13611],
        ["Category A Female", "41-59", 22617],
        ["Category A Female", "60+", 37400],
        [None],
        ["Underwriter", "Pradeep/Roshan"],
    ]


def test_parse_age_band_range():
    assert parse_age_band_range("0-17") == (0, 17)
    assert parse_age_band_range("18-40") == (18, 40)
    assert parse_age_band_range("60+") == (60, 999)
    assert parse_age_band_range("not a band") is None
    assert parse_age_band_range(None) is None


def test_parse_premium_summary_rate_card_extracts_the_rate_grid():
    parsed = parse_premium_summary_rate_card(_write_xlsx(_sample_rows()))
    rates = parsed["rates"]
    assert len(rates) == 8
    assert {"category": "A", "gender": "M", "age_low": 0, "age_high": 17, "premium": 9117.0} in rates
    assert {"category": "A", "gender": "F", "age_low": 60, "age_high": 999, "premium": 37400.0} in rates


def test_parse_premium_summary_rate_card_extracts_header_fees():
    parsed = parse_premium_summary_rate_card(_write_xlsx(_sample_rows()))
    assert parsed["fees"] == {
        "commission_pct": pytest.approx(0.125),
        "tpa_fee_pct": pytest.approx(0.065),
        "hc_fee_pct": pytest.approx(0.065),
    }


def test_parse_premium_summary_rate_card_normalizes_non_breaking_spaces_in_labels():
    # The real export writes "Health\xa0CROSS " (non-breaking space,
    # trailing whitespace) rather than a plain "Health CROSS" - a naive
    # .strip().lower() dict-key match misses that entirely.
    rows = _sample_rows()
    for row in rows:
        for i, cell in enumerate(row):
            if cell == "Health CROSS":
                row[i] = "Health\xa0CROSS "
    parsed = parse_premium_summary_rate_card(_write_xlsx(rows))
    assert parsed["fees"]["hc_fee_pct"] == pytest.approx(0.065)


def test_parse_premium_summary_rate_card_missing_header_raises():
    rows = [["Nothing", "Relevant", "Here"], ["No", "Rate", "Table"]]
    with pytest.raises(ValueError):
        parse_premium_summary_rate_card(_write_xlsx(rows))


def test_lookup_rate_matches_category_gender_and_age():
    parsed = parse_premium_summary_rate_card(_write_xlsx(_sample_rows()))
    rates = parsed["rates"]

    assert lookup_rate(rates, "A", "M", 30) == 11664.0
    assert lookup_rate(rates, "a", "M", 30) == 11664.0  # category matching is case-insensitive
    assert lookup_rate(rates, "A", "F", 65) == 37400.0


def test_lookup_rate_returns_none_for_no_match():
    parsed = parse_premium_summary_rate_card(_write_xlsx(_sample_rows()))
    rates = parsed["rates"]

    assert lookup_rate(rates, "B", "M", 30) is None  # no Category B rows
    assert lookup_rate(rates, "A", "M", None) is None  # missing age
    assert lookup_rate(rates, "A", "G", 30) is None  # invalid gender
    assert lookup_rate(rates, None, "M", 30) is None  # missing category
