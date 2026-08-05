"""Parser for the "Payment Tracker" working sheet - HC's per-invoice ledger
of QIC premium documents and the HC fee earned on each, e.g. the real
"Payment Tracker" / "Ledgers" / "Eman" sheets of the working payment
tracker workbook (their column sets differ slightly release to release;
the alias map below covers the variants seen across all three).

Historical rows are imported with whatever hc_fees/vat_amount/total_value
the sheet itself already computed, rather than recalculated through
app.finance.fee_engine - a bootstrap import should preserve the actual
historical invoiced figures (including any manual roundings/adjustments)
exactly as issued, not silently rewrite them. New entries created going
forward (POST /finance/payment-tracker) use the fee engine instead.
"""
from typing import Any, BinaryIO, Dict, List

import pandas as pd

from app.finance.common import normalize_doc_no
from app.ingestion.column_mapping import map_columns

PAYMENT_TRACKER_ALIASES: Dict[str, List[str]] = {
    "invoice_mode": ["invoice mode"],
    "source_name": ["source"],
    "division": ["division", "branch"],
    "client_code": ["client code", "client  code", "qic code"],
    "main_policy_holder": ["main policy holder"],
    "sub_group_name": ["sub group name"],
    "policy_no": ["policy no.", "policy no", "policy number"],
    "policy_period_from": ["policy period fm dt", "policy period from date"],
    "policy_period_to": ["policy period to dt", "policy period to date"],
    "endorsement_no": ["endorsement"],
    "endorsement_type": ["endo. type", "endo type", "endorsement type"],
    "doc_date": ["doc. date", "doc date"],
    "due_date": ["due date"],
    "doc_no": ["docno.", "doc no", "docno"],
    "doc_code": ["doccode.", "doc code", "doccode"],
    "client_doc_no": ["client docno.", "client doc no"],
    "invoice_amount": ["invoice amount"],
    "premium_excl_vat": ["premium ( excl vat)", "premium excl vat", "premium"],
    "basmah": ["basmah"],
    "icp": ["icp"],
    "client_vat": ["client vat"],
    "client_payment_status": ["client payment status"],
    "healthcross_doc": ["healthcross doc"],
    "client_premium_amount_excl_tax": [
        "client premium amount ( excl tax)",
        "client premium amount excl tax",
        "premium amount settled ( excl tax)",
        "premium amount settled excl tax",
    ],
    "product": ["product"],
    "hc_fee_pct_raw": ["hc fee %", "hc fee pct"],
    "hc_fees": ["hc fees"],
    "vat_amount": ["vat 5%"],
    "total_value": ["total value"],
    "invoice_type": ["invoice type"],
    "invoice_status": ["invoice status"],
    "invoice_raised_period": ["invoiced raised", "invoice raised", "hc settlement"],
    "hc_payment_status": ["hc payment status"],
    "payment_receive_date_raw": ["payment receive date"],
}


def _infer_channel(source_name: Any) -> str:
    """"Direct"/"Direct Channel" in the Source column means direct
    business; anything else is a broker company name. There's no signal in
    this sheet for a negotiated Group/case-to-case row - that channel is
    set manually (see PaymentTrackerEntryCreate.channel) rather than
    inferred here.
    """
    if source_name is None:
        return "broker"
    normalized = str(source_name).strip().lower()
    if normalized in ("direct", "direct channel"):
        return "direct"
    return "broker"


