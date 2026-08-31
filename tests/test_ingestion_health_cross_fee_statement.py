import io

import pandas as pd
import pytest

from app.ingestion.health_cross_fee_statement import parse_health_cross_fee_statement


def _statement_bytes() -> io.BytesIO:
    rows = [
        [None] * 21,
        [None] * 21,
        [None] * 21,
        ["Customer Name", "216331-HEALTH CROSS GROUP FZCO"] + [None] * 19,
        ["Customer Code", "216331"] + [None] * 19,
        ["Date", "31-Jul-2026"] + [None] * 19,
        [None] * 21,
        [
            "Doc No", "Doc Date", "Due Date", "Line of Business", "Policy No", "Assured",
            "Claim No /Participant Name", "Chassis No", "Invoice No", "Report Currency", "Doc Currency",
            "Narration", "Debit FC", "Credit FC", "Debit LC", "Credit LC", "Division",
            "Transaction Type", "Policy From Date", "Policy To Date", "Age Band",
        ],
        [
            "228-116288", "15-SEP-2025", "04-NOV-2025", "Medical", "P2520001886", "Fortis Digital Global Holdings DMCC",
            "-", "-", "-", "AED", "AED", "Pol No: P2520001886", 0, 1426.96, 0, 1426.96, "Dubai Branch",
            "TPA Fee", "06-AUG-2025", "05-AUG-2026", "271 TO 365",
        ],
        [
            "128-173216", "17-APR-2026", "-", "Medical", "P2620001125", "MEETHAQ MANPOWER LLC",
            "-", "-", "-", "AED", "AED", "Pol No: P2620001125", 15440.95, 0, 15440.95, 0, "Dubai Branch",
            "Other Fee", "01-MAR-2026", "28-FEB-2027", "61 TO 90",
        ],
        [
            "726-70514", "01-JAN-2026", "-", "-", "-", "-",
            "-", "-", "-", "AED", "AED", "Bank charge", 145.39, 0, 145.39, 0, "Dubai Branch",
            "-", None, None, None,
        ],
        [None] * 21,
        [None, None, None, None, None, None, None, None, None, None, None, "Total", None, None, 15586.34, 1426.96] + [None] * 5,
        [None, None, None, None, None, None, None, None, None, None, None, "Net Due to You", None, None, None, -14159.38] + [None] * 5,
    ]
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, header=False)
    buf.seek(0)
    return buf


def test_parse_health_cross_fee_statement_locates_header_and_stops_at_totals():
    records = parse_health_cross_fee_statement(_statement_bytes(), "fee_statement.xlsx")
    assert len(records) == 3

    fee_row = records[0]
    assert fee_row["doc_no"] == "228-116288"
    assert fee_row["policy_no"] == "P2520001886"
    assert fee_row["assured_name"] == "Fortis Digital Global Holdings DMCC"
    assert fee_row["credit_amount"] == pytest.approx(1426.96)
    assert fee_row["debit_amount"] == 0.0
    assert fee_row["division"] == "Dubai Branch"
    assert fee_row["transaction_type"] == "TPA Fee"
    assert fee_row["due_date"].isoformat() == "2025-11-04"
    # The statement's true per-file identity comes from its own Customer
    # Code (header block), not the per-row Division - captured on every row.
    assert fee_row["statement_customer_code"] == "216331"

    other_fee_row = records[1]
    assert other_fee_row["debit_amount"] == pytest.approx(15440.95)
    assert other_fee_row["credit_amount"] == 0.0
    assert other_fee_row["statement_customer_code"] == "216331"

    # "-" placeholders (no policy, no assured) become None rather than the literal dash.
    dash_row = records[2]
    assert dash_row["policy_no"] is None
    assert dash_row["assured_name"] is None
    assert dash_row["debit_amount"] == pytest.approx(145.39)


def test_parse_health_cross_fee_statement_raises_when_header_not_found():
    df = pd.DataFrame([["not", "a", "statement"]])
    buf = io.BytesIO()
    df.to_excel(buf, index=False, header=False)
    buf.seek(0)
    with pytest.raises(ValueError):
        parse_health_cross_fee_statement(buf, "fee_statement.xlsx")
