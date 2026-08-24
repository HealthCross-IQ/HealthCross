"""One number an underwriter can argue with, and the seven behind it.

The opportunity assessment already produces findings, but findings are
prose: an underwriter reading eight paragraphs cannot say whether this
account is worse than the one they looked at yesterday. A scorecard can,
because it is the same seven questions asked in the same order with the
same weights every time.

Two rules make it usable rather than decorative.

Higher is always safer. Every factor, without exception. A scale that
means "good" on one row and "bad" on the next is worse than no scale at
all - it gets misread in exactly the meeting where the number matters,
and nobody notices because both readings look plausible.

Every score traces to a measurement. Nothing here is a judgement typed
into a box: each factor names the figure it was derived from, so a score
can be disagreed with on its evidence rather than on its vibe. Where the
evidence is missing the factor says so and is dropped from the weighting
rather than scored at some comfortable middle value - an unmeasured
factor scored 50 is an opinion wearing a number's clothes.

Pure functions over plain dicts - no ORM, no database.
"""
import math
from typing import Dict, List, Optional

#: The seven factors, their weights, and what each one is measured from.
#: Weights are a house judgement and are meant to be argued with - they
#: live here, named, rather than buried in the arithmetic.
WEIGHTS: Dict[str, float] = {
    "claims_experience": 0.25,
    "group_size": 0.20,
    "age_profile": 0.15,
    "gender_maternity": 0.15,
    "benefit_design": 0.10,
    "chronic_pre_existing": 0.10,
    "rate_adequacy": 0.05,
}

LABELS: Dict[str, str] = {
    "claims_experience": "Claims experience",
    "group_size": "Group size",
    "age_profile": "Age profile",
    "gender_maternity": "Gender / maternity",
    "benefit_design": "Benefit design",
    "chronic_pre_existing": "Chronic / pre-existing",
    "rate_adequacy": "Rate adequacy",
}

#: Where the bands sit. Deliberately three, not five: an underwriter does
#: nothing different at 61 than at 64, and more bands invite precision
#: the inputs cannot support.
BAND_HIGH_RISK = 40.0
BAND_MEDIUM_RISK = 70.0


