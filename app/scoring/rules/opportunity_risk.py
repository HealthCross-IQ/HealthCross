"""Is this opportunity worth writing, and at what price.

The quote already says what HealthCross would charge and the burning-cost
cube already says what the members should cost. What neither says is
whether to go in hard, hold the price, or walk - and that judgement is
currently made on instinct about the broker rather than on anything in
the book.

The discipline this module exists to enforce is one distinction, because
getting it wrong is how a rate gets loaded twice:

  Some risk is ALREADY IN THE PRICE. The cube costs every member through
  product, network, age band, gender, relation and nationality zone. An
  old population, a spouse-heavy one, a restricted network - all of them
  move the risk price on their own. Loading again for "old population" is
  charging for the same thing twice, and it is the single easiest way for
  a portal like this to make pricing worse rather than better. These
  factors are reported with treatment "already_in_price" and contribute
  nothing to the required margin. They are on screen to explain the risk
  price, not to adjust it.

  Some risk is NOT in the cube, and only that may load. The cube knows
  nothing about the benefit design being proposed, about how thin the
  cells it just priced from were, or about members who will join after
  the quote is signed. Those are the honest loadings.

  And some risk is not in any data we hold - participation, the
  incumbent's loss ratio, why the client is moving. Those are reported as
  treatment "ask": open questions on the opportunity, not silent
  assumptions that everything is fine.

Every benchmark here is measured off HealthCross's own book at the time
it runs, never hardcoded, so the assessment moves as the book does.

Pure functions over plain dicts - no ORM, no database.
"""
from collections import defaultdict
from typing import Dict, List, Optional, Sequence

from app.scoring.rules.credibility import FULL_CREDIBILITY_MEMBER_YEARS

#: Editable. These are judgement calls, not measurements - kept in one
#: place and named so an underwriter can argue with them directly rather
#: than reverse-engineering them out of the arithmetic. Everything else
#: in this module is read off the book.
ASSUMPTIONS = {
    # The margin a straightforward account has to clear over its own risk
    # price before it is worth writing. Everything below is added to this.
    "base_required_margin_pct": 0.05,
    # A group whose expected cost leans on cells with little exposure
    # behind them is not more expensive - it is less known. The answer to
    # not knowing is a wider margin, not a loading.
    "thin_cell_margin_pct": 0.05,
    # Below this share of full credibility a cell counts as thin.
    "thin_cell_credibility": 0.5,
    # Small groups do not have a higher mean cost, they have a wider one -
    # and the ones that move are the ones that move against you.
    "small_group_lives": 50,
    "small_group_margin_pct": 0.05,
    # Pre-existing and chronic conditions covered from day one is the
    # largest first-year driver on a new scheme, and no burning cost
    # earned on renewing business contains it. Netted against the card's
    # own variant uplift for the same choice - see the note above.
    "pre_existing_day_one_load_pct": 0.10,
    # Each core benefit the proposal pitches materially above the
    # incumbent's. The book's cost was earned at the book's benefit
    # levels; a richer plan is used harder.
    #
    # This is a claims-cost estimate, NOT the adjustment. The rate card
    # already charges its own uplift for a richer variant - pick a higher
    # pre-existing limit and the quoted price moves on its own - so what
    # is suggested here is only the part the card has not already taken.
    # Suggesting the whole figure would charge the same buy-up twice:
    # once through the higher quote, once through the loading on top.
    "benefit_buy_up_load_pct_each": 0.05,
    "benefit_buy_up_max_load_pct": 0.20,
    # A census that would not price in full is not a census with no risk
    # in the gap.
    "unpriced_member_load_pct": 0.10,
    # A newborn is on risk for part of the policy year, not all of it.
    "newborn_part_year_exposure": 0.5,
}

#: The benefit lines a proposal is judged "richer" on. Deliberately the
#: ones that drive utilisation rather than every field in the summary -
#: an area-of-cover difference is not a buy-up in the same sense.
BUY_UP_FIELDS = (
    "annual_limit",
    "maternity_limit",
    "dental",
    "optical",
    "pharmacy_limit_and_coinsurance",
    "coinsurance",
    "deductible",
)

