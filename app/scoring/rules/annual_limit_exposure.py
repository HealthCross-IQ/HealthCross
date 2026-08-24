"""How much of the book an annual limit would actually have to pay.

Choosing an annual limit is the one benefit decision made almost entirely
on instinct. USD 1,000,000 and USD 7,500,000 price differently, and the
difference is defended with a shrug - nobody has ever hit it - because
the portfolio has never been asked. It can be: every member's own claims
are in the book, and the question "how many of them would have breached
this limit" has an exact answer.

Two figures per candidate limit, and they answer different questions:

  members at or above it - how often the limit binds at all. This is the
  number an underwriter feels: "seven members in eleven thousand".

  spend above it - what the limit would not have paid. This is the number
  that prices: a limit that binds on seven members and saves AED 40,000
  is not worth an argument with the broker, and one that binds on the
  same seven and saves AED 3.1m is the whole negotiation.

A note on the window. Claims are measured over a rolling 365 days, not
per calendar year, because an annual limit runs on the member's own
policy year and the book holds accounts renewing in every month of it.
Bucketing by calendar year would split a member whose expensive December
runs into January across two years and report neither as a breach - the
one member the report exists to find. The rolling peak asks the question
the limit actually asks: was there ever a twelve-month stretch in which
this member claimed more than this.

Pure functions over plain dicts - no ORM, no database.
"""
from collections import defaultdict
from datetime import timedelta
from typing import Dict, List, Optional, Sequence

from app.scoring.rules.benefits_comparison import AED_PER_USD

#: The limits worth asking about by default, in AED. Round numbers an
#: underwriter recognises rather than the exact rate-card options, so the
#: report reads on its own before any particular quote is attached to it.
DEFAULT_LIMITS_AED = (
    150_000.0,
    300_000.0,
    500_000.0,
    1_000_000.0,
    2_000_000.0,
    5_000_000.0,
)

#: A member with claims in only one month has a rolling-year peak equal
#: to those claims, which is correct, but their exposure to an ANNUAL
#: limit is not yet established - they have not been on risk for a year.
#: Reported separately rather than dropped: excluding them understates
#: the count of members at risk, and including them silently overstates
#: how well-observed the breach rate is.
MIN_OBSERVED_DAYS = 365


def _claim_amount(claim: dict) -> float:
    return float(claim.get("final_amount") or 0.0)


def member_rolling_year_peaks(claims: List[dict]) -> Dict[str, dict]:
    """Per member: the most they ever claimed in any 365-day window.

    A sliding window over their own claims in date order. Claims with no
    treatment date can't be placed in a window at all, so they are summed
    into the member's total and reported as unplaceable rather than
    dropped or dumped into an arbitrary window - a member whose only
    large claim is undated should not read as a member who never claimed.
    """
    by_patient: Dict[str, List[dict]] = defaultdict(list)
    for claim in claims:
        patient_id = claim.get("patient_id")
        if patient_id:
            by_patient[patient_id].append(claim)

    peaks: Dict[str, dict] = {}
    for patient_id, member_claims in by_patient.items():
        dated = sorted(
            (c for c in member_claims if c.get("date_of_treatment")),
            key=lambda c: c["date_of_treatment"],
        )
        undated_total = sum(_claim_amount(c) for c in member_claims if not c.get("date_of_treatment"))

        peak = 0.0
        window_total = 0.0
        start = 0
        for end, claim in enumerate(dated):
            window_total += _claim_amount(claim)
            cutoff = claim["date_of_treatment"] - timedelta(days=MIN_OBSERVED_DAYS - 1)
            while dated[start]["date_of_treatment"] < cutoff:
                window_total -= _claim_amount(dated[start])
                start += 1
            peak = max(peak, window_total)

        first = dated[0]["date_of_treatment"] if dated else None
        last = dated[-1]["date_of_treatment"] if dated else None
        peaks[patient_id] = {
            "patient_id": patient_id,
            "peak_rolling_year": round(peak, 2),
            "total_claims": round(sum(_claim_amount(c) for c in member_claims), 2),
            "undated_claims": round(undated_total, 2),
            "observed_days": (last - first).days + 1 if first and last else 0,
            "group_name": next((c.get("group_name") for c in member_claims if c.get("group_name")), None),
            "client_name": next((c.get("client_name") for c in member_claims if c.get("client_name")), None),
        }
    return peaks


