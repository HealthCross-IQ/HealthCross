import io

import pandas as pd

from app.ingestion.qic_soa import parse_qic_soa


def _xlsx(df: pd.DataFrame) -> io.BytesIO:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf


def test_parse_qic_soa_debit_credit_columns_shape():
    df = pd.DataFrame(
        [
            {
                "Doc No": "128 - 173361",
                "Tran Code": "128",
                "Doc Dt": "17-APR-2026",
                "Tran Type": "Premium",
                "Doc Due Date": "07/05/2026",
                "Policy No": "P1",
                "Insured Name": "Acme LLC",
                "Currency": "AED",
                "Debit LC": 50434.5,
                "Credit LC": 0,
                "Dr/Cr": "D",
                "Gross Amount": 50434.5,
                "Cust Group Name": "DIRECT",
            }
        ]
    )
    records = parse_qic_soa(_xlsx(df), "soa.xlsx")
    assert len(records) == 1
    row = records[0]
    assert row["doc_no"] == "128-173361"
    assert row["debit_amount"] == 50434.5
    assert row["credit_amount"] == 0.0
    assert row["doc_date"].isoformat() == "2026-04-17"
    # dd/mm/yyyy - due date is after doc date, not before
    assert row["doc_due_date"].isoformat() == "2026-05-07"


def test_parse_qic_soa_signed_amount_shape():
    df = pd.DataFrame(
        [
            {
                "Doc No": "228 - 93037",
                "Tran Code": 228,
                "Doc Dt": "29-MAY-2025",
                "Tran Type": "Cancellation- followup",
                "Policy No": "P2",
                "Insured Name": "Beta LLC",
                "Currency": "AED",
                "AMOUNT": -726436.2,
                "Dr/Cr": "C",
                "Gross Amount": 726436.2,
            }
        ]
    )
    records = parse_qic_soa(_xlsx(df), "soa.xlsx")
    row = records[0]
    assert row["doc_no"] == "228-93037"
    assert row["debit_amount"] == 0.0
    assert row["credit_amount"] == 726436.2


def test_parse_qic_soa_skips_rows_without_doc_no():
    df = pd.DataFrame([{"Doc No": None, "AMOUNT": 100, "Dr/Cr": "D"}])
    records = parse_qic_soa(_xlsx(df), "soa.xlsx")
    assert records == []
