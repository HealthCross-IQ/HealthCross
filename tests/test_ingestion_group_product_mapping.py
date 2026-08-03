import openpyxl
import pytest

from app.ingestion.group_product_mapping import parse_group_product_mapping


@pytest.fixture()
def mapping_xlsx(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Subgroup", "Master Group", "Product"])
    ws.append(["Acme Sub LLC", "Acme Holdings", "Bronze"])
    # A row naming only the master group (no distinct sub-group) still
    # produces a usable mapping entry.
    ws.append([None, "Umbrella Co", "Gold"])
    path = tmp_path / "mapping.xlsx"
    wb.save(path)
    return path


def test_produces_one_row_per_populated_group_column(mapping_xlsx):
    with open(mapping_xlsx, "rb") as f:
        rows = parse_group_product_mapping(f, "mapping.xlsx")
    by_name = {r["group_name"]: r["product"] for r in rows}
    assert by_name == {
        "Acme Sub LLC": "Bronze",
        "Acme Holdings": "Bronze",
        "Umbrella Co": "Gold",
    }


def test_skips_rows_with_no_product(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Subgroup", "Master Group", "Product"])
    ws.append(["Acme Sub LLC", "Acme Holdings", None])
    path = tmp_path / "mapping2.xlsx"
    wb.save(path)
    with open(path, "rb") as f:
        rows = parse_group_product_mapping(f, "mapping2.xlsx")
    assert rows == []
