"""Four reconciliation reports HealthCross runs over its finance data,
each a pure function over plain dicts (never touching the DB directly) so
they can be unit tested without a database - see app/api/routes_finance.py
for how ORM rows are converted to dicts before being passed in here.

1. reconcile_tracker_vs_client_soa_by_policy - does every Payment Tracker
   policy that's still outstanding on the client side exist (and match in
   amount) on QIC's Client Statement of Account, and vice versa.
2. reconcile_tracker_vs_fee_statement_by_policy - same idea, but for HC's
   own fee/commission side: does every Payment Tracker policy still
   outstanding on the HC-fee side exist (and match in amount) on QIC's
   HealthCross Fee Statement, and vice versa.
3. compare_qic_soa_periods - two QIC SOA exports (e.g. this month's vs. a
   later "recon" re-export) should describe the same underlying documents;
   flags what changed between them per doc.
4. reconcile_tracker_received_vs_bank - every PaymentTrackerEntry HC marked
   "Received" should be backed by a real inbound QIC credit in the bank
   statement. QIC settles HC's fee in periodic batched remittances rather
   than one wire per invoice, so this matches "everything marked Received
   on the same date" (summed) against the nearest bank credit within a
   date window, not one row at a time.
"""
from collections import Counter, defaultdict
from datetime import date
from typing import Dict, List, Optional

AMOUNT_TOLERANCE = 0.05

# Most-actionable first: something's genuinely wrong (mismatch, missing) before
# a clean match, which the UI usually filters out entirely.
_STATUS_PRIORITY = {
    "amount_mismatch": 0,
    "missing_in_client_soa": 1,
    "settled_in_tracker_but_open_in_client_soa": 2,
    "missing_in_tracker": 3,
    "matched": 4,
}

_FEE_STATUS_PRIORITY = {
    "amount_mismatch": 0,
    "missing_in_fee_statement": 1,
    "received_in_tracker_but_open_in_fee_statement": 2,
    "missing_in_tracker": 3,
    "matched": 4,
}


