from datetime import date

from app.finance.reconciliation import (
    compare_qic_soa_periods,
    reconcile_tracker_received_vs_bank,
    reconcile_tracker_vs_client_soa_by_policy,
)


def test_reconcile_tracker_vs_client_soa_by_policy_covers_all_five_outcomes():
    tracker_entries = [
        # P-1: outstanding, sums to 1000 across two installments - matches SOA.
        {"id": 1, "doc_no": "128-1", "policy_no": "P-1", "invoice_amount": 600.0, "main_policy_holder": "A", "client_payment_status": "Outstanding"},
        {"id": 2, "doc_no": "128-1", "policy_no": "P-1", "invoice_amount": 400.0, "main_policy_holder": "A", "client_payment_status": "Outstanding"},
        # P-2: outstanding at 2000, SOA shows 2500 - amount_mismatch.
        {"id": 3, "doc_no": "128-2", "policy_no": "P-2", "invoice_amount": 2000.0, "main_policy_holder": "B", "client_payment_status": None},
        # P-3: outstanding, no SOA line at all - missing_in_client_soa.
        {"id": 4, "doc_no": "128-3", "policy_no": "P-3", "invoice_amount": 3000.0, "main_policy_holder": "C", "client_payment_status": "Outstanding"},
        # P-4: fully Settled in tracker, but SOA still shows it open -
        # settled_in_tracker_but_open_in_client_soa (not "missing", since HC
        # does have the policy - it's just marked with the wrong status).
        {"id": 5, "doc_no": "128-4", "policy_no": "P-4", "invoice_amount": 800.0, "main_policy_holder": "D", "client_payment_status": "Settled"},
    ]
    soa_lines = [
        {"policy_no": "P-1", "doc_no": "128-1", "gross_amount": 1000.0, "insured_name": "A"},
        {"policy_no": "P-2", "doc_no": "128-2", "gross_amount": 2500.0, "insured_name": "B"},
        {"policy_no": "P-4", "doc_no": "128-4", "gross_amount": 800.0, "insured_name": "D"},
        # P-5 never appears in the tracker at all - missing_in_tracker.
        {"policy_no": "P-5", "doc_no": "128-5", "gross_amount": 500.0, "insured_name": "E"},
    ]

    result = reconcile_tracker_vs_client_soa_by_policy(tracker_entries, soa_lines, statement_period="2026-06")

    statuses = {row["policy_no"]: row["status"] for row in result["rows"]}
    assert statuses["P-1"] == "matched"
    assert statuses["P-2"] == "amount_mismatch"
    assert statuses["P-3"] == "missing_in_client_soa"
    assert statuses["P-4"] == "settled_in_tracker_but_open_in_client_soa"
    assert statuses["P-5"] == "missing_in_tracker"
    assert result["matched_count"] == 1
    assert result["mismatched_count"] == 1
    assert result["missing_in_client_soa_count"] == 1
    assert result["settled_in_tracker_but_open_in_client_soa_count"] == 1
    assert result["missing_in_tracker_count"] == 1

    # P-1's two installments were summed into one policy-level row.
    p1_row = next(r for r in result["rows"] if r["policy_no"] == "P-1")
    assert p1_row["tracker_outstanding_amount"] == 1000.0
    assert p1_row["tracker_outstanding_count"] == 2


def test_compare_qic_soa_periods_only_reports_differences():
    lines_a = [
        {"doc_no": "128-1", "gross_amount": 1000.0},
        {"doc_no": "128-2", "gross_amount": 2000.0},
        {"doc_no": "128-3", "gross_amount": 3000.0},
    ]
    lines_b = [
        {"doc_no": "128-1", "gross_amount": 1000.0},  # unchanged - should NOT appear in rows
        {"doc_no": "128-2", "gross_amount": 2500.0},  # changed
        {"doc_no": "128-4", "gross_amount": 400.0},  # only_in_b
        # 128-3 only_in_a
    ]

    result = compare_qic_soa_periods(lines_a, lines_b, "June", "Recon")

    doc_nos = {row["doc_no"] for row in result["rows"]}
    assert "128-1" not in doc_nos
    assert result["changed_count"] == 1
    assert result["only_in_a_count"] == 1
    assert result["only_in_b_count"] == 1


def test_reconcile_tracker_received_vs_bank_groups_by_date_and_sums():
    tracker_entries = [
        {"id": 1, "doc_no": "128-1", "main_policy_holder": "A", "total_value": 1000.0, "hc_payment_status": "Received", "payment_receive_date": date(2026, 7, 24)},
        {"id": 2, "doc_no": "128-2", "main_policy_holder": "B", "total_value": 2000.0, "hc_payment_status": "Received", "payment_receive_date": date(2026, 7, 24)},
        # not received - should be ignored entirely
        {"id": 3, "doc_no": "128-3", "main_policy_holder": "C", "total_value": 500.0, "hc_payment_status": None, "payment_receive_date": None},
    ]
    bank_transactions = [
        {"id": 100, "credit_amount": 3000.0, "txn_date": date(2026, 7, 24)},
        {"id": 101, "credit_amount": 50000.0, "txn_date": date(2026, 7, 31)},  # unmatched bank credit
    ]

    result = reconcile_tracker_received_vs_bank(tracker_entries, bank_transactions)

    matched_rows = [r for r in result["rows"] if r["status"] == "matched"]
    assert {r["tracker_entry_id"] for r in matched_rows} == {1, 2}
    assert all(r["bank_transaction_id"] == 100 for r in matched_rows)
    assert result["matched_count"] == 2
    assert result["unmatched_bank_count"] == 1


def test_reconcile_tracker_received_vs_bank_no_match_within_tolerance():
    tracker_entries = [
        {"id": 1, "doc_no": "128-1", "main_policy_holder": "A", "total_value": 1000.0, "hc_payment_status": "Received", "payment_receive_date": date(2026, 7, 24)},
    ]
    bank_transactions = [{"id": 100, "credit_amount": 5000.0, "txn_date": date(2026, 7, 24)}]

    result = reconcile_tracker_received_vs_bank(tracker_entries, bank_transactions)
    assert result["rows"][0]["status"] == "no_bank_match"
    assert result["unmatched_bank_count"] == 1
