import io

import pandas as pd
import pytest


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf.read()


def _upload(client, path, filename, df):
    return client.post(
        path,
        files={"file": (filename, _xlsx_bytes(df), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )


def test_create_payment_tracker_entry_computes_fee_from_rate_card(client):
    resp = client.post("/finance/payment-tracker", json={"channel": "direct", "product": "Silver", "premium_excl_vat": 200000})
    assert resp.status_code == 200
    body = resp.json()
    assert body["hc_fee_pct"] == 0.115
    assert body["hc_fees"] == 23000.0
    assert body["is_manual_fee"] is False


def test_create_payment_tracker_entry_requires_manual_rate_for_group(client):
    resp = client.post("/finance/payment-tracker", json={"channel": "group", "product": "Gold", "premium_excl_vat": 50000})
    assert resp.status_code == 400

    resp = client.post("/finance/payment-tracker", json={"channel": "group", "product": "Gold", "premium_excl_vat": 50000, "manual_fee_pct": 0.08})
    assert resp.status_code == 200
    assert resp.json()["is_manual_fee"] is True


def test_payment_tracker_upload_and_update_status(client):
    df = pd.DataFrame(
        [
            {
                "Source": "Direct Channel",
                "Policy No.": "P1",
                "DocNo.": "128-1",
                "Premium\n( excl VAT)": 100000.0,
                "Product": "Silver",
                "HC Fee %": 0.115,
                "HC Fees": 11500.0,
                "Total Value": 12075.0,
            }
        ]
    )
    resp = _upload(client, "/finance/payment-tracker/upload", "tracker.xlsx", df)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    entry_id = rows[0]["id"]
    assert rows[0]["doc_no"] == "128-1"

    resp = client.patch(f"/finance/payment-tracker/{entry_id}", json={"hc_payment_status": "Received", "payment_receive_date": "2026-07-24"})
    assert resp.status_code == 200
    assert resp.json()["hc_payment_status"] == "Received"

    resp = client.get("/finance/payment-tracker", params={"hc_payment_status": "Received"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_qic_soa_upload_replaces_same_period_and_reconciles_against_tracker(client):
    tracker_df = pd.DataFrame(
        [{"Source": "Direct Channel", "Policy No.": "P1", "DocNo.": "128-1", "Invoice amount": 1000.0, "Premium\n( excl VAT)": 1000.0, "Product": "Silver"}]
    )
    _upload(client, "/finance/payment-tracker/upload", "tracker.xlsx", tracker_df)

    soa_df = pd.DataFrame(
        [{"Doc No": "128 - 1", "Policy No": "P1", "Gross Amount": 1000.0, "AMOUNT": 1000.0, "Dr/Cr": "D", "Insured Name": "Acme"}]
    )
    resp = _upload(client, "/finance/qic-soa/upload?statement_period=2026-06", "soa.xlsx", soa_df)
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # Re-uploading the same period replaces, not accumulates.
    resp = _upload(client, "/finance/qic-soa/upload?statement_period=2026-06", "soa.xlsx", soa_df)
    assert len(resp.json()) == 1
    resp = client.get("/finance/qic-soa", params={"statement_period": "2026-06"})
    assert len(resp.json()) == 1

    resp = client.get("/finance/reconciliation/tracker-vs-client-soa", params={"statement_period": "2026-06"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_count"] == 1
    assert body["missing_in_client_soa_count"] == 0


def _fee_statement_bytes(rows, customer_code="216331"):
    header_block = [
        [None] * 21,
        [None] * 21,
        [None] * 21,
        ["Customer Name", f"{customer_code}-HEALTH CROSS GROUP FZCO"] + [None] * 19,
        ["Customer Code", customer_code] + [None] * 19,
        ["Date", "31-Jul-2026"] + [None] * 19,
        [None] * 21,
        [
            "Doc No", "Doc Date", "Due Date", "Line of Business", "Policy No", "Assured",
            "Claim No /Participant Name", "Chassis No", "Invoice No", "Report Currency", "Doc Currency",
            "Narration", "Debit FC", "Credit FC", "Debit LC", "Credit LC", "Division",
            "Transaction Type", "Policy From Date", "Policy To Date", "Age Band",
        ],
    ]
    df = pd.DataFrame(header_block + rows)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, header=False)
    buf.seek(0)
    return buf.read()


def test_health_cross_fee_statement_upload_replaces_only_its_own_statement_and_reconciles(client):
    tracker_df = pd.DataFrame(
        [
            {
                "Source": "Direct Channel", "Policy No.": "P1", "DocNo.": "128-1", "HealthCross Doc": "228-1",
                "Invoice amount": 1000.0, "Premium\n( excl VAT)": 1000.0, "Product": "Silver",
                "HC Fees": 100.0, "Total Value": 105.0, "HC Payment Status": None,
            }
        ]
    )
    _upload(client, "/finance/payment-tracker/upload", "tracker.xlsx", tracker_df)

    dubai_row = [
        "228-1", "15-SEP-2025", "04-NOV-2025", "Medical", "P1", "Acme",
        "-", "-", "-", "AED", "AED", "Pol No: P1", 0, 105.0, 0, 105.0, "Dubai Branch",
        "TPA Fee", "06-AUG-2025", "05-AUG-2026", "271 TO 365",
    ]
    resp = client.post(
        "/finance/health-cross-fee-statement/upload?statement_period=2026-07",
        files={"file": ("dubai.xlsx", _fee_statement_bytes([dubai_row], customer_code="216331"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["division"] == "Dubai Branch"

    # The Abu Dhabi statement's own row is itself labeled Division "Dubai
    # Branch" (a real quirk seen in production - Division reflects which
    # office administers the policy, not which branch statement it's on).
    # Uploading it for the SAME period must not wipe the Dubai statement's
    # rows just because they share that Division value - only its own
    # statement (by Customer Code) should be replaced.
    abu_dhabi_row = [
        "128-9", "17-APR-2026", "-", "Medical", "P9", "Other Client",
        "-", "-", "-", "AED", "AED", "Pol No: P9", 50.0, 0, 50.0, 0, "Dubai Branch",
        "Premium", "01-MAR-2026", "28-FEB-2027", "61 TO 90",
    ]
    resp = client.post(
        "/finance/health-cross-fee-statement/upload?statement_period=2026-07",
        files={"file": ("abudhabi.xlsx", _fee_statement_bytes([abu_dhabi_row], customer_code="293276"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    resp = client.get("/finance/health-cross-fee-statement", params={"statement_period": "2026-07"})
    lines = resp.json()
    assert len(lines) == 2
    assert {l["statement_customer_code"] for l in lines} == {"216331", "293276"}

    # Re-uploading Dubai's file again (e.g. a corrected re-export) replaces
    # only its own 1 row, still leaving Abu Dhabi's row untouched.
    resp = client.post(
        "/finance/health-cross-fee-statement/upload?statement_period=2026-07",
        files={"file": ("dubai2.xlsx", _fee_statement_bytes([dubai_row], customer_code="216331"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    resp = client.get("/finance/health-cross-fee-statement", params={"statement_period": "2026-07"})
    assert len(resp.json()) == 2

    resp = client.get("/finance/reconciliation/tracker-vs-fee-statement", params={"statement_period": "2026-07"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_count"] == 1
    assert body["missing_in_fee_statement_count"] == 0


def test_payment_tracker_analysis_endpoint(client):
    tracker_df = pd.DataFrame(
        [
            {
                "Source": "Direct Channel", "Policy No.": "P1", "DocNo.": "128-1",
                "Invoice amount": 10500.0, "Premium\n( excl VAT)": 10000.0, "Product": "Silver",
                "HC Fee %": 0.115, "HC Fees": 1150.0, "Total Value": 1207.5,
                "Client Payment Status": "Settled", "HC Payment Status": "Received",
                "Payment Receive Date": pd.Timestamp("2026-07-24"),
            },
            {
                # Client already Settled, but HC hasn't collected its fee yet.
                "Source": "Direct Channel", "Policy No.": "P2", "DocNo.": "128-2",
                "Invoice amount": 5000.0, "Premium\n( excl VAT)": 5000.0, "Product": "Gold",
                "HC Fee %": 0.10, "HC Fees": 500.0, "Total Value": 525.0,
                "Client Payment Status": "Settled", "HC Payment Status": None,
            },
        ]
    )
    _upload(client, "/finance/payment-tracker/upload", "tracker.xlsx", tracker_df)

    bank_df = pd.DataFrame(
        [{"Date": "24-Jul-2026", "Value Date": "24-Jul-2026", "Reference Number": "R1", "Description": "QIC settlement", "Credit": 1207.5, "Debit": 0}]
    )
    buf = io.BytesIO()
    bank_df.to_excel(buf, index=False)
    buf.seek(0)
    client.post("/finance/bank-statement/upload", files={"file": ("bank.xlsx", buf.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})

    resp = client.get("/finance/payment-tracker-analysis")
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["received_by_date"]) == 1
    assert body["received_by_date"][0]["amount_received"] == 1207.5
    assert body["received_by_date"][0]["status"] == "matched"

    assert body["total_outstanding_hc_fee"] == 525.0
    assert body["total_fee"] == 1150.0 + 500.0
    assert body["total_received"] == 1207.5

    assert body["client_settled_hc_outstanding_count"] == 1
    assert body["client_settled_hc_outstanding"][0]["doc_no"] == "128-2"


def test_employees_recurring_expenses_and_generate(client):
    resp = client.post("/finance/employees", json={"full_name": "Sheetal", "role_title": "Head of Operations", "monthly_salary": 30000, "currency": "AED"})
    assert resp.status_code == 200

    resp = client.post("/finance/recurring-expenses", json={"name": "Nivotime", "category": "software", "default_amount": 5000, "expense_type": "fixed"})
    assert resp.status_code == 200

    resp = client.post("/finance/expenses/generate", params={"period": "2026-08-15"})
    assert resp.status_code == 200
    generated = resp.json()
    assert len(generated) == 2
    assert {e["period"] for e in generated} == {"2026-08-01"}

    # Calling again for the same period doesn't duplicate.
    resp = client.post("/finance/expenses/generate", params={"period": "2026-08-01"})
    assert resp.json() == []

    resp = client.get("/finance/expenses", params={"year": 2026})
    assert len(resp.json()) == 2


def test_employee_edit_and_delete_keeps_expense_history(client):
    resp = client.post("/finance/employees", json={"full_name": "Dianne", "role_title": "Account Executive", "monthly_salary": 4500, "currency": "AED"})
    employee_id = resp.json()["id"]

    resp = client.patch(f"/finance/employees/{employee_id}", json={"monthly_salary": 5000})
    assert resp.status_code == 200
    assert resp.json()["monthly_salary"] == 5000

    resp = client.post("/finance/expenses/generate", params={"period": "2026-08-01"})
    expense_id = resp.json()[0]["id"]

    resp = client.delete(f"/finance/employees/{employee_id}")
    assert resp.status_code == 204

    resp = client.get("/finance/employees")
    assert all(e["id"] != employee_id for e in resp.json())

    # The already-generated expense row survives, just unlinked from the deleted employee.
    resp = client.get("/finance/expenses")
    matching = [e for e in resp.json() if e["id"] == expense_id]
    assert len(matching) == 1
    assert matching[0]["employee_id"] is None


def test_employee_end_of_service_computed_from_start_date(client):
    import datetime

    six_years_ago = (datetime.date.today() - datetime.timedelta(days=365 * 6)).isoformat()
    resp = client.post(
        "/finance/employees",
        json={"full_name": "Karim", "monthly_salary": 12000, "currency": "AED", "start_date": six_years_ago},
    )
    body = resp.json()
    assert body["years_of_service"] > 5.9
    assert body["end_of_service_gratuity"] > 0

    # No start_date yet - not computable.
    resp = client.post("/finance/employees", json={"full_name": "New Hire", "monthly_salary": 8000, "currency": "AED"})
    body = resp.json()
    assert body["start_date"] is None
    assert body["years_of_service"] is None
    assert body["end_of_service_gratuity"] is None

    # Setting an end_date fixes the gratuity as of that date rather than today.
    employee_id = client.post(
        "/finance/employees",
        json={"full_name": "Leaver", "monthly_salary": 10000, "currency": "AED", "start_date": "2020-01-01"},
    ).json()["id"]
    resp = client.patch(f"/finance/employees/{employee_id}", json={"end_date": "2023-01-01"})
    body = resp.json()
    assert body["years_of_service"] == pytest.approx(3.0, abs=0.01)


def test_recurring_expense_edit_and_delete(client):
    resp = client.post("/finance/recurring-expenses", json={"name": "Etisalat", "category": "telecom", "expense_type": "variable"})
    recurring_id = resp.json()["id"]

    resp = client.patch(f"/finance/recurring-expenses/{recurring_id}", json={"default_amount": 300})
    assert resp.status_code == 200
    assert resp.json()["default_amount"] == 300

    resp = client.delete(f"/finance/recurring-expenses/{recurring_id}")
    assert resp.status_code == 204

    resp = client.get("/finance/recurring-expenses")
    assert all(r["id"] != recurring_id for r in resp.json())


def test_expense_entry_delete(client):
    resp = client.post("/finance/expenses", json={"period": "2026-08-01", "category": "rent", "expense_type": "fixed", "amount": 1000})
    expense_id = resp.json()["id"]

    resp = client.delete(f"/finance/expenses/{expense_id}")
    assert resp.status_code == 204

    resp = client.get("/finance/expenses", params={"year": 2026})
    assert all(e["id"] != expense_id for e in resp.json())


def test_cash_flow_and_summary_endpoints(client):
    tracker_df = pd.DataFrame(
        [
            {
                "Source": "Direct Channel",
                "Policy No.": "P1",
                "DocNo.": "128-1",
                "Premium\n( excl VAT)": 100000.0,
                "Product": "Silver",
                "HC Fee %": 0.115,
                "Total Value": 12075.0,
                "HC Payment Status": "Received",
                "Payment Receive Date": pd.Timestamp("2026-03-06"),
            }
        ]
    )
    _upload(client, "/finance/payment-tracker/upload", "tracker.xlsx", tracker_df)

    resp = client.get("/finance/cash-flow", params={"year": 2026})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_inflow"] == 12075.0

    resp = client.get("/finance/summary")
    assert resp.status_code == 200
    assert resp.json()["total_hc_fees_received"] == 12075.0
