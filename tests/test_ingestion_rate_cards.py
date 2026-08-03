"""Tests for app/ingestion/rate_cards.py - parses HealthCross's own two New
Business rate-card spreadsheets. Uses small synthetic spreadsheets built
in-test (openpyxl) rather than the real rate card, which is commercially
sensitive and not committed to the repo.
"""
import openpyxl
import pytest

from app.ingestion.rate_cards import parse_benefit_variant_option_list, parse_product_pricing_list


def _write_xlsx(tmp_path, name, header, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    path = tmp_path / name
    wb.save(path)
    return path


PRODUCT_PRICING_HEADER = [
    "Product Name", "From Age", "To Age", "Male Price", "Female Price",
    "Married Female Price", "Region", "Network", "TPA", "Zone", "Created Date", "Updated Date",
]

VARIANT_HEADER = [
    "Benefit Name", "Variant Name", "Option Value", "Direction", "Impact Type",
    "Impact Value", "Is Default", "Zone", "Region", "TPA", "Network", "Created Date", "Updated Date",
]


@pytest.fixture()
def product_pricing_xlsx(tmp_path):
    return _write_xlsx(
        tmp_path,
        "pricing.xlsx",
        PRODUCT_PRICING_HEADER,
        [
            ["Bronze", 0, 17, 1000, 1000, "Not Applicable", "Dubai", "Net A", "TPA X", "Worldwide", "2025-01-01", ""],
            ["Bronze", 18, 40, 2000, 2200, "0 (Applicable only for age band 18-50)", "Dubai", "Net A", "TPA X", "Worldwide", "2025-01-01", ""],
            ["Bronze", 18, 40, 2000, 2500, "500 (Applicable only for age band 18-50)", "Abu Dhabi", "Net A", "TPA X", "Worldwide", "2025-01-01", ""],
            # A duplicate row (same key, identical values) re-entered later - should collapse to one.
            ["Bronze", 0, 17, 1000, 1000, "Not Applicable", "Dubai", "Net A", "TPA X", "Worldwide", "2025-02-01", ""],
        ],
    )


@pytest.fixture()
def variant_option_xlsx(tmp_path):
    return _write_xlsx(
        tmp_path,
        "variants.xlsx",
        VARIANT_HEADER,
        [
            ["UAE Benefit", "Annual Limit", "USD 150,000", "Base", "Text", 0, "No", "Worldwide", "Dubai", "TPA X", "Net A", "2025-01-01", ""],
            ["UAE Benefit", "Annual Limit", "USD 500,000", "Upgrade", "Percent", 3, "No", "Worldwide", "Dubai", "TPA X", "Net A", "2025-01-01", ""],
            ["UAE Benefit", "Dental Limit", "Not Covered", "Base", "Text", 0, "No", "Worldwide", "Dubai", "TPA X", "Net A", "2025-01-01", ""],
            ["UAE Benefit", "Dental Limit", "USD 500", "Upgrade", "Fixed", 275, "No", "Worldwide", "Dubai", "TPA X", "Net A", "2025-01-01", ""],
            # Two conflicting Base rows for the same group - the older one
            # (earlier Created Date, no Updated Date) should be dropped.
            ["UAE Benefit", "Pharmacy Limit", "USD 1,000", "Base", "Text", 0, "No", "Worldwide", "Dubai", "TPA X", "Net A", "2025-01-01", ""],
            ["UAE Benefit", "Pharmacy Limit", "Annual Limit", "Base", "Text", 0, "No", "Worldwide", "Dubai", "TPA X", "Net A", "2025-03-01", ""],
        ],
    )


def test_parse_product_pricing_list_extracts_all_fields(product_pricing_xlsx):
    with open(product_pricing_xlsx, "rb") as f:
        rows = parse_product_pricing_list(f, "pricing.xlsx")

    dubai_adult = next(r for r in rows if r["region"] == "Dubai" and r["from_age"] == 18)
    assert dubai_adult["male_price"] == 2000
    assert dubai_adult["female_price"] == 2200
    assert dubai_adult["married_female_surcharge"] == 0.0

    abu_dhabi_adult = next(r for r in rows if r["region"] == "Abu Dhabi")
    assert abu_dhabi_adult["married_female_surcharge"] == 500.0

    child_row = next(r for r in rows if r["from_age"] == 0)
    assert child_row["married_female_surcharge"] is None  # "Not Applicable"


def test_parse_product_pricing_list_drops_stale_duplicate_rows(product_pricing_xlsx):
    with open(product_pricing_xlsx, "rb") as f:
        rows = parse_product_pricing_list(f, "pricing.xlsx")

    child_rows = [r for r in rows if r["from_age"] == 0 and r["region"] == "Dubai"]
    assert len(child_rows) == 1


def test_parse_benefit_variant_option_list_extracts_all_fields(variant_option_xlsx):
    with open(variant_option_xlsx, "rb") as f:
        rows = parse_benefit_variant_option_list(f, "variants.xlsx")

    upgrade = next(r for r in rows if r["variant_name"] == "Annual Limit" and r["direction"] == "Upgrade")
    assert upgrade["option_value"] == "USD 500,000"
    assert upgrade["impact_type"] == "Percent"
    assert upgrade["impact_value"] == 3.0

    dental_upgrade = next(r for r in rows if r["variant_name"] == "Dental Limit" and r["direction"] == "Upgrade")
    assert dental_upgrade["impact_type"] == "Fixed"
    assert dental_upgrade["impact_value"] == 275.0


def test_parse_benefit_variant_option_list_keeps_only_the_most_recent_base_row(variant_option_xlsx):
    with open(variant_option_xlsx, "rb") as f:
        rows = parse_benefit_variant_option_list(f, "variants.xlsx")

    pharmacy_base_rows = [r for r in rows if r["variant_name"] == "Pharmacy Limit" and r["direction"] == "Base"]
    assert len(pharmacy_base_rows) == 1
    assert pharmacy_base_rows[0]["option_value"] == "Annual Limit"  # the 2025-03-01 row, not the 2025-01-01 one