def _str_or_none(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip() or None


def _float_or_none(value: Any) -> Any:
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# A plausibility floor for parsed dates - "Oct'25" and similar free-text
# period labels sometimes parse "successfully" via dateutil's lenient
# fallback (e.g. as day=25/month=10 of year 1) rather than failing outright,
# so a bare pd.notna() check isn't enough to trust the result.
_MIN_PLAUSIBLE_YEAR = 2000


def _date_or_none(value: Any):
    if pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(parsed) or parsed.year < _MIN_PLAUSIBLE_YEAR:
        return None
    return parsed.date()


def _fee_pct_or_none(value: Any):
    """HC Fee % is a numeric rate (e.g. 0.115) on a rate-card row, or the
    literal text "manual calc" on a negotiated/mixed-tier row - see
    app.finance.fee_engine._band_for_product for why some Products can't be
    banded automatically.
    """
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _is_manual_fee(value: Any) -> bool:
    if pd.isna(value):
        return False
    return not isinstance(value, (int, float))


def _payment_receive_fields(value: Any):
    """Payment Receive Date holds a real date on most rows but a free-text
    period label (e.g. "Oct'25") on others - return (date, note), only one
    of which is ever set.
    """
    if pd.isna(value):
        return None, None
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.notna(parsed) and parsed.year >= _MIN_PLAUSIBLE_YEAR:
        return parsed.date(), None
    return None, str(value).strip() or None


def parse_payment_tracker(file: BinaryIO, filename: str, sheet_name: Any = 0) -> List[dict]:
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file, sheet_name=sheet_name)

    df = map_columns(df, PAYMENT_TRACKER_ALIASES)

    records = []
    for _, row in df.iterrows():
        doc_no_raw = row.get("doc_no")
        # Blank rows (trailing pivot-table scratch rows in the source
        # workbook, or a stray blank line) carry no policy/doc identity at
        # all - skip rather than importing an empty entry.
        if pd.isna(doc_no_raw) and pd.isna(row.get("policy_no")):
            continue

        source_name = _str_or_none(row.get("source_name"))
        records.append(
            {
                "invoice_mode": _str_or_none(row.get("invoice_mode")),
                "source_name": source_name,
                "channel": _infer_channel(source_name),
                "division": _str_or_none(row.get("division")),
                "client_code": _str_or_none(row.get("client_code")),
                "main_policy_holder": _str_or_none(row.get("main_policy_holder")),
                "sub_group_name": _str_or_none(row.get("sub_group_name")),
                "policy_no": _str_or_none(row.get("policy_no")),
                "policy_period_from": _date_or_none(row.get("policy_period_from")),
                "policy_period_to": _date_or_none(row.get("policy_period_to")),
                "endorsement_no": _str_or_none(row.get("endorsement_no")),
                "endorsement_type": _str_or_none(row.get("endorsement_type")),
                "doc_date": _date_or_none(row.get("doc_date")),
                "due_date": _date_or_none(row.get("due_date")),
                "doc_no": normalize_doc_no(doc_no_raw),
                "doc_no_raw": _str_or_none(doc_no_raw),
                "doc_code": _str_or_none(row.get("doc_code")),
                "client_doc_no": _str_or_none(row.get("client_doc_no")),
                "invoice_amount": _float_or_none(row.get("invoice_amount")),
                "premium_excl_vat": _float_or_none(row.get("premium_excl_vat")),
                "basmah": _float_or_none(row.get("basmah")),
                "icp": _float_or_none(row.get("icp")),
                "client_vat": _float_or_none(row.get("client_vat")),
                "client_payment_status": _str_or_none(row.get("client_payment_status")),
                "healthcross_doc": _str_or_none(row.get("healthcross_doc")),
                "client_premium_amount_excl_tax": _float_or_none(row.get("client_premium_amount_excl_tax")),
                "product": _str_or_none(row.get("product")),
                "is_manual_fee": _is_manual_fee(row.get("hc_fee_pct_raw")),
                "hc_fee_pct": _fee_pct_or_none(row.get("hc_fee_pct_raw")),
                "hc_fees": _float_or_none(row.get("hc_fees")),
                "vat_amount": _float_or_none(row.get("vat_amount")),
                "total_value": _float_or_none(row.get("total_value")),
                "invoice_type": _str_or_none(row.get("invoice_type")),
                "invoice_status": _str_or_none(row.get("invoice_status")),
                "invoice_raised_period": _str_or_none(row.get("invoice_raised_period")),
                "hc_payment_status": _str_or_none(row.get("hc_payment_status")),
                **dict(
                    zip(
                        ("payment_receive_date", "payment_receive_note"),
                        _payment_receive_fields(row.get("payment_receive_date_raw")),
                    )
                ),
            }
        )
    return records
