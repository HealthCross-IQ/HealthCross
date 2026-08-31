"""Standalone Payment Tracker analysis - a single dashboard view over the
tracker alone (not a reconciliation against another system), covering:

- Amount received, grouped by Payment Receive Date, next to the matching
  bank credit - reuses reconcile_tracker_received_vs_bank's own date/amount
  matching so this always agrees with the Reconciliation tab's own report,
  just rolled up to one row per date instead of one row per tracker entry.
- Amount still due for collection (client hasn't paid QIC yet) and amount
  still outstanding on the HC-fee side (QIC hasn't paid HC yet) - the same
  two legs used throughout this module (see reconciliation.py, the
  client-wise outstanding view).
- Fee-rate compliance: every rate-carded (non-manual) row's own recorded
  HC Fee % should match what the active FeeRateCard says for its channel x
  tier band - flags rows where it doesn't.
- Client-settled-but-HC-fee-outstanding: rows where the client has already
  paid QIC (Client Payment Status = Settled) but HC hasn't yet collected
  its own fee on that same document - the collection gap most worth
  chasing, since there's no client-side reason left for it to be open.
- Total fee, total received, total premium, and the average fee % of
  premium across every tracker row.
"""
from collections import defaultdict
from typing import Dict, List, Optional

from app.finance.fee_engine import FeeRate, band_for_product
from app.finance.reconciliation import reconcile_tracker_received_vs_bank

FEE_PCT_TOLERANCE = 0.001


def check_fee_rate_compliance(
    tracker_entries: List[dict],
    rate_cards: List[FeeRate],
    pct_tolerance: float = FEE_PCT_TOLERANCE,
) -> List[dict]:
    """Flags rate-carded tracker rows whose own recorded HC Fee % doesn't
    match the active FeeRateCard for their channel x tier band. Skips rows
    that aren't rate-card-checkable at all: manual/negotiated rows
    (`is_manual_fee`), a channel other than broker/direct, a Product that
    doesn't band to a single tier, or a channel x tier_band combination
    with no active rate card row - none of those are a "wrong rate"
    finding, they're just outside what a rate card can validate.
    """
    rate_lookup: Dict[tuple, float] = {(r.channel, r.tier_band): r.fee_pct for r in rate_cards}
    mismatches = []
    for entry in tracker_entries:
        if entry.get("is_manual_fee"):
            continue
        channel = entry.get("channel")
        if channel not in ("broker", "direct"):
            continue
        tier_band = band_for_product(entry.get("product"))
        if tier_band is None:
            continue
        expected_pct = rate_lookup.get((channel, tier_band))
        if expected_pct is None:
            continue
        recorded_pct = entry.get("hc_fee_pct")
        if recorded_pct is None or abs(recorded_pct - expected_pct) > pct_tolerance:
            mismatches.append(
                {
                    "tracker_entry_id": entry.get("id"),
                    "doc_no": entry.get("doc_no"),
                    "policy_no": entry.get("policy_no"),
                    "client_name": entry.get("main_policy_holder"),
                    "channel": channel,
                    "product": entry.get("product"),
                    "recorded_fee_pct": recorded_pct,
                    "expected_fee_pct": expected_pct,
                }
            )
    return mismatches


def analyze_payment_tracker(
    tracker_entries: List[dict],
    bank_transactions: List[dict],
    rate_cards: List[FeeRate],
    date_tolerance_days: int = 10,
    amount_tolerance: float = 1.0,
    pct_tolerance: float = FEE_PCT_TOLERANCE,
) -> dict:
    bank_recon = reconcile_tracker_received_vs_bank(tracker_entries, bank_transactions, date_tolerance_days, amount_tolerance)

    by_date: Dict[object, dict] = {}
    for row in bank_recon["rows"]:
        if row["tracker_entry_id"] is None:
            continue  # an "unmatched_bank_credit" phantom row, not a tracker-side row
        receive_date = row["payment_receive_date"]
        bucket = by_date.setdefault(
            receive_date,
            {
                "receive_date": receive_date,
                "count": 0,
                "amount_received": 0.0,
                "bank_credit_amount": row["bank_credit_amount"],
                "bank_txn_date": row["bank_txn_date"],
                "status": row["status"],
            },
        )
        bucket["count"] += 1
        bucket["amount_received"] += row["total_value"] or 0.0

    received_by_date = []
    for bucket in sorted(by_date.values(), key=lambda r: r["receive_date"]):
        bucket["amount_received"] = round(bucket["amount_received"], 2)
        bucket["variance"] = (
            round(bucket["amount_received"] - bucket["bank_credit_amount"], 2)
            if bucket["bank_credit_amount"] is not None
            else None
        )
        received_by_date.append(bucket)

    def _hc_outstanding(entry: dict) -> bool:
        status = (entry.get("hc_payment_status") or "").strip().lower()
        return not (status.startswith("received") or status == "done")

    total_due_for_collection = round(
        sum(
            e.get("invoice_amount") or 0.0
            for e in tracker_entries
            if (e.get("client_payment_status") or "").strip().lower() != "settled"
        ),
        2,
    )
    total_outstanding_hc_fee = round(sum(e.get("total_value") or 0.0 for e in tracker_entries if _hc_outstanding(e)), 2)
    total_fee = round(sum(e.get("hc_fees") or 0.0 for e in tracker_entries), 2)
    total_received = round(
        sum(
            e.get("total_value") or 0.0
            for e in tracker_entries
            if (e.get("hc_payment_status") or "").strip().lower().startswith("received")
        ),
        2,
    )
    total_premium = round(sum(e.get("premium_excl_vat") or 0.0 for e in tracker_entries), 2)
    average_fee_pct_of_premium = round(total_fee / total_premium, 4) if total_premium else None

    fee_rate_mismatches = check_fee_rate_compliance(tracker_entries, rate_cards, pct_tolerance)

    def _client_settled(entry: dict) -> bool:
        return (entry.get("client_payment_status") or "").strip().lower() == "settled"

    client_settled_hc_outstanding = sorted(
        (
            {
                "tracker_entry_id": e.get("id"),
                "doc_no": e.get("doc_no"),
                "policy_no": e.get("policy_no"),
                "client_name": e.get("main_policy_holder"),
                "total_value": e.get("total_value"),
                "hc_payment_status": e.get("hc_payment_status"),
                "due_date": e.get("due_date"),
            }
            for e in tracker_entries
            if _client_settled(e) and _hc_outstanding(e)
        ),
        key=lambda r: (r["total_value"] or 0.0),
        reverse=True,
    )

    return {
        "received_by_date": received_by_date,
        "total_due_for_collection": total_due_for_collection,
        "total_outstanding_hc_fee": total_outstanding_hc_fee,
        "total_fee": total_fee,
        "total_received": total_received,
        "total_premium": total_premium,
        "average_fee_pct_of_premium": average_fee_pct_of_premium,
        "fee_rate_mismatch_count": len(fee_rate_mismatches),
        "fee_rate_mismatches": fee_rate_mismatches,
        "client_settled_hc_outstanding_count": len(client_settled_hc_outstanding),
        "client_settled_hc_outstanding_amount": round(sum(r["total_value"] or 0.0 for r in client_settled_hc_outstanding), 2),
        "client_settled_hc_outstanding": client_settled_hc_outstanding,
    }
