from datetime import date

from app.finance.fee_engine import FeeRate
from app.finance.tracker_analysis import analyze_payment_tracker, check_fee_rate_compliance


def test_check_fee_rate_compliance_flags_only_real_mismatches():
    rate_cards = [
        FeeRate(channel="broker", tier_band="bronze_silver", fee_pct=0.065),
        FeeRate(channel="direct", tier_band="gold_platinum", fee_pct=0.10),
    ]
    tracker_entries = [
        # Matches the rate card exactly - not flagged.
        {"id": 1, "doc_no": "128-1", "policy_no": "P-1", "channel": "broker", "product": "Silver", "is_manual_fee": False, "hc_fee_pct": 0.065},
        # Recorded 5% but rate card says broker/bronze_silver should be 6.5% - flagged.
        {"id": 2, "doc_no": "128-2", "policy_no": "P-2", "channel": "broker", "product": "Bronze", "is_manual_fee": False, "hc_fee_pct": 0.05},
        # Manual/negotiated rate - never checked against the rate card.
        {"id": 3, "doc_no": "128-3", "policy_no": "P-3", "channel": "group", "product": "Gold/Bronze", "is_manual_fee": True, "hc_fee_pct": 0.08},
        # Mixed-tier product that can't be banded at all - not checkable, not flagged.
        {"id": 4, "doc_no": "128-4", "policy_no": "P-4", "channel": "broker", "product": "Gold/Bronze", "is_manual_fee": False, "hc_fee_pct": 0.05},
    ]

    mismatches = check_fee_rate_compliance(tracker_entries, rate_cards)

    assert len(mismatches) == 1
    assert mismatches[0]["doc_no"] == "128-2"
    assert mismatches[0]["recorded_fee_pct"] == 0.05
    assert mismatches[0]["expected_fee_pct"] == 0.065


def test_analyze_payment_tracker_covers_all_sections():
    rate_cards = [FeeRate(channel="broker", tier_band="bronze_silver", fee_pct=0.065)]
    tracker_entries = [
        # Received on the same date - sums with entry 2 for the received_by_date bucket.
        {
            "id": 1, "doc_no": "128-1", "policy_no": "P-1", "main_policy_holder": "A", "channel": "broker", "product": "Silver",
            "is_manual_fee": False, "hc_fee_pct": 0.065, "hc_fees": 650.0, "total_value": 682.5,
            "premium_excl_vat": 10000.0, "invoice_amount": 10500.0,
            "client_payment_status": "Settled", "hc_payment_status": "Received", "payment_receive_date": date(2026, 7, 24),
        },
        {
            "id": 2, "doc_no": "128-2", "policy_no": "P-2", "main_policy_holder": "B", "channel": "broker", "product": "Silver",
            "is_manual_fee": False, "hc_fee_pct": 0.065, "hc_fees": 325.0, "total_value": 341.25,
            "premium_excl_vat": 5000.0, "invoice_amount": 5250.0,
            "client_payment_status": "Outstanding", "hc_payment_status": "Received", "payment_receive_date": date(2026, 7, 24),
        },
        # Client already Settled but HC fee still outstanding - the collection-gap report.
        {
            "id": 3, "doc_no": "128-3", "policy_no": "P-3", "main_policy_holder": "C", "channel": "direct", "product": "Gold",
            "is_manual_fee": False, "hc_fee_pct": 0.10, "hc_fees": 1000.0, "total_value": 1050.0,
            "premium_excl_vat": 10000.0, "invoice_amount": 10000.0,
            "client_payment_status": "Settled", "hc_payment_status": None, "payment_receive_date": None,
        },
        # Wrong rate vs the active rate card (broker/bronze_silver should be 6.5%, not 5%).
        {
            "id": 4, "doc_no": "128-4", "policy_no": "P-4", "main_policy_holder": "D", "channel": "broker", "product": "Bronze",
            "is_manual_fee": False, "hc_fee_pct": 0.05, "hc_fees": 500.0, "total_value": 525.0,
            "premium_excl_vat": 10000.0, "invoice_amount": 10000.0,
            "client_payment_status": "Outstanding", "hc_payment_status": "Outstanding", "payment_receive_date": None,
        },
    ]
    bank_transactions = [{"id": 100, "credit_amount": 1023.75, "txn_date": date(2026, 7, 24)}]  # 682.5 + 341.25

    result = analyze_payment_tracker(tracker_entries, bank_transactions, rate_cards)

    # Received-by-date: one bucket for 2026-07-24, matched to the bank credit.
    assert len(result["received_by_date"]) == 1
    bucket = result["received_by_date"][0]
    assert bucket["receive_date"] == date(2026, 7, 24)
    assert bucket["count"] == 2
    assert bucket["amount_received"] == 1023.75
    assert bucket["status"] == "matched"
    assert bucket["variance"] == 0.0

    # Due for collection (client side, not Settled): entries 2 and 4.
    assert result["total_due_for_collection"] == 5250.0 + 10000.0
    # Outstanding on the HC-fee side (not Received/Done): entries 3 and 4.
    assert result["total_outstanding_hc_fee"] == 1050.0 + 525.0

    assert result["total_fee"] == 650.0 + 325.0 + 1000.0 + 500.0
    assert result["total_received"] == 682.5 + 341.25
    assert result["total_premium"] == 10000.0 + 5000.0 + 10000.0 + 10000.0
    assert result["average_fee_pct_of_premium"] == round((650.0 + 325.0 + 1000.0 + 500.0) / 35000.0, 4)

    assert result["fee_rate_mismatch_count"] == 1
    assert result["fee_rate_mismatches"][0]["doc_no"] == "128-4"

    assert result["client_settled_hc_outstanding_count"] == 1
    assert result["client_settled_hc_outstanding"][0]["doc_no"] == "128-3"
    assert result["client_settled_hc_outstanding_amount"] == 1050.0
