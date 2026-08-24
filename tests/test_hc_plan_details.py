"""The pricing tool's own Plan Details export -
app/ingestion/hc_plan_details.py.
"""
import io

import openpyxl
import pytest

from app.ingestion.hc_plan_details import parse_hc_plan_details, unmatched_selections

HEADER = ["Category", "Emirates", "TPA", "Network", "Zone", "Product", "Benefit Name", "Benefit Value"]


def _xlsx(rows, header=HEADER):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# The real Haworth shape: header on the first row of each block, blank on
# the rest, a blank separator row between categories.
HAWORTH = [
    ["A", "DXB", "MSH MENA", "\tMSH Platinum", "Worldwide Excluding USA", "Gold", "Annual Limit", "USD 1,000,000"],
    [None, None, None, None, None, None, "Deductible", "NIL"],
    [None, None, None, None, None, None, "Dental Limit", "USD 2,000"],
    [None, None, None, None, None, None, "Dental Copay", "20%"],
    [None, None, None, None, None, None, None, None],
    ["B", "DXB", "MSH MENA", "\tMSH Platinum", "Worldwide Excluding USA", "Gold", "Annual Limit", "USD 1,000,000"],
    [None, None, None, None, None, None, "Deductible", "20% MAX AED 50"],
]


def test_a_block_header_carries_down_its_own_rows():
    # Only the first row of a block names the category - a human
    # convention that would attribute every later benefit to nothing.
    parsed = parse_hc_plan_details(_xlsx(HAWORTH), "plan.xlsx")
    assert parsed["category_count"] == 2
    a, b = parsed["categories"]
    assert a["category"] == "A"
    assert len(a["variant_selections"]) == 4
    assert b["category"] == "B"


def test_categories_keep_their_own_differing_selections():
    parsed = parse_hc_plan_details(_xlsx(HAWORTH), "plan.xlsx")
    a, b = parsed["categories"]
    assert a["variant_selections"]["Deductible"] == "NIL"
    assert b["variant_selections"]["Deductible"] == "20% MAX AED 50"


def test_the_stray_tab_a_spreadsheet_export_adds_is_stripped():
    # Network arrives as "\tMSH Platinum" often enough that matching it
    # raw fails against every rate card row.
    parsed = parse_hc_plan_details(_xlsx(HAWORTH), "plan.xlsx")
    assert parsed["categories"][0]["network"] == "MSH Platinum"


def test_the_block_header_fields_are_all_captured():
    a = parse_hc_plan_details(_xlsx(HAWORTH), "plan.xlsx")["categories"][0]
    assert a["product"] == "Gold"
    assert a["tpa"] == "MSH MENA"
    assert a["emirates"] == "DXB"
    assert a["zone"] == "Worldwide Excluding USA"


def test_blank_separator_rows_do_not_invent_selections():
    # Forward-filling the whole frame rather than just the header columns
    # would repeat the last benefit name into the separator row.
    parsed = parse_hc_plan_details(_xlsx(HAWORTH), "plan.xlsx")
    assert all(None not in c["variant_selections"] for c in parsed["categories"])
    assert "" not in parsed["categories"][0]["variant_selections"]
    assert len(parsed["categories"][1]["variant_selections"]) == 2


def test_a_file_that_is_not_a_plan_details_export_says_so():
    other = _xlsx([["x", "y"]], header=["Something", "Else"])
    with pytest.raises(ValueError, match="Not a Plan Details export"):
        parse_hc_plan_details(other, "wrong.xlsx")


def test_a_benefit_named_twice_takes_the_last_value():
    rows = [
        ["A", "DXB", "T", "N", "Z", "Gold", "Dental Limit", "USD 1,000"],
        [None, None, None, None, None, None, "Dental Limit", "USD 2,000"],
    ]
    parsed = parse_hc_plan_details(_xlsx(rows), "plan.xlsx")
    assert parsed["categories"][0]["variant_selections"]["Dental Limit"] == "USD 2,000"


# --- selections the rate card cannot price ------------------------------

def test_an_option_the_card_does_not_price_is_reported_not_dropped():
    # It does not fail - pricing falls back to the base option and the
    # quote looks fine while being for a different plan than the export.
    issues = unmatched_selections(
        [{"category": "A", "variant_selections": {"Dental Limit": "USD 9,999"}}],
        {"Dental Limit": ["USD 1,000", "USD 2,000"]},
    )
    assert len(issues) == 1
    assert "base option would be used" in issues[0]["reason"]
    assert issues[0]["option_value"] == "USD 9,999"


def test_a_variant_the_card_does_not_have_at_all_is_reported():
    issues = unmatched_selections(
        [{"category": "A", "variant_selections": {"Invented Benefit": "X"}}],
        {"Dental Limit": ["USD 2,000"]},
    )
    assert issues[0]["reason"].startswith("no such benefit variant")


def test_selections_the_card_does_price_raise_nothing():
    issues = unmatched_selections(
        [{"category": "A", "variant_selections": {"Dental Limit": "USD 2,000"}}],
        {"Dental Limit": ["USD 1,000", "USD 2,000"]},
    )
    assert issues == []


def test_matching_ignores_case_and_stray_whitespace():
    issues = unmatched_selections(
        [{"category": "A", "variant_selections": {" dental limit ": "usd 2,000"}}],
        {"Dental Limit": ["USD 2,000"]},
    )
    assert issues == []