def reconcile_tracker_vs_client_soa_by_policy(
    tracker_entries: List[dict],
    client_soa_lines: List[dict],
    statement_period: Optional[str] = None,
    amount_tolerance: float = AMOUNT_TOLERANCE,
) -> dict:
    """Compares HC's Payment Tracker against QIC's Client Statement of
    Account, grouped by Policy No (column G on both sheets) rather than by
    individual Doc No - a policy is often billed in several installments
    sharing one Doc No, and the two systems should agree on the total
    outstanding per policy even when the per-installment split drifts.

    QIC's Client SOA is an outstanding-only snapshot - it only lists
    documents QIC hasn't closed out yet - so the tracker side is filtered
    the same way first (Client Payment Status != "Settled", column W)
    before summing; otherwise an already-settled tracker row (correctly
    absent from the SOA) would misreport as "missing in client SOA".

    A policy on the SOA with no *outstanding* tracker row splits into two
    distinct outcomes rather than one generic "missing": if the policy
    exists in the tracker at all (just marked Settled), that's a status
    HC got wrong, not a policy HC never logged - "missing_in_tracker" is
    reserved for the latter, genuinely-never-logged case.
    """
    all_tracker_policies = set()
    outstanding_by_policy: Dict[str, dict] = {}
    for entry in tracker_entries:
        policy_no = entry.get("policy_no")
        if not policy_no:
            continue
        all_tracker_policies.add(policy_no)
        status = (entry.get("client_payment_status") or "").strip().lower()
        if status == "settled":
            continue
        bucket = outstanding_by_policy.setdefault(
            policy_no, {"amount": 0.0, "count": 0, "doc_nos": set(), "client_name": None}
        )
        bucket["amount"] += entry.get("invoice_amount") or 0.0
        bucket["count"] += 1
        if entry.get("doc_no"):
            bucket["doc_nos"].add(entry["doc_no"])
        if entry.get("main_policy_holder"):
            bucket["client_name"] = entry["main_policy_holder"]

    soa_by_policy: Dict[str, dict] = {}
    for line in client_soa_lines:
        policy_no = line.get("policy_no")
        if not policy_no:
            continue
        bucket = soa_by_policy.setdefault(policy_no, {"amount": 0.0, "count": 0, "doc_nos": set(), "client_name": None})
        bucket["amount"] += line.get("gross_amount") or 0.0
        bucket["count"] += 1
        if line.get("doc_no"):
            bucket["doc_nos"].add(line["doc_no"])
        if line.get("insured_name"):
            bucket["client_name"] = line["insured_name"]

    def _row(policy_no, status, tracker=None, soa=None, variance=None):
        doc_nos = sorted((tracker or {}).get("doc_nos", set()) | (soa or {}).get("doc_nos", set()))
        return {
            "policy_no": policy_no,
            "client_name": (tracker or {}).get("client_name") or (soa or {}).get("client_name"),
            "doc_nos": doc_nos,
            "tracker_outstanding_amount": tracker["amount"] if tracker else None,
            "tracker_outstanding_count": tracker["count"] if tracker else 0,
            "client_soa_amount": soa["amount"] if soa else None,
            "client_soa_count": soa["count"] if soa else 0,
            "variance": variance,
            "status": status,
        }

    rows = []
    for policy_no, tracker in outstanding_by_policy.items():
        soa = soa_by_policy.get(policy_no)
        if soa is None:
            rows.append(_row(policy_no, "missing_in_client_soa", tracker=tracker))
            continue
        variance = round(tracker["amount"] - soa["amount"], 2)
        status = "matched" if abs(variance) <= amount_tolerance else "amount_mismatch"
        rows.append(_row(policy_no, status, tracker=tracker, soa=soa, variance=variance))

    for policy_no, soa in soa_by_policy.items():
        if policy_no in outstanding_by_policy:
            continue
        status = "settled_in_tracker_but_open_in_client_soa" if policy_no in all_tracker_policies else "missing_in_tracker"
        rows.append(_row(policy_no, status, soa=soa))

    rows.sort(key=lambda r: (_STATUS_PRIORITY[r["status"]], r["policy_no"]))
    counts = Counter(r["status"] for r in rows)

    return {
        "statement_period": statement_period,
        "total_policies_outstanding_in_tracker": len(outstanding_by_policy),
        "total_policies_in_client_soa": len(soa_by_policy),
        "matched_count": counts["matched"],
        "mismatched_count": counts["amount_mismatch"],
        "missing_in_client_soa_count": counts["missing_in_client_soa"],
        "settled_in_tracker_but_open_in_client_soa_count": counts["settled_in_tracker_but_open_in_client_soa"],
        "missing_in_tracker_count": counts["missing_in_tracker"],
        "rows": rows,
    }


