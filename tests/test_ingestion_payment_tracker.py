import io

import pandas as pd

from app.ingestion.payment_tracker import parse_payment_tracker


def _xlsx(df: pd.DataFrame) -> io.BytesIO:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf


def test_parse_payment_tracker_numeric_fee_and_channel_inference():
    df = pd.DataFrame(
        [
            {
                "Source": "Direct Channel",
                "Policy No.": "P123",
                "DocNo.": "128-11111",
                "Client  Code": 12345,
                "Premium\n( excl VAT)": 100000.0,
                "Client premium Amount\n( excl tax)": 100000.0,
                "Product": "Silver",
                "HC Fee %": 0.115,
                "HC Fees": 11500.0,
                "VAT 5%": 575.0,
                "Total Value": 12075.0,
                "HC Payment Status": "Received",
                "Payment Receive Date": pd.Timestamp("2026-04-06"),
            }
        ]
    )
    records = parse_payment_tracker(_xlsx(df), "tracker.xlsx")
    assert len(records) == 1
    row = records[0]
    assert row["channel"] == "direct"
    assert row["doc_no"] == "128-11111"
    assert row["is_manual_fee"] is False
    assert row["hc_fee_pct"] == 0.115
    assert row["hc_fees"] == 11500.0
    assert row["payment_receive_date"].isoformat() == "2026-04-06"
    assert row["payment_receive_note"] is None


def test_parse_payment_tracker_manual_calc_fee_and_broker_channel():
    df = pd.DataFrame(
        [
            {
                "Source": "Some Broker LLC",
                "Policy No.": "P456",
                "DocNo.": "128 - 22222",
                "Premium\n( excl VAT)": 50000.0,
                "Product": "Gold/Bronze",
                "HC Fee %": "manual calc",
                "HC Fees": 2500.0,
                "HC Payment Status": "",
                "Payment Receive Date": "Oct'25",
            }
        ]
    )
    records = parse_payment_tracker(_xlsx(df), "tracker.xlsx")
    row = records[0]
    assert row["channel"] == "broker"
    # Spaced doc no from the source sheet normalizes the same as an
    # unspaced one so it can join against a QIC SOA export.
    assert row["doc_no"] == "128-22222"
    assert row["is_manual_fee"] is True
    assert row["hc_fee_pct"] is None
    assert row["payment_receive_date"] is None
    assert row["payment_receive_note"] == "Oct'25"


def test_parse_payment_tracker_skips_blank_rows():
    df = pd.DataFrame(
        [
            {"Source": "Direct Channel", "Policy No.": "P1", "DocNo.": "128-1", "Premium\n( excl VAT)": 1000.0, "Product": "Silver"},
            {"Source": None, "Policy No.": None, "DocNo.": None, "Premium\n( excl VAT)": None, "Product": None},
        ]
    )
    records = parse_payment_tracker(_xlsx(df), "tracker.xlsx")
    assert len(records) == 1
