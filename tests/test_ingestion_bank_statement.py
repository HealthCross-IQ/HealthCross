import io

import pandas as pd
import pytest

from app.ingestion.bank_statement import parse_bank_statement


def _statement_bytes() -> io.BytesIO:
    rows = [
        [None] * 7,
        ["Account transactions Statement Report", None, None, None, None, None, None],
        [None] * 7,
        ["Account Holder Name", "HEALTH CROSS GROUP FZCO", None, None, None, None, None],
        ["Account Number", "019101729732", None, None, None, None, None],
        ["Account Currency", "AED", None, None, None, None, None],
        [None] * 7,
        ["Date", "Value Date", "Reference Number", "Description", "Credit", "Debit", "Balance"],
        ["01 Jul 2026", "01 Jul 2026", "REF1", "IPP TRANSFER - SOFTWARE FEE", "", "-175.00", "422,583.19"],
        ["24 Jul 2026", "24 Jul 2026", "REF2", "Inward Remittance QATAR INSURANCE CO.", "297,313.93", "", "717,128.56"],
    ]
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, header=False)
    buf.seek(0)
    return buf


def test_parse_bank_statement_locates_header_below_metadata_block():
    records = parse_bank_statement(_statement_bytes(), "statement.xlsx")
    assert len(records) == 2
    assert records[0]["account_number"] == "019101729732"
    assert records[0]["currency"] == "AED"
    assert records[0]["debit_amount"] == 175.0
    assert records[0]["credit_amount"] == 0.0
    assert records[0]["balance"] == pytest.approx(422583.19)

    qic_row = records[1]
    assert qic_row["credit_amount"] == pytest.approx(297313.93)
    assert qic_row["debit_amount"] == 0.0
    assert qic_row["txn_date"].isoformat() == "2026-07-24"


def test_parse_bank_statement_raises_when_header_not_found():
    df = pd.DataFrame([["not", "a", "statement"]])
    buf = io.BytesIO()
    df.to_excel(buf, index=False, header=False)
    buf.seek(0)
    with pytest.raises(ValueError):
        parse_bank_statement(buf, "statement.xlsx")