def band(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    if score < BAND_HIGH_RISK:
        return "high"
    if score < BAND_MEDIUM_RISK:
        return "medium"
    return "low"


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _scale(value: float, best: float, worst: float) -> float:
    """Map a measurement onto 0-100 where `best` scores 100 and `worst`
    scores 0, in whichever direction those two sit.
    """
    if best == worst:
        return 50.0
    return _clamp((value - worst) / (best - worst) * 100.0)


def claims_experience_score(own_vs_book: Optional[float]) -> Optional[dict]:
    """How this group's own claims compare with what the book expects of
    members like these.

    1.0 means it costs exactly what the book predicts. Below 1.0 is
    better than expected. The scale tops out at 2.0 because a group at
    twice its predicted cost is already as bad as this factor can say -
    stretching further would only compress the range where real accounts
    actually sit.
    """
    if own_vs_book is None:
        return None
    return {
        "score": round(_scale(own_vs_book, best=0.6, worst=2.0), 1),
        "measure": f"{own_vs_book:.2f}x the book's prediction for these members",
    }


#: Lives at which a group's own year is as well-behaved as the book's.
#: Chosen to line up with FULL_CREDIBILITY_MEMBER_YEARS (100) allowing
#: for the part-year exposure a real scheme carries, so this factor and
#: the pricing engine's credibility weight tell the same story.
FULL_CREDIBILITY_LIVES = 150.0


def group_size_score(lives: Optional[int]) -> Optional[dict]:
    """Not a higher mean, a wider spread. A small group's own year is
    decided by one or two claims, and the ones that move tend to move
    against you.

    Scored on the square-root rule rather than a straight line, because
    that is how volatility actually falls with exposure - and because it
    is the same rule the burning-cost cube and the experience blend use,
    so a group called credible over there is not called risky here.
    """
    if not lives:
        return None
    score = 100.0 * min(1.0, math.sqrt(lives / FULL_CREDIBILITY_LIVES))
    return {
        "score": round(_clamp(score), 1),
        "measure": f"{lives} lives",
    }


def age_profile_score(average_employee_age: Optional[float]) -> Optional[dict]:
    if average_employee_age is None:
        return None
    return {
        "score": round(_scale(average_employee_age, best=30.0, worst=55.0), 1),
        "measure": f"employees average {average_employee_age:.1f}",
    }


def gender_maternity_score(
    maternity_age_female_share: Optional[float],
    maternity_capped: bool,
) -> Optional[dict]:
    """Maternity-age females, and whether the benefit that makes them
    expensive is capped.

    An uncapped maternity benefit on a young female population is a
    different risk from the same population with a USD 4,000 limit, and
    the share alone cannot tell those apart.
    """
    if maternity_age_female_share is None:
        return None
    base = _scale(maternity_age_female_share, best=0.02, worst=0.35)
    # An uncapped benefit removes the ceiling on what that share costs.
    score = base if maternity_capped else base * 0.7
    return {
        "score": round(_clamp(score), 1),
        "measure": (
            f"{maternity_age_female_share:.1%} of the scheme is a maternity-age female"
            + ("; limits capped" if maternity_capped else "; maternity limit uncapped")
        ),
    }


def benefit_design_score(
    has_deductible: bool,
    pharmacy_capped: bool,
    richer_than_incumbent_count: int = 0,
    plan_designed: bool = True,
) -> Optional[dict]:
    """The controls in the plan, and what has been given away.

    Starts from a neutral design and moves for each control present or
    absent, rather than scoring the benefits themselves - a rich plan
    correctly priced is not a risk, a rich plan with no member
    contribution is.

    plan_designed=False means no plan has been chosen yet, which is not
    the same as a plan with no controls in it. Scoring an empty design
    as "no deductible, pharmacy uncapped" turns a blank form into a
    finding, and a case nobody has configured into a bad risk.
    """
    if not plan_designed:
        return None
    score = 50.0
    notes = []
    if has_deductible:
        score += 20.0
        notes.append("outpatient deductible in place")
    else:
        score -= 15.0
        notes.append("no outpatient deductible")
    if pharmacy_capped:
        score += 15.0
        notes.append("pharmacy capped")
    else:
        score -= 10.0
        notes.append("pharmacy uncapped")
    if richer_than_incumbent_count:
        score -= min(richer_than_incumbent_count * 5.0, 20.0)
        notes.append(f"{richer_than_incumbent_count} benefit line(s) richer than the incumbent's")
    return {"score": round(_clamp(score), 1), "measure": "; ".join(notes)}


def chronic_score(chronic_share_of_claims: Optional[float], covered_day_one: bool) -> Optional[dict]:
    """Chronic disease already present in the population, and whether
    there is a waiting period between it and us.

    Unlike an accident, a chronic condition does not resolve at renewal -
    it transfers with the member, and it claims from month one.
    """
    if chronic_share_of_claims is None:
        return None
    base = _scale(chronic_share_of_claims, best=0.02, worst=0.35)
    score = base if not covered_day_one else base * 0.7
    return {
        "score": round(_clamp(score), 1),
        "measure": (
            f"{chronic_share_of_claims:.0%} of claims are chronic or metabolic"
            + ("; covered from day one" if covered_day_one else "; waiting period applies")
        ),
    }


def rate_adequacy_score(quoted: Optional[float], break_even: Optional[float]) -> Optional[dict]:
    """The only factor that is about the price rather than the risk, and
    the only one an underwriter can fix in a single keystroke.
    """
    if not quoted or not break_even:
        return None
    ratio = quoted / break_even
    return {
        "score": round(_scale(ratio, best=1.30, worst=0.70), 1),
        "measure": (
            f"quoted {ratio - 1:+.0%} against break-even"
            if ratio != 1 else "quoted exactly at break-even"
        ),
    }


def build_scorecard(factors: Dict[str, Optional[dict]]) -> dict:
    """Weighted overall, with the unmeasurable left out rather than
    guessed.

    A factor with no evidence is reported as unscored and its weight is
    redistributed across the rest, so the overall is always out of the
    weights that actually had something behind them. Scoring it 50
    instead would let a missing measurement quietly pull every account
    toward the middle.
    """
    rows: List[dict] = []
    scored_weight = 0.0
    weighted_total = 0.0

    for key, weight in WEIGHTS.items():
        detail = factors.get(key)
        if detail and detail.get("score") is not None:
            scored_weight += weight
            weighted_total += weight * detail["score"]
            rows.append({
                "key": key,
                "label": LABELS[key],
                "weight": weight,
                "score": detail["score"],
                "band": band(detail["score"]),
                "measure": detail.get("measure"),
            })
        else:
            rows.append({
                "key": key,
                "label": LABELS[key],
                "weight": weight,
                "score": None,
                "band": None,
                "measure": "not measurable from the data on file",
            })

    overall = round(weighted_total / scored_weight, 1) if scored_weight else None
    return {
        "rows": rows,
        "overall_score": overall,
        "overall_band": band(overall),
        "weight_scored": round(scored_weight, 4),
        "weight_unscored": round(1.0 - scored_weight, 4),
        "weights": dict(WEIGHTS),
    }


def sensitivity(
    expected_claims: Optional[float],
    premiums: Dict[str, Optional[float]],
    loading_pct: float,
    stresses: tuple = (0.0, 0.10, 0.20, 0.30, 0.40),
) -> List[dict]:
    """What each candidate price does if claims run above expectation.

    The question a single loss ratio cannot answer: how much room is
    there before this account turns. A price that breaks even at +5%
    claims inflation is not the same offer as one that holds to +20%,
    and nothing on a quote sheet distinguishes them.
    """
    if not expected_claims:
        return []
    rows = []
    for stress in stresses:
        claims = expected_claims * (1 + stress)
        row = {"stress_pct": stress, "expected_claims": round(claims, 2), "loss_ratios": {}}
        for name, premium in premiums.items():
            funding = (premium or 0.0) * (1 - loading_pct)
            row["loss_ratios"][name] = round(claims / funding, 4) if funding > 0 else None
        rows.append(row)
    return rows


def stress_absorbed(
    expected_claims: Optional[float],
    premium: Optional[float],
    loading_pct: float,
) -> Optional[float]:
    """How much claims inflation a price absorbs before break-even.

    Negative when the price is already below break-even, which is the
    honest way to say it: there is no cushion, there is a hole.
    """
    if not expected_claims or not premium:
        return None
    funding = premium * (1 - loading_pct)
    if funding <= 0:
        return None
    return round(funding / expected_claims - 1, 4)
