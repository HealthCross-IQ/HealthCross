from datetime import date

from app.finance.reconciliation import (
    compare_qic_soa_periods,
    reconcile_tracker_received_vs_bank,
    reconcile_tracker_vs_qic_soa,
)


def test_reconcile_tracker_vs_qic_soa_covers_all_four_outcomes():
    tracker_entries = [
        {"id": 1, "doc_no": "128-1", "invoice_amount": 1000.0, "main_policy_holder": "A"},
        {"id": 2, "doc_no": "128-2", "invoice_amount": 2000.0, "main_policy_holder": "B"},
        {"id": 3, "doc_no": "128-3", "invoice_amount": 3000.0, "main_policy_holder": "C"},
    ]
    qic_lines = [
        {"id": 10, "doc_no": "128-1", "gross_amount": 1000.0, "insured_name": "A"},  # matched
        {"id": 11, "doc_no": "128-2", "gross_amount": 2500.0, "insured_name": "B"},  # amount_mismatch
        # 128-3 missing_in_qic
        {"id": 12, "doc_no": "128-9", "gross_amount": 500.0, "insured_name": "D"},  # missing_in_tracker
    ]

    result = reconcile_tracker_vs_qic_soa(tracker_entries, qic_lines, statement_period="2026-06")

    statuses = {row["doc_no"]: row["status"] for row in result["rows"]}
    assert statuses["128-1"] == "matched"
    assert statuses["128-2"] == "amount_mismatch"
    assert statuses["128-3"] == "missing_in_qic"
    assert statuses["128-9"] == "missing_in_tracker"
    assert result["matched_count"] == 1
    assert result["mismatched_count"] == 1
    assert result["missing_in_qic_count"] == 1
    assert result["missing_in_tracker_count"] == 1


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
