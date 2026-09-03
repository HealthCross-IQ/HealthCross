"""Small, purely-derived pieces the Renewal Bench Executive Summary needs
that don't exist anywhere else in the codebase - each one a straight
computation over numbers `renewal_rating.py`/`renewal_options.py`/
`renewal_bench_metrics.py`/`census_summary.py` already produce, never a
new pricing decision of its own. Nothing here invents a methodology this
account's price was not already built from - see those modules' own
docstrings for why that matters on a renewal.
"""
from typing import List, Optional


def per_member_premium(total_premium: Optional[float], member_count: Optional[int]) -> Optional[float]:
    if not total_premium or not member_count:
        return None
    return round(total_premium / member_count, 2)


def age_threshold_percentages(census: List[dict]) -> dict:
    """% of members over 50 and over 60, and the Male:Female ratio as a
    "M:F" string - none of which census_demographic_summary() computes as
    a named field (it bands ages into the risk-scoring AGE_BANDS instead,
    a different cut than the two thresholds this dashboard shows).
    """
    ages = [m["age"] for m in census if m.get("age") is not None]
    total = len(census)
    over_50 = sum(1 for a in ages if a > 50)
    over_60 = sum(1 for a in ages if a > 60)
    male = sum(1 for m in census if m.get("gender") == "M")
    female = sum(1 for m in census if m.get("gender") == "F")

    ratio = None
    if male or female:
        gcd = _gcd(male, female) or 1
        ratio = f"{male // gcd}:{female // gcd}" if (male and female) else (f"{male}:0" if male else f"0:{female}")

    return {
        "pct_over_50": round(over_50 / total, 4) if total else None,
        "pct_over_60": round(over_60 / total, 4) if total else None,
        "male_count": male,
        "female_count": female,
        "male_female_ratio": ratio,
    }


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def renewal_increase_reason(drivers: dict) -> str:
    """One plain-English line explaining the required increase, built only
    from renewal_bench_metrics.renewal_drivers()'s own already-computed
    percentages (claims_experience_pct, medical_trend_pct, floor_pct,
    inflation_pts) - never re-deriving them, so this sentence can never
    disagree with the waterfall it is describing.
    """
    parts = []
    claims_pct = drivers.get("claims_experience_pct")
    if claims_pct is not None:
        parts.append(f"claims experience ({claims_pct:+.1f}%)")
    inflation_pts = drivers.get("inflation_pts")
    if inflation_pts:
        parts.append(f"claim inflation ({inflation_pts:.1%})")
    if drivers.get("floor_applied"):
        parts.append("the house minimum renewal increase")
    else:
        parts.append("expense & risk loading")

    if not parts:
        return "The required increase reflects this account's own claims experience."
    if len(parts) == 1:
        reason = parts[0]
    elif len(parts) == 2:
        reason = f"{parts[0]} and {parts[1]}"
    else:
        reason = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    return f"The required increase is mainly due to {reason}."