def reconcile_tracker_vs_fee_statement_by_policy(
    tracker_entries: List[dict],
    fee_statement_lines: List[dict],
    statement_period: Optional[str] = None,
    amount_tolerance: float = AMOUNT_TOLERANCE,
) -> dict:
    """Compares HC's Payment Tracker against QIC's HealthCross Fee
    Statement ("Statement of Outstanding" addressed to HC itself), grouped
    by Policy No - mirrors reconcile_tracker_vs_client_soa_by_policy's
    shape and reasoning, but for the HC-fee side rather than the
    client-premium side:

    - Tracker outstanding = `hc_payment_status` isn't "Received" or "Done"
      (column AI), summed on `total_value` (the VAT-inclusive fee, which is
      what the fee statement's own amounts turned out to match exactly).
    - Fee statement amount = credit_amount minus debit_amount per line,
      summed regardless of QIC's own Transaction Type label - validated
      against the file's own printed "Net Due to You" total, which is
      exactly the sum of every row with no Transaction Type filtering.
    - A policy on the fee statement with no *outstanding* tracker row
      splits into two outcomes: if the tracker has the policy at all (just
      already marked Received/Done), that's "received_in_tracker_but_open_
      in_fee_statement" - HC's own record may be ahead of QIC's, or QIC
      hasn't dropped it from its ledger yet, either way worth flagging
      rather than treating as a policy HC never logged at all.
    """
    all_tracker_policies = set()
    outstanding_by_policy: Dict[str, dict] = {}
    for entry in tracker_entries:
        policy_no = entry.get("policy_no")
        if not policy_no:
            continue
        all_tracker_policies.add(policy_no)
        status = (entry.get("hc_payment_status") or "").strip().lower()
        if status.startswith("received") or status == "done":
            continue
        bucket = outstanding_by_policy.setdefault(
            policy_no, {"amount": 0.0, "count": 0, "doc_nos": set(), "client_name": None}
        )
        bucket["amount"] += entry.get("total_value") or 0.0
        bucket["count"] += 1
        if entry.get("healthcross_doc"):
            bucket["doc_nos"].add(entry["healthcross_doc"])
        if entry.get("main_policy_holder"):
            bucket["client_name"] = entry["main_policy_holder"]

    fee_by_policy: Dict[str, dict] = {}
    for line in fee_statement_lines:
        policy_no = line.get("policy_no")
        if not policy_no:
            continue
        bucket = fee_by_policy.setdefault(policy_no, {"amount": 0.0, "count": 0, "doc_nos": set(), "client_name": None})
        bucket["amount"] += (line.get("credit_amount") or 0.0) - (line.get("debit_amount") or 0.0)
        bucket["count"] += 1
        if line.get("doc_no"):
            bucket["doc_nos"].add(line["doc_no"])
        if line.get("assured_name"):
            bucket["client_name"] = line["assured_name"]

    def _row(policy_no, status, tracker=None, fee=None, variance=None):
        doc_nos = sorted((tracker or {}).get("doc_nos", set()) | (fee or {}).get("doc_nos", set()))
        return {
            "policy_no": policy_no,
            "client_name": (tracker or {}).get("client_name") or (fee or {}).get("client_name"),
            "doc_nos": doc_nos,
            "tracker_outstanding_amount": tracker["amount"] if tracker else None,
            "tracker_outstanding_count": tracker["count"] if tracker else 0,
            "fee_statement_amount": fee["amount"] if fee else None,
            "fee_statement_count": fee["count"] if fee else 0,
            "variance": variance,
            "status": status,
        }

    rows = []
    for policy_no, tracker in outstanding_by_policy.items():
        fee = fee_by_policy.get(policy_no)
        if fee is None:
            rows.append(_row(policy_no, "missing_in_fee_statement", tracker=tracker))
            continue
        variance = round(tracker["amount"] - fee["amount"], 2)
        status = "matched" if abs(variance) <= amount_tolerance else "amount_mismatch"
        rows.append(_row(policy_no, status, tracker=tracker, fee=fee, variance=variance))

    for policy_no, fee in fee_by_policy.items():
        if policy_no in outstanding_by_policy:
            continue
        status = "received_in_tracker_but_open_in_fee_statement" if policy_no in all_tracker_policies else "missing_in_tracker"
        rows.append(_row(policy_no, status, fee=fee))

    rows.sort(key=lambda r: (_FEE_STATUS_PRIORITY[r["status"]], r["policy_no"]))
    counts = Counter(r["status"] for r in rows)

    return {
        "statement_period": statement_period,
        "total_policies_outstanding_in_tracker": len(outstanding_by_policy),
        "total_policies_in_fee_statement": len(fee_by_policy),
        "matched_count": counts["matched"],
        "mismatched_count": counts["amount_mismatch"],
        "missing_in_fee_statement_count": counts["missing_in_fee_statement"],
        "received_in_tracker_but_open_in_fee_statement_count": counts["received_in_tracker_but_open_in_fee_statement"],
        "missing_in_tracker_count": counts["missing_in_tracker"],
        "rows": rows,
    }