#: Ages at which maternity actually happens in this book. Narrow on
#: purpose: widening it to 18-40 dilutes the signal with the 36-40 tail,
#: where the book shows maternity has largely stopped.
MATERNITY_AGE_RANGE = (20, 45)

#: A newborn's first year costs multiples of any later childhood year,
#: and a single 0-17 rate band flattens all of it away. Measured, not
#: assumed - see child_cost_curve.
NEWBORN_AGE = 0
CHILD_AGE_BANDS = ((0, 0, "0 (newborn)"), (1, 4, "1-4"), (5, 9, "5-9"), (10, 17, "10-17"))

#: Below this, a rate measured off the book is quoted as an observation
#: and never used to move a price.
MIN_CREDIBLE_MEMBER_YEARS = 25.0

TREATMENT_ALREADY_PRICED = "already_in_price"
TREATMENT_LOAD = "load"
TREATMENT_WIDEN_MARGIN = "widen_margin"
TREATMENT_ASK = "ask"


def _relation(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _is_child(value: Optional[str]) -> bool:
    return _relation(value) in {"child", "children", "son", "daughter", "dependant", "dependent"}


def _is_spouse(value: Optional[str]) -> bool:
    return _relation(value) in {"spouse", "wife", "husband", "partner"}


def _is_female(value: Optional[str]) -> bool:
    return str(value or "").strip().upper().startswith("F")


def _member_years(result: dict) -> float:
    return float(result.get("earned_premium_fraction") or 0.0)


# --- what the book says (benchmarks, measured not assumed) --------------

def child_cost_curve(member_results: List[dict]) -> List[dict]:
    """Claims per member-year for children, split finer than the rate
    card's single 0-17 band.

    This is the one place the cube is structurally blind: it prices every
    child through one band, so a group of babies and a group of teenagers
    come out at the same rate. Splitting the band says by how much.
    """
    buckets: Dict[str, dict] = {
        label: {"band": label, "member_count": 0, "member_years": 0.0, "claims": 0.0}
        for _, _, label in CHILD_AGE_BANDS
    }
    for result in member_results:
        if not _is_child(result.get("relation")):
            continue
        age = result.get("age")
        if age is None:
            continue
        label = next((lbl for low, high, lbl in CHILD_AGE_BANDS if low <= age <= high), None)
        if label is None:
            continue
        bucket = buckets[label]
        bucket["member_count"] += 1
        bucket["member_years"] += _member_years(result)
        bucket["claims"] += float(result.get("actual_claims") or 0.0)

    rows = []
    for _, _, label in CHILD_AGE_BANDS:
        bucket = buckets[label]
        years = bucket["member_years"]
        rows.append({
            **bucket,
            "member_years": round(years, 1),
            "claims": round(bucket["claims"], 2),
            "claims_per_member_year": round(bucket["claims"] / years, 2) if years else None,
            "credible": years >= MIN_CREDIBLE_MEMBER_YEARS,
        })
    return rows


def maternity_rates(member_results: List[dict], maternity_claims_by_member: Dict[str, float]) -> Dict[str, dict]:
    """Maternity frequency and severity per maternity-age female, split
    by relation.

    Split by relation because the book says the two behave nothing alike:
    a female spouse is enrolled at a very different point in her life
    from a female employee, and blending them hides the fact. Frequency x
    severity rather than a single rate per member-year, because the two
    move independently - a richer maternity limit lifts severity while
    leaving frequency alone.
    """
    low, high = MATERNITY_AGE_RANGE
    buckets: Dict[str, dict] = defaultdict(
        lambda: {"member_count": 0, "member_years": 0.0, "with_maternity": 0, "maternity_claims": 0.0}
    )
    for result in member_results:
        age = result.get("age")
        if age is None or not (low <= age <= high) or not _is_female(result.get("gender")):
            continue
        relation = "spouse" if _is_spouse(result.get("relation")) else "employee"
        amount = float(maternity_claims_by_member.get(result.get("beneficiary_id")) or 0.0)
        bucket = buckets[relation]
        bucket["member_count"] += 1
        bucket["member_years"] += _member_years(result)
        bucket["maternity_claims"] += amount
        if amount > 0:
            bucket["with_maternity"] += 1

    out = {}
    for relation, bucket in buckets.items():
        years = bucket["member_years"]
        out[relation] = {
            **bucket,
            "member_years": round(years, 1),
            "maternity_claims": round(bucket["maternity_claims"], 2),
            "frequency": round(bucket["with_maternity"] / years, 4) if years else None,
            "severity": round(bucket["maternity_claims"] / bucket["with_maternity"], 2) if bucket["with_maternity"] else None,
            "cost_per_member_year": round(bucket["maternity_claims"] / years, 2) if years else None,
            "credible": years >= MIN_CREDIBLE_MEMBER_YEARS,
        }
    return out


def book_benchmarks(member_results: List[dict], maternity_claims_by_member: Dict[str, float]) -> dict:
    """Everything this module measures off the book, in one call."""
    employees = [r for r in member_results if not _is_child(r.get("relation")) and not _is_spouse(r.get("relation"))]
    children = [r for r in member_results if _is_child(r.get("relation"))]
    spouses = [r for r in member_results if _is_spouse(r.get("relation"))]
    return {
        "child_cost_curve": child_cost_curve(member_results),
        "maternity": maternity_rates(member_results, maternity_claims_by_member),
        "children_per_employee": round(len(children) / len(employees), 3) if employees else None,
        "spouses_per_employee": round(len(spouses) / len(employees), 3) if employees else None,
        "member_count": len(member_results),
    }


def _curve_rate(curve: List[dict], band_label: str) -> Optional[float]:
    row = next((r for r in curve if r["band"] == band_label and r["credible"]), None)
    return row["claims_per_member_year"] if row else None


def _blended_child_rate(curve: List[dict]) -> Optional[float]:
    """What one flat 0-17 rate comes to across the whole book - the rate
    the cube actually charges every child at.
    """
    years = sum(r["member_years"] for r in curve)
    claims = sum(r["claims"] for r in curve)
    return round(claims / years, 2) if years else None


# --- what this census says ----------------------------------------------

def child_age_finding(census_rows: List[dict], benchmarks: dict) -> Optional[dict]:
    """How much the single 0-17 band mis-prices THIS group's children.

    Not a loading formula - an arithmetic restatement. Each child is
    re-costed at their own age band's rate and compared with the flat
    rate the cube charged them at. The difference is the error the band
    width introduces, in AED, for this census.
    """
    curve = benchmarks["child_cost_curve"]
    flat_rate = _blended_child_rate(curve)
    if flat_rate is None:
        return None

    children = [r for r in census_rows if _is_child(r.get("relation"))]
    if not children:
        return None

    at_own_band = 0.0
    at_flat_rate = 0.0
    newborns = 0
    unaged = 0
    for child in children:
        age = child.get("age")
        if age is None:
            unaged += 1
            continue
        label = next((lbl for low, high, lbl in CHILD_AGE_BANDS if low <= age <= high), None)
        own_rate = _curve_rate(curve, label) if label else None
        at_own_band += own_rate if own_rate is not None else flat_rate
        at_flat_rate += flat_rate
        if age == NEWBORN_AGE:
            newborns += 1

    difference = at_own_band - at_flat_rate
    return {
        "key": "child_age_mix",
        "label": "Child age mix inside the 0-17 band",
        "treatment": TREATMENT_LOAD if difference > 0 else TREATMENT_ALREADY_PRICED,
        "child_count": len(children),
        "newborn_count": newborns,
        "children_without_an_age": unaged,
        "children_per_employee": _children_per_employee(census_rows),
        "book_children_per_employee": benchmarks["children_per_employee"],
        "flat_band_rate": flat_rate,
        "cost_at_own_age_bands": round(at_own_band, 2),
        "cost_at_flat_band_rate": round(at_flat_rate, 2),
        "difference_aed": round(difference, 2),
        "finding": (
            f"{len(children)} children price through one 0-17 band at AED {flat_rate:,.0f} each. "
            f"At their own ages the book says AED {at_own_band:,.0f}, a difference of AED {difference:,.0f}."
        ),
    }


def _children_per_employee(census_rows: List[dict]) -> Optional[float]:
    employees = [r for r in census_rows if not _is_child(r.get("relation")) and not _is_spouse(r.get("relation"))]
    children = [r for r in census_rows if _is_child(r.get("relation"))]
    return round(len(children) / len(employees), 3) if employees else None


def maternity_finding(
    census_rows: List[dict],
    benchmarks: dict,
    maternity_covered: bool,
    maternity_richer_than_incumbent: bool = False,
) -> Optional[dict]:
    """What maternity on this census is worth, in both directions.

    Two-directional on purpose. A group with no maternity benefit carries
    none of this cost, and the cube's own spouse rates - which are full
    of it - are then overstating that group. Saying so is what turns this
    into a reason to go in hard rather than only ever a reason to load.
    """
    rates = benchmarks["maternity"]
    low, high = MATERNITY_AGE_RANGE

    exposed = defaultdict(int)
    for row in census_rows:
        age = row.get("age")
        if age is None or not (low <= age <= high) or not _is_female(row.get("gender")):
            continue
        exposed["spouse" if _is_spouse(row.get("relation")) else "employee"] += 1
    if not exposed:
        return None

    expected_cost = 0.0
    expected_births = 0.0
    detail = []
    for relation, count in sorted(exposed.items()):
        rate = rates.get(relation)
        if not rate or not rate.get("credible") or rate.get("cost_per_member_year") is None:
            detail.append({"relation": relation, "members": count, "book_rate": None})
            continue
        expected_cost += count * rate["cost_per_member_year"]
        expected_births += count * (rate["frequency"] or 0.0)
        detail.append({
            "relation": relation,
            "members": count,
            "frequency": rate["frequency"],
            "severity": rate["severity"],
            "book_rate": rate["cost_per_member_year"],
        })

    if not maternity_covered:
        return {
            "key": "maternity",
            "label": "Maternity",
            "treatment": TREATMENT_ALREADY_PRICED,
            "direction": "reduces_risk",
            "maternity_age_females": dict(exposed),
            "by_relation": detail,
            "expected_cost_aed": 0.0,
            "cost_in_the_risk_price_aed": round(expected_cost, 2),
            "expected_births": round(expected_births, 2),
            "finding": (
                f"Maternity is not covered, but the book rates behind the risk price include "
                f"AED {expected_cost:,.0f} of it for this census. The risk price is overstated by "
                f"roughly that much - room to go in harder, not a loading."
            ),
        }

    return {
        "key": "maternity",
        "label": "Maternity",
        "treatment": TREATMENT_LOAD if maternity_richer_than_incumbent else TREATMENT_ALREADY_PRICED,
        "direction": "adds_risk",
        "maternity_age_females": dict(exposed),
        "by_relation": detail,
        "expected_cost_aed": round(expected_cost, 2),
        "expected_births": round(expected_births, 2),
        "finding": (
            f"{sum(exposed.values())} females of maternity age. At the book's own frequency and severity "
            f"that is AED {expected_cost:,.0f} a year and about {expected_births:.1f} births."
            + (" The maternity limit proposed is richer than the incumbent's, which lifts severity above what the book earned."
               if maternity_richer_than_incumbent else "")
        ),
    }


def newborn_pipeline_finding(maternity: Optional[dict], benchmarks: dict) -> Optional[dict]:
    """The children who arrive after the quote is signed.

    Births during the term become members mid-term, at a newborn's cost
    rather than a child's, and none of them are on the census that was
    priced. This is the one exposure on a new-business quote that is
    invisible by construction.
    """
    if not maternity or not maternity.get("expected_births"):
        return None
    curve = benchmarks["child_cost_curve"]
    newborn_rate = _curve_rate(curve, "0 (newborn)")
    flat_rate = _blended_child_rate(curve)
    if newborn_rate is None or flat_rate is None:
        return None

    births = maternity["expected_births"]
    part_year = ASSUMPTIONS["newborn_part_year_exposure"]
    cost = births * newborn_rate * part_year
    priced_as = births * flat_rate * part_year
    return {
        "key": "newborn_pipeline",
        "label": "Newborns joining mid-term",
        "treatment": TREATMENT_LOAD,
        "expected_births": births,
        "newborn_rate": newborn_rate,
        "flat_child_rate": flat_rate,
        "newborn_multiple": round(newborn_rate / flat_rate, 2) if flat_rate else None,
        "expected_cost_aed": round(cost, 2),
        "difference_vs_flat_rate_aed": round(cost - priced_as, 2),
        "finding": (
            f"About {births:.1f} births during the term become members mid-term at AED {newborn_rate:,.0f} "
            f"a year against a flat child rate of AED {flat_rate:,.0f} - "
            f"roughly AED {cost:,.0f} of cost on members who are not on the census being priced."
        ),
    }


def credibility_finding(priced_members: List[dict]) -> Optional[dict]:
    """How much of this price rests on cells that barely have any data.

    A thin cell is not more expensive, it is less known - the cube has
    already blended it toward its parent, which is the right thing to do
    and also means the price carries less information than it looks like
    it does. The answer is a wider margin, never a loading: loading a
    thin cell would be inventing a number to sit on top of a number that
    was already an estimate.
    """
    if not priced_members:
        return None
    threshold = ASSUMPTIONS["thin_cell_credibility"]
    thin = [m for m in priced_members if (m.get("credibility") or 0.0) < threshold]
    total_cost = sum(float(m.get("expected_cost") or 0.0) for m in priced_members)
    thin_cost = sum(float(m.get("expected_cost") or 0.0) for m in thin)
    share = (thin_cost / total_cost) if total_cost else 0.0
    return {
        "key": "credibility",
        "label": "Credibility of the risk price",
        "treatment": TREATMENT_WIDEN_MARGIN if share > 0.5 else TREATMENT_ALREADY_PRICED,
        "members_in_thin_cells": len(thin),
        "member_count": len(priced_members),
        "share_of_cost_from_thin_cells": round(share, 4),
        "full_credibility_member_years": FULL_CREDIBILITY_MEMBER_YEARS,
        "finding": (
            f"{share:.0%} of this group's expected cost comes from cube cells below "
            f"{threshold:.0%} credibility - blended toward their parent rather than measured."
        ),
    }


def group_size_finding(census_rows: List[dict]) -> dict:
    lives = len(census_rows)
    small = lives < ASSUMPTIONS["small_group_lives"]
    return {
        "key": "group_size",
        "label": "Group size",
        "treatment": TREATMENT_WIDEN_MARGIN if small else TREATMENT_ALREADY_PRICED,
        "lives": lives,
        "threshold": ASSUMPTIONS["small_group_lives"],
        "finding": (
            f"{lives} lives - below {ASSUMPTIONS['small_group_lives']}, so the mean is right but the "
            f"spread around it is wide, and one claim moves the year."
            if small else
            f"{lives} lives - large enough that no single claim decides the year."
        ),
    }


def data_quality_finding(census_rows: List[dict], priced_member_count: int) -> dict:
    total = len(census_rows)
    unpriced = max(total - priced_member_count, 0)
    share = (unpriced / total) if total else 0.0
    return {
        "key": "data_quality",
        "label": "Census that could be priced",
        "treatment": TREATMENT_LOAD if unpriced else TREATMENT_ALREADY_PRICED,
        "census_lives": total,
        "priced_lives": priced_member_count,
        "unpriced_lives": unpriced,
        "unpriced_share": round(share, 4),
        "finding": (
            f"{unpriced} of {total} lives did not price. They are not lives with no risk - "
            f"they are lives with no measurement."
            if unpriced else f"All {total} lives priced."
        ),
    }


def benefit_buy_up_finding(comparison_rows: List[dict]) -> Optional[dict]:
    """Benefits the proposal pitches above the incumbent's.

    The burning cost behind the risk price was earned on the book's own
    benefit levels. A plan that is richer than the one these members have
    now will be used harder than those levels imply, and no amount of
    demographic detail captures that - it is a property of the offer, not
    of the people.
    """
    if not comparison_rows:
        return None
    richer = [
        r for r in comparison_rows
        if r.get("field") in BUY_UP_FIELDS and r.get("direction") == "improved"
    ]
    leaner = [
        r for r in comparison_rows
        if r.get("field") in BUY_UP_FIELDS and r.get("direction") == "reduced"
    ]
    if not richer and not leaner:
        return None
    return {
        "key": "benefit_buy_up",
        "label": "Benefits richer than the incumbent's",
        "treatment": TREATMENT_LOAD if richer else TREATMENT_ALREADY_PRICED,
        "richer_fields": [r.get("label") for r in richer],
        "leaner_fields": [r.get("label") for r in leaner],
        "finding": (
            f"{len(richer)} benefit line(s) richer than the incumbent's"
            + (f", {len(leaner)} leaner" if leaner else "")
            + " - the book's cost was earned at the book's benefit levels."
            if richer else
            f"{len(leaner)} benefit line(s) leaner than the incumbent's and none richer."
        ),
    }


def pre_existing_finding(proposed_summary: Optional[Dict[str, str]]) -> Optional[dict]:
    value = (proposed_summary or {}).get("pre_existing_chronic_limit")
    if not value:
        return None
    covered = "not covered" not in value.lower()
    return {
        "key": "pre_existing",
        "label": "Pre-existing & chronic from day one",
        "treatment": TREATMENT_LOAD if covered else TREATMENT_ALREADY_PRICED,
        "proposed": value,
        "finding": (
            "Pre-existing and chronic conditions covered from day one - the largest first-year "
            "driver on a new scheme, and none of it is in a burning cost earned on renewing business."
            if covered else "Pre-existing and chronic conditions not covered from day one."
        ),
    }


OPEN_QUESTIONS = (
    {
        "key": "participation",
        "label": "Take-up",
        "finding": "Voluntary or compulsory? A voluntary scheme at partial take-up is a self-selected population, and nothing on the census reveals it.",
    },
    {
        "key": "incumbent_loss_ratio",
        "label": "Incumbent's loss ratio",
        "finding": "What is the scheme running at today, and is that why it is in the market?",
    },
    {
        "key": "reason_for_moving",
        "label": "Reason for moving",
        "finding": "Price, service, or a decline. The third one is the only one that changes the risk.",
    },
)


# --- putting it together ------------------------------------------------

def _margin_contributions(factors: List[dict], card_variant_uplift_pct: float = 0.0) -> List[dict]:
    """Which factors move the required margin, and by how much.

    Only "load" and "widen_margin" factors appear. Everything the cube
    already prices contributes zero by construction - that is the whole
    point of the treatment field, and it is enforced here rather than
    left to whoever reads the table.

    card_variant_uplift_pct is what the rate card has ALREADY charged for
    the benefit selections on this quote. A richer pre-existing limit is
    not a free choice on the card - picking it moves the quoted price on
    its own - so the benefit-driven suggestions are netted against it.
    Without that, the same buy-up is charged twice: once in the quote the
    card produced, and again as a loading laid on top of it.
    """
    by_key = {f["key"]: f for f in factors}
    out = []
    # Shared across the benefit-driven factors, spent in the order they
    # are applied below, so the card's uplift is credited once rather
    # than once per factor.
    remaining_card_credit = max(card_variant_uplift_pct, 0.0)

    def add(key: str, pct: float, why: str, net_against_card: bool = False):
        nonlocal remaining_card_credit
        gross = pct
        credited = 0.0
        if net_against_card and pct > 0:
            credited = min(remaining_card_credit, pct)
            remaining_card_credit -= credited
            pct = pct - credited
        if pct or credited:
            out.append({
                "key": key,
                "pct": round(pct, 4),
                "suggested_before_card_pct": round(gross, 4),
                "already_charged_by_card_pct": round(credited, 4),
                "why": why + (
                    f" - the card already charges {credited:.1%} of it through the variant uplift"
                    if credited else ""
                ),
            })

    factor = by_key.get("credibility")
    if factor and factor["treatment"] == TREATMENT_WIDEN_MARGIN:
        add("credibility", ASSUMPTIONS["thin_cell_margin_pct"],
            f"{factor['share_of_cost_from_thin_cells']:.0%} of the cost comes from thin cells")

    factor = by_key.get("group_size")
    if factor and factor["treatment"] == TREATMENT_WIDEN_MARGIN:
        add("group_size", ASSUMPTIONS["small_group_margin_pct"], f"{factor['lives']} lives")

    factor = by_key.get("pre_existing")
    if factor and factor["treatment"] == TREATMENT_LOAD:
        add("pre_existing", ASSUMPTIONS["pre_existing_day_one_load_pct"], "covered from day one",
            net_against_card=True)

    factor = by_key.get("benefit_buy_up")
    if factor and factor["treatment"] == TREATMENT_LOAD:
        pct = min(
            len(factor["richer_fields"]) * ASSUMPTIONS["benefit_buy_up_load_pct_each"],
            ASSUMPTIONS["benefit_buy_up_max_load_pct"],
        )
        add("benefit_buy_up", pct, f"{len(factor['richer_fields'])} benefit line(s) richer than the incumbent's",
            net_against_card=True)

    factor = by_key.get("data_quality")
    if factor and factor["treatment"] == TREATMENT_LOAD:
        add("data_quality", ASSUMPTIONS["unpriced_member_load_pct"] * factor["unpriced_share"],
            f"{factor['unpriced_share']:.0%} of the census did not price")

    return out


def _aed_adjustments(factors: List[dict]) -> List[dict]:
    """Factors that move the risk price by a stated amount of money
    rather than by a percentage - because the book measured them
    directly, so there is no reason to express them as a guess.
    """
    by_key = {f["key"]: f for f in factors}
    out = []

    factor = by_key.get("child_age_mix")
    if factor and factor["difference_aed"]:
        out.append({"key": "child_age_mix", "aed": factor["difference_aed"],
                    "why": "children re-costed at their own ages rather than one 0-17 band"})

    factor = by_key.get("newborn_pipeline")
    if factor:
        out.append({"key": "newborn_pipeline", "aed": factor["expected_cost_aed"],
                    "why": "newborns joining mid-term, not on the priced census"})

    factor = by_key.get("maternity")
    if factor and factor.get("direction") == "reduces_risk":
        out.append({"key": "maternity", "aed": -factor["cost_in_the_risk_price_aed"],
                    "why": "maternity not covered, but the book rates behind the price include it"})

    return out


def assess_opportunity(
    census_rows: List[dict],
    priced_members: List[dict],
    benchmarks: dict,
    risk_price_aed: Optional[float],
    quoted_price_aed: Optional[float],
    comparison_rows: Optional[List[dict]] = None,
    proposed_summary: Optional[Dict[str, str]] = None,
    maternity_covered: bool = True,
    maternity_richer_than_incumbent: bool = False,
    card_variant_uplift_pct: float = 0.0,
) -> dict:
    """Every factor, then one conclusion.

    The conclusion is a comparison of two margins, not a score. The
    required margin is built up from the factors the cube cannot see; the
    actual margin is what the quote is charging over the risk price. Which
    is bigger decides the answer, and every percentage point of the
    required margin names the factor that put it there.
    """
    factors: List[dict] = []
    for factor in (
        credibility_finding(priced_members),
        group_size_finding(census_rows),
        data_quality_finding(census_rows, len(priced_members)),
        pre_existing_finding(proposed_summary),
        benefit_buy_up_finding(comparison_rows or []),
        child_age_finding(census_rows, benchmarks),
    ):
        if factor:
            factors.append(factor)

    maternity = maternity_finding(census_rows, benchmarks, maternity_covered, maternity_richer_than_incumbent)
    if maternity:
        factors.append(maternity)
        newborns = newborn_pipeline_finding(maternity, benchmarks)
        if newborns:
            factors.append(newborns)

    contributions = _margin_contributions(factors, card_variant_uplift_pct)
    adjustments = _aed_adjustments(factors)

    required_margin = ASSUMPTIONS["base_required_margin_pct"] + sum(c["pct"] for c in contributions)
    adjusted_risk_price = (risk_price_aed + sum(a["aed"] for a in adjustments)) if risk_price_aed else None
    actual_margin = (
        (quoted_price_aed - adjusted_risk_price) / adjusted_risk_price
        if adjusted_risk_price and quoted_price_aed is not None and adjusted_risk_price > 0
        else None
    )

    return {
        "factors": factors,
        "open_questions": list(OPEN_QUESTIONS),
        "card_variant_uplift_pct": round(card_variant_uplift_pct, 4),
        "risk_price_aed": round(risk_price_aed, 2) if risk_price_aed else None,
        "aed_adjustments": adjustments,
        "adjusted_risk_price_aed": round(adjusted_risk_price, 2) if adjusted_risk_price else None,
        "quoted_price_aed": round(quoted_price_aed, 2) if quoted_price_aed is not None else None,
        "required_margin_pct": round(required_margin, 4),
        "required_margin_contributions": contributions,
        "actual_margin_pct": round(actual_margin, 4) if actual_margin is not None else None,
        "verdict": verdict(actual_margin, required_margin),
        "assumptions": dict(ASSUMPTIONS),
    }


#: The four things an underwriter can actually do with an opportunity.
VERDICT_AGGRESSIVE = "go_aggressive"
VERDICT_PRICED_RIGHT = "priced_right"
VERDICT_NEEDS_LOADING = "needs_loading"
VERDICT_DECLINE = "decline_or_reprice"
VERDICT_UNKNOWN = "not_enough_to_say"

#: How far above the required margin counts as room to move rather than
#: noise. Below it, "priced right" is the honest answer.
AGGRESSIVE_HEADROOM_PCT = 0.05


def verdict(actual_margin_pct: Optional[float], required_margin_pct: float) -> dict:
    """One of four answers, with the arithmetic that produced it.

    Deliberately four rather than a score out of a hundred: an
    underwriter does not do anything different at 71 than at 68, and a
    score invites exactly that false precision.
    """
    if actual_margin_pct is None:
        return {
            "verdict": VERDICT_UNKNOWN,
            "headline": "Not enough to say",
            "detail": "No risk price or no quote yet - the comparison this rests on cannot be made.",
        }
    if actual_margin_pct < 0:
        return {
            "verdict": VERDICT_DECLINE,
            "headline": "Below its own risk price",
            "detail": (
                f"Quoted {actual_margin_pct:.1%} against the adjusted risk price. This is not a negotiation "
                f"about margin - the price does not cover the expected claims."
            ),
        }
    if actual_margin_pct < required_margin_pct:
        return {
            "verdict": VERDICT_NEEDS_LOADING,
            "headline": f"Short by {required_margin_pct - actual_margin_pct:.1%}",
            "detail": (
                f"Carrying {actual_margin_pct:.1%} where the risk factors ask for {required_margin_pct:.1%}."
            ),
        }
    if actual_margin_pct >= required_margin_pct + AGGRESSIVE_HEADROOM_PCT:
        return {
            "verdict": VERDICT_AGGRESSIVE,
            "headline": f"Room to move: {actual_margin_pct - required_margin_pct:.1%}",
            "detail": (
                f"Carrying {actual_margin_pct:.1%} against {required_margin_pct:.1%} required - "
                f"that gap is what can be given away and still clear the risk."
            ),
        }
    return {
        "verdict": VERDICT_PRICED_RIGHT,
        "headline": "Priced about right",
        "detail": (
            f"Carrying {actual_margin_pct:.1%} against {required_margin_pct:.1%} required - "
            f"no room worth naming in either direction."
        ),
    }