def annual_limit_exposure(
    claims: List[dict],
    limits_aed: Sequence[float] = DEFAULT_LIMITS_AED,
) -> dict:
    """For each candidate annual limit: who breaches it and by how much.

    The counts are of members, not claim lines - an annual limit is
    breached by a person over a year, not by an invoice.
    """
    peaks = member_rolling_year_peaks(claims)
    members = list(peaks.values())
    member_count = len(members)
    fully_observed = [m for m in members if m["observed_days"] >= MIN_OBSERVED_DAYS]

    rows = []
    for limit in sorted(limits_aed):
        breaching = [m for m in members if m["peak_rolling_year"] > limit]
        spend_above = sum(m["peak_rolling_year"] - limit for m in breaching)
        rows.append({
            "limit_aed": limit,
            "members_above": len(breaching),
            "share_of_members": (len(breaching) / member_count) if member_count else 0.0,
            "spend_above_limit": round(spend_above, 2),
            "largest_breach": round(max((m["peak_rolling_year"] for m in breaching), default=0.0), 2),
        })

    return {
        "member_count": member_count,
        "fully_observed_member_count": len(fully_observed),
        "highest_peak": round(max((m["peak_rolling_year"] for m in members), default=0.0), 2),
        "rows": rows,
    }


def members_above_limit(claims: List[dict], limit_aed: float, top_n: int = 20) -> List[dict]:
    """The members a given limit would actually have cut off, biggest
    first - the names behind the count, for the conversation that follows
    it.
    """
    peaks = member_rolling_year_peaks(claims)
    breaching = [m for m in peaks.values() if m["peak_rolling_year"] > limit_aed]
    breaching.sort(key=lambda m: m["peak_rolling_year"], reverse=True)
    return [dict(m, above_limit=round(m["peak_rolling_year"] - limit_aed, 2)) for m in breaching[:top_n]]


def parse_limit_to_aed(value: Optional[str]) -> Optional[float]:
    """An annual limit as a table of benefits writes it ("US$7,500,000
    per year of insurance", "AED 5,520,000/-") as a number in AED.

    Returns None for a limit that isn't a number at all - "Covered up to
    Policy Limit", "Not specified in source document". That is a real
    answer, and it is not zero: a limit nobody stated cannot be shown as
    the harshest limit on the table.
    """
    from app.scoring.rules.benefits_comparison import extract_amount_aed

    return extract_amount_aed(value)


def exposure_for_quoted_limits(
    claims: List[dict],
    quoted_limits: Dict[str, Optional[str]],
) -> dict:
    """The same report, asked of the limits actually on this quote.

    quoted_limits maps a category to the annual limit proposed for it, as
    written. Categories whose limit doesn't parse are named rather than
    dropped, because "we could not check category B" and "category B is
    fine" are the same silence otherwise.
    """
    parsed: Dict[str, float] = {}
    unparsed: List[str] = []
    for category, written in quoted_limits.items():
        amount = parse_limit_to_aed(written)
        if amount is None:
            unparsed.append(category)
        else:
            parsed[category] = amount

    report = annual_limit_exposure(claims, sorted(set(parsed.values())) or DEFAULT_LIMITS_AED)
    by_limit = {row["limit_aed"]: row for row in report["rows"]}
    return {
        **report,
        "categories": [
            {"category": category, "limit_aed": amount, **by_limit[amount]}
            for category, amount in sorted(parsed.items())
        ],
        "categories_without_a_readable_limit": sorted(unparsed),
        "aed_per_usd": AED_PER_USD,
    }