def compare_qic_soa_periods(
    lines_a: List[dict],
    lines_b: List[dict],
    period_a: str,
    period_b: str,
    amount_tolerance: float = AMOUNT_TOLERANCE,
) -> dict:
    # A document can carry more than one installment line at the same
    # Gross Amount (e.g. two due dates on one invoice) - doc_no is still the
    # right comparison key since what matters here is "does this document
    # still describe the same amount in both exports", not per-installment
    # detail, so later lines for a repeated doc_no simply overwrite earlier
    # ones rather than being treated as a conflict.
    by_doc_a = {line["doc_no"]: line for line in lines_a if line.get("doc_no")}
    by_doc_b = {line["doc_no"]: line for line in lines_b if line.get("doc_no")}

    rows = []
    for doc_no in sorted(set(by_doc_a) | set(by_doc_b)):
        in_a = by_doc_a.get(doc_no)
        in_b = by_doc_b.get(doc_no)
        if in_a is not None and in_b is None:
            rows.append({"doc_no": doc_no, "period_a_amount": in_a.get("gross_amount"), "period_b_amount": None, "variance": None, "status": "only_in_a"})
        elif in_b is not None and in_a is None:
            rows.append({"doc_no": doc_no, "period_a_amount": None, "period_b_amount": in_b.get("gross_amount"), "variance": None, "status": "only_in_b"})
        else:
            amount_a, amount_b = in_a.get("gross_amount"), in_b.get("gross_amount")
            variance = round((amount_a or 0) - (amount_b or 0), 2)
            if abs(variance) > amount_tolerance:
                rows.append({"doc_no": doc_no, "period_a_amount": amount_a, "period_b_amount": amount_b, "variance": variance, "status": "changed"})
            # Unchanged docs aren't included in the row list - a
            # reconciliation report should surface differences, not
            # reproduce the entire (usually much larger) matching set.

    return {
        "period_a": period_a,
        "period_b": period_b,
        "changed_count": sum(1 for r in rows if r["status"] == "changed"),
        "only_in_a_count": sum(1 for r in rows if r["status"] == "only_in_a"),
        "only_in_b_count": sum(1 for r in rows if r["status"] == "only_in_b"),
        "rows": rows,
    }


def reconcile_tracker_received_vs_bank(
    tracker_entries: List[dict],
    bank_transactions: List[dict],
    date_tolerance_days: int = 10,
    amount_tolerance: float = 1.0,
) -> dict:
    received = [
        t
        for t in tracker_entries
        if (t.get("hc_payment_status") or "").strip().lower().startswith("received") and t.get("payment_receive_date")
    ]

    groups: Dict[date, List[dict]] = defaultdict(list)
    for entry in received:
        groups[entry["payment_receive_date"]].append(entry)

    candidate_credits = [b for b in bank_transactions if (b.get("credit_amount") or 0) > 0]
    matched_bank_ids = set()
    rows = []

    for receive_date, entries in sorted(groups.items()):
        expected_total = round(sum(e.get("total_value") or 0 for e in entries), 2)
        best_match, best_diff = None, None
        for bank_txn in candidate_credits:
            if bank_txn.get("id") in matched_bank_ids or not bank_txn.get("txn_date"):
                continue
            if abs((bank_txn["txn_date"] - receive_date).days) > date_tolerance_days:
                continue
            diff = abs((bank_txn.get("credit_amount") or 0) - expected_total)
            if diff <= amount_tolerance and (best_diff is None or diff < best_diff):
                best_match, best_diff = bank_txn, diff

        if best_match is not None:
            matched_bank_ids.add(best_match["id"])

        for entry in entries:
            rows.append(
                {
                    "tracker_entry_id": entry.get("id"),
                    "doc_no": entry.get("doc_no"),
                    "client_name": entry.get("main_policy_holder"),
                    "total_value": entry.get("total_value"),
                    "payment_receive_date": receive_date,
                    "bank_transaction_id": best_match.get("id") if best_match else None,
                    "bank_credit_amount": best_match.get("credit_amount") if best_match else None,
                    "bank_txn_date": best_match.get("txn_date") if best_match else None,
                    "status": "matched" if best_match else "no_bank_match",
                }
            )

    for bank_txn in candidate_credits:
        if bank_txn.get("id") not in matched_bank_ids:
            rows.append(
                {
                    "tracker_entry_id": None,
                    "doc_no": None,
                    "client_name": None,
                    "total_value": None,
                    "payment_receive_date": None,
                    "bank_transaction_id": bank_txn.get("id"),
                    "bank_credit_amount": bank_txn.get("credit_amount"),
                    "bank_txn_date": bank_txn.get("txn_date"),
                    "status": "unmatched_bank_credit",
                }
            )

    return {
        "matched_count": sum(1 for r in rows if r["status"] == "matched"),
        "unmatched_tracker_count": sum(1 for r in rows if r["status"] == "no_bank_match"),
        "unmatched_bank_count": sum(1 for r in rows if r["status"] == "unmatched_bank_credit"),
        "rows": rows,
    }
