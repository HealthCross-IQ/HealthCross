"""Three reconciliation reports HealthCross runs over its finance data,
each a pure function over plain dicts (never touching the DB directly) so
they can be unit tested without a database - see app/api/routes_finance.py
for how ORM rows are converted to dicts before being passed in here.

1. reconcile_tracker_vs_qic_soa - does every Payment Tracker doc exist (and
   match in amount) on QIC's own Statement of Account, and vice versa.
2. compare_qic_soa_periods - two QIC SOA exports (e.g. this month's vs. a
   later "recon" re-export) should describe the same underlying documents;
   flags what changed between them per doc.
3. reconcile_tracker_received_vs_bank - every PaymentTrackerEntry HC marked
   "Received" should be backed by a real inbound QIC credit in the bank
   statement. QIC settles HC's fee in periodic batched remittances rather
   than one wire per invoice, so this matches "everything marked Received
   on the same date" (summed) against the nearest bank credit within a
   date window, not one row at a time.
"""
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

AMOUNT_TOLERANCE = 0.05


def reconcile_tracker_vs_qic_soa(
    tracker_entries: List[dict],
    qic_soa_lines: List[dict],
    statement_period: Optional[str] = None,
    amount_tolerance: float = AMOUNT_TOLERANCE,
) -> dict:
    qic_by_doc: Dict[str, dict] = {}
    for line in qic_soa_lines:
        doc_no = line.get("doc_no")
        if doc_no:
            qic_by_doc[doc_no] = line

    matched_tracker_docs = set()
    rows = []
    for entry in tracker_entries:
        doc_no = entry.get("doc_no")
        if not doc_no:
            continue
        qic_line = qic_by_doc.get(doc_no)
        tracker_amount = entry.get("invoice_amount")
        if qic_line is None:
            status = "missing_in_qic"
            qic_amount = None
            variance = None
        else:
            matched_tracker_docs.add(doc_no)
            qic_amount = qic_line.get("gross_amount")
            variance = (
                round((tracker_amount or 0) - (qic_amount or 0), 2)
                if tracker_amount is not None and qic_amount is not None
                else None
            )
            status = "matched" if variance is not None and abs(variance) <= amount_tolerance else "amount_mismatch"

        rows.append(
            {
                "doc_no": doc_no,
                "client_name": entry.get("main_policy_holder"),
                "tracker_entry_id": entry.get("id"),
                "tracker_total_value": tracker_amount,
                "qic_soa_line_id": qic_line.get("id") if qic_line else None,
                "qic_gross_amount": qic_amount,
                "variance": variance,
                "status": status,
            }
        )

    for doc_no, qic_line in qic_by_doc.items():
        if doc_no in matched_tracker_docs:
            continue
        rows.append(
            {
                "doc_no": doc_no,
                "client_name": qic_line.get("insured_name"),
                "tracker_entry_id": None,
                "tracker_total_value": None,
                "qic_soa_line_id": qic_line.get("id"),
                "qic_gross_amount": qic_line.get("gross_amount"),
                "variance": None,
                "status": "missing_in_tracker",
            }
        )

    return {
        "statement_period": statement_period,
        "total_tracker_rows": len({r["doc_no"] for r in rows if r["tracker_entry_id"] is not None}),
        "total_qic_rows": len(qic_by_doc),
        "matched_count": sum(1 for r in rows if r["status"] == "matched"),
        "mismatched_count": sum(1 for r in rows if r["status"] == "amount_mismatch"),
        "missing_in_qic_count": sum(1 for r in rows if r["status"] == "missing_in_qic"),
        "missing_in_tracker_count": sum(1 for r in rows if r["status"] == "missing_in_tracker"),
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
