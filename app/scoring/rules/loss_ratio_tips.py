"""Where this book's loss ratio is actually losing money, and what to do
about each finding.

Generic advice - "manage chronics", "steer to cheaper providers" - is
free and useless: it does not say whether chronics are 3% or 30% of your
book, which providers, or how much is on the table. Every tip here is
computed from HealthCross's own claims and membership, carries the
figures that produced it, and is ranked by the opportunity it represents
rather than by how important the category sounds.

Two rules govern the estimates, because a tip that overstates its own
worth is worse than no tip:

1. Every opportunity is an assumption applied to a measured base, and the
   assumption travels with the number (see each tip's `basis`). "16.4% of
   the book is pharmacy" is a fact; "a 15% reduction saves AED 480k" is
   that fact times a stated judgement, and the reader is told which is
   which.

2. Nothing is claimed for a segment too small to matter. A tip that fires
   on a category worth 0.3% of the book is noise dressed as insight, so
   each has a materiality floor and stays silent below it.

Pure functions over claim/member dicts - no ORM, no database.
"""
from collections import defaultdict
from typing import Dict, List, Optional

from app.reference.diagnosis_classification import CHRONIC, classify_diagnosis_group
from app.reference.icd10_chapters import icd10_chapter

#: Below this share of total claims a finding is not worth an
#: underwriter's attention, however striking the ratio behind it.
MIN_MATERIAL_SHARE = 0.02

#: Working assumptions for what each intervention typically recovers.
#: Deliberately conservative, deliberately parameters rather than
#: constants buried in the arithmetic - these are the numbers an
#: underwriter should argue with, and they should be able to see and
#: change them without reading the code.
ASSUMPTIONS = {
    "case_management_reduction": 0.10,      # on the top cohort's spend
    "chronic_programme_reduction": 0.15,    # on managed chronic spend
    "pharmacy_generic_reduction": 0.12,     # on pharmacy spend
    "provider_steering_reduction": 0.08,    # on steerable outpatient spend
}

#: Cohort used for the concentration tip - the share of claimants whose
#: spend a case-management programme would realistically cover.
CASE_MANAGEMENT_COHORT = 0.05

#: How disproportionate that cohort's spend has to be before it is a
#: finding at all. Materiality alone is the wrong test here: on a
#: perfectly even book the top 5% of claimants carry exactly 5% of
#: claims, which clears any share-of-book floor while being the precise
#: opposite of concentration. What matters is the MULTIPLE - the top 5%
#: carrying 40% is 8x their own weight and worth acting on; carrying 6%
#: is noise.
MIN_CONCENTRATION_MULTIPLE = 3.0

#: A provider needs this many claims in a category before its typical
#: cost is a signal rather than a small sample.
MIN_PROVIDER_CLAIMS = 30

#: How much dearer the upper-quartile provider has to be before it is
#: worth steering away from. Some spread is normal - a hospital and a
#: clinic legitimately cost different amounts for nominally the same
#: category - so a small gap is not a finding.
MIN_PROVIDER_SPREAD = 1.5


def _is_paid(status: Optional[str]) -> bool:
    s = (status or "").lower()
    return "paid" in s or "validated" in s


def _tip(
    tip_id: str,
    title: str,
    category: str,
    finding: str,
    action: str,
    opportunity_aed: Optional[float],
    basis: str,
    evidence: Optional[List[dict]] = None,
) -> dict:
    return {
        "id": tip_id,
        "title": title,
        "category": category,
        "finding": finding,
        "action": action,
        "opportunity_aed": round(opportunity_aed, 2) if opportunity_aed else None,
        "basis": basis,
        "evidence": evidence or [],
    }


def _concentration_tip(claims: List[dict], total: float, assumptions: dict) -> Optional[dict]:
    per_member: Dict[str, float] = defaultdict(float)
    for c in claims:
        if c.get("patient_id"):
            per_member[c["patient_id"]] += c.get("final_amount") or 0.0
    if not per_member:
        return None

    values = sorted(per_member.values(), reverse=True)
    cohort_size = max(1, int(len(values) * CASE_MANAGEMENT_COHORT))
    cohort_spend = sum(values[:cohort_size])
    share = cohort_spend / total if total else 0.0
    cohort_fraction = cohort_size / len(values)
    concentration = (share / cohort_fraction) if cohort_fraction else 0.0
    if share < MIN_MATERIAL_SHARE or concentration < MIN_CONCENTRATION_MULTIPLE:
        return None

    reduction = assumptions["case_management_reduction"]
    return _tip(
        "claims_concentration",
        f"{cohort_size:,} members drive {share * 100:.0f}% of your claims",
        "Case management",
        f"The heaviest {CASE_MANAGEMENT_COHORT * 100:.0f}% of claimants ({cohort_size:,} of "
        f"{len(values):,} people with any claim) account for AED {cohort_spend:,.0f} of "
        f"AED {total:,.0f}. Broad measures - benefit trims, blanket rate rises - act on the "
        f"{(1 - share) * 100:.0f}% that is not the problem.",
        "Put named case management on this cohort: care coordination, adherence follow-up, "
        "and pre-authorisation review on their recurring claims. It is a tractable list of "
        "people, not a population-wide programme.",
        cohort_spend * reduction,
        f"{reduction * 100:.0f}% of the cohort's own AED {cohort_spend:,.0f} - an assumption "
        f"about what active management recovers, applied to a measured base.",
    )


def _chronic_tip(claims: List[dict], total: float, assumptions: dict) -> Optional[dict]:
    by_chapter: Dict[str, dict] = defaultdict(lambda: {"value": 0.0, "count": 0})
    chronic_value = 0.0
    for c in claims:
        chapter = icd10_chapter(c.get("diagnosis_code"))
        if not chapter:
            continue
        amount = c.get("final_amount") or 0.0
        entry = by_chapter[chapter]
        entry["value"] += amount
        entry["count"] += 1
        if classify_diagnosis_group(chapter)["classification"] == CHRONIC:
            chronic_value += amount

    if not total or chronic_value / total < MIN_MATERIAL_SHARE:
        return None

    chronic_chapters = sorted(
        (
            {"chapter": ch, "claims": v["count"], "amount": round(v["value"], 2)}
            for ch, v in by_chapter.items()
            if classify_diagnosis_group(ch)["classification"] == CHRONIC
        ),
        key=lambda r: -r["amount"],
    )[:5]

    reduction = assumptions["chronic_programme_reduction"]
    return _tip(
        "chronic_disease",
        f"Chronic conditions are {chronic_value / total * 100:.0f}% of the book",
        "Disease management",
        f"AED {chronic_value:,.0f} sits in diagnosis chapters classified as chronic - "
        f"conditions that recur every year by definition, and that respond to management "
        f"rather than to underwriting.",
        "Stand up a structured programme on the largest chapters: adherence monitoring, "
        "scheduled review rather than episodic presentation, and pharmacy management for "
        "the maintenance drugs. Chronic spend is the most predictable part of the book and "
        "therefore the most improvable.",
        chronic_value * reduction,
        f"{reduction * 100:.0f}% of measured chronic spend. Programmes vary widely in what "
        f"they recover; treat this as the order of magnitude, not a forecast.",
        chronic_chapters,
    )


def _category_tip(claims: List[dict], total: float, assumptions: dict) -> Optional[dict]:
    by_cat: Dict[str, dict] = defaultdict(lambda: {"value": 0.0, "count": 0})
    for c in claims:
        entry = by_cat[(c.get("medical_category") or "Unclassified").strip().title()]
        entry["value"] += c.get("final_amount") or 0.0
        entry["count"] += 1

    pharmacy = by_cat.get("Pharmacy")
    if not pharmacy or not total or pharmacy["value"] / total < MIN_MATERIAL_SHARE:
        return None

    reduction = assumptions["pharmacy_generic_reduction"]
    avg = pharmacy["value"] / pharmacy["count"] if pharmacy["count"] else 0.0
    return _tip(
        "pharmacy_management",
        f"Pharmacy is {pharmacy['value'] / total * 100:.0f}% of claims - the most controllable line",
        "Benefit design",
        f"AED {pharmacy['value']:,.0f} across {pharmacy['count']:,} scripts, averaging "
        f"AED {avg:,.0f} each. Pharmacy is high-frequency and low-severity, which makes it "
        f"the line where design changes bite quickly without members losing cover.",
        "Generic substitution as default, a tiered formulary, and 90-day supply for "
        "maintenance drugs. None of these reduce what a member is entitled to - they change "
        "what the same entitlement costs to deliver.",
        pharmacy["value"] * reduction,
        f"{reduction * 100:.0f}% of measured pharmacy spend - the low end of what generic "
        f"substitution and formulary control typically deliver.",
        sorted(
            ({"category": k, "claims": v["count"], "amount": round(v["value"], 2)} for k, v in by_cat.items()),
            key=lambda r: -r["amount"],
        )[:8],
    )


def _provider_tip(claims: List[dict], total: float, assumptions: dict) -> Optional[dict]:
    """Same treatment category, wildly different cost per claim. The
    spread between providers is the clearest money on the table in the
    whole book, because steering costs a member nothing.
    """
    by_cat_provider: Dict[tuple, List[float]] = defaultdict(list)
    by_cat: Dict[str, dict] = defaultdict(lambda: {"value": 0.0, "count": 0})
    for c in claims:
        provider = (c.get("provider_name") or "").strip()
        category = (c.get("medical_category") or "").strip().title()
        if not provider or not category:
            continue
        amount = c.get("final_amount") or 0.0
        by_cat_provider[(category, provider)].append(amount)
        by_cat[category]["value"] += amount
        by_cat[category]["count"] += 1

    def _median(values: List[float]) -> float:
        ordered = sorted(values)
        mid = len(ordered) // 2
        if not ordered:
            return 0.0
        return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2

    spreads = []
    for category, cat_totals in by_cat.items():
        if not total or cat_totals["value"] / total < MIN_MATERIAL_SHARE:
            continue
        providers = [
            {
                "category": category,
                "provider": provider,
                "claims": len(amounts),
                "amount": round(sum(amounts), 2),
                # Median, not mean. A category like "Diagnostic
                # Procedures" holds both a blood test and an MRI, so one
                # provider's case mix rather than its prices can drive a
                # mean-based spread into the hundreds - a number that is
                # arithmetically true and completely useless as a
                # steering signal. The median is what a typical claim at
                # that provider costs, which is the comparison that
                # actually means something.
                "median_cost": round(_median(amounts), 2),
            }
            for (cat, provider), amounts in by_cat_provider.items()
            if cat == category and len(amounts) >= MIN_PROVIDER_CLAIMS
        ]
        if len(providers) < 4:
            continue
        providers.sort(key=lambda p: p["median_cost"])
        # Quartile providers rather than the absolute extremes, so a
        # single unusual clinic at either end cannot define the spread.
        low = providers[len(providers) // 4]
        high = providers[(len(providers) * 3) // 4]
        if not low["median_cost"]:
            continue
        spread = high["median_cost"] / low["median_cost"]
        if spread < MIN_PROVIDER_SPREAD:
            continue
        spreads.append({
            "category": category,
            "category_amount": round(cat_totals["value"], 2),
            "cheapest": low,
            "dearest": high,
            "spread": round(spread, 2),
            "provider_count": len(providers),
        })

    if not spreads:
        return None
    spreads.sort(key=lambda s: -s["category_amount"])
    worst = max(spreads, key=lambda s: s["spread"])
    steerable = sum(s["category_amount"] for s in spreads)
    reduction = assumptions["provider_steering_reduction"]

    return _tip(
        "provider_steering",
        f"A typical claim costs {worst['spread']:.1f}x more at some providers than others",
        "Network steering",
        f"In {worst['category']}, a typical claim at {worst['dearest']['provider'][:40]} "
        f"costs AED {worst['dearest']['median_cost']:,.0f} against "
        f"AED {worst['cheapest']['median_cost']:,.0f} at {worst['cheapest']['provider'][:40]} - "
        f"a {worst['spread']:.1f}x spread between the upper and lower quartile provider. "
        f"Across the categories large enough to measure, AED {steerable:,.0f} of claims sits "
        f"in this pattern.",
        "Steer volume toward the efficient end of the network: default routing in the app, "
        "differential co-pay, and renegotiation with the outliers using their own numbers. "
        "This is the one lever that costs a member nothing - same benefit, same treatment, "
        "different unit price.",
        steerable * reduction,
        f"{reduction * 100:.0f}% of the AED {steerable:,.0f} in categories where a measurable "
        f"provider spread exists. Costs are compared on the MEDIAN claim at each provider and "
        f"between quartile providers rather than the extremes, so one unusual clinic or a "
        f"heavier case mix cannot manufacture a spread. Real recovery still depends on how "
        f"much volume can actually be moved, which is a contracting question, not a claims one.",
        sorted(spreads, key=lambda s: -s["spread"])[:5],
    )


def _unattributed_provider_tip(claims: List[dict], total: float) -> Optional[dict]:
    missing = sum(
        (c.get("final_amount") or 0.0) for c in claims
        if not (c.get("provider_name") or "").strip()
    )
    if not total or missing / total < MIN_MATERIAL_SHARE:
        return None
    return _tip(
        "provider_data_gap",
        f"AED {missing:,.0f} of claims carry no provider name",
        "Data quality",
        f"{missing / total * 100:.1f}% of the book cannot be attributed to a provider, so it "
        f"is invisible to any steering or contracting analysis - including the one above, "
        f"which measures only what it can see.",
        "Fix the provider field at source with the TPA. Until it is populated, every "
        "network decision is being made on a partial view of the book.",
        None,
        "No saving is claimed - this is a measurement gap, and its value is in making the "
        "other analyses complete rather than in itself.",
    )


def _maternity_tip(claims: List[dict], total: float) -> Optional[dict]:
    maternity = sum(
        (c.get("final_amount") or 0.0) for c in claims
        if "maternity" in (c.get("ip_op_maternity") or "").lower()
        or "maternity" in (c.get("medical_category") or "").lower()
    )
    if not total or maternity / total < MIN_MATERIAL_SHARE:
        return None
    return _tip(
        "maternity_pricing",
        f"Maternity is {maternity / total * 100:.0f}% of claims - a pricing problem, not a cost one",
        "Pricing",
        f"AED {maternity:,.0f} of maternity claims. Unlike most claims, maternity is "
        f"predictable from the census before the policy starts: it follows the count of "
        f"married women of childbearing age, not chance.",
        "Price it at quote from the demographic mix rather than absorbing it as experience. "
        "The burning cost cube already carries the per-cell maternity load - use it on new "
        "business, and at renewal strip a completed pregnancy from the base while providing "
        "for the members still exposed.",
        None,
        "No cost saving is claimed - correctly priced maternity is funded rather than "
        "avoided. The gain shows up as a loss ratio that was never wrong in the first place.",
    )


def _large_claim_tip(claims: List[dict], total: float, threshold: float) -> Optional[dict]:
    per_member: Dict[str, float] = defaultdict(float)
    for c in claims:
        if c.get("patient_id"):
            per_member[c["patient_id"]] += c.get("final_amount") or 0.0
    large = {k: v for k, v in per_member.items() if v >= threshold}
    large_value = sum(large.values())
    if not total or large_value / total < MIN_MATERIAL_SHARE or not large:
        return None
    return _tip(
        "large_claims",
        f"{len(large)} members above AED {threshold:,.0f} carry {large_value / total * 100:.0f}% of claims",
        "Reinsurance",
        f"AED {large_value:,.0f} of the book sits with {len(large)} individuals. Losses this "
        f"concentrated are volatility rather than experience - one such member arriving or "
        f"leaving moves a small account's loss ratio more than any underwriting decision.",
        "Consider individual excess-of-loss cover above this level. It does not reduce claims; "
        "it stops a handful of members determining whether the year is profitable, which is "
        "what makes the rest of the book's experience readable.",
        None,
        "No saving is claimed - reinsurance trades expected cost for reduced volatility, and "
        "whether that trade is worth its premium depends on appetite, not arithmetic.",
    )


def loss_ratio_tips(
    claims: List[dict],
    account_rows: Optional[List[dict]] = None,
    assumptions: Optional[dict] = None,
    large_claim_threshold: float = 100_000.0,
) -> dict:
    """Ranked, quantified findings from this book's own claims.

    `account_rows` are account_loss_ratio_rows, used only for the
    re-pricing tip - the tips work on claims alone when they aren't
    supplied, since claims are always available and premium sometimes
    isn't.
    """
    assumptions = {**ASSUMPTIONS, **(assumptions or {})}
    total = sum((c.get("final_amount") or 0.0) for c in claims)
    if not total:
        return {"total_claims": 0.0, "tips": [], "assumptions": assumptions}

    tips = [
        _concentration_tip(claims, total, assumptions),
        _chronic_tip(claims, total, assumptions),
        _category_tip(claims, total, assumptions),
        _provider_tip(claims, total, assumptions),
        _unattributed_provider_tip(claims, total),
        _maternity_tip(claims, total),
        _large_claim_tip(claims, total, large_claim_threshold),
        _repricing_tip(account_rows or []),
    ]
    tips = [t for t in tips if t]

    # Quantified tips first, largest opportunity at the top; the
    # unquantified ones follow rather than being dropped, because "no
    # saving is claimed" is not the same as "not worth doing".
    tips.sort(key=lambda t: (t["opportunity_aed"] is None, -(t["opportunity_aed"] or 0.0)))

    quantified = sum(t["opportunity_aed"] or 0.0 for t in tips)
    return {
        "total_claims": round(total, 2),
        "tips": tips,
        "quantified_opportunity": round(quantified, 2),
        "quantified_share_of_claims": round(quantified / total, 4) if total else None,
        "assumptions": assumptions,
    }


def _repricing_tip(account_rows: List[dict]) -> Optional[dict]:
    """Accounts priced below their own cost. Unlike every other tip here
    this one needs no operational change at all - only that the next
    renewal asks for what the experience already says it should.
    """
    priced = [r for r in account_rows if r.get("net_premium") and r.get("incurred_claims") is not None]
    if not priced:
        return None
    underpriced = [r for r in priced if (r["incurred_claims"] / r["net_premium"]) > 1.0]
    if not underpriced:
        return None

    shortfall = sum(r["incurred_claims"] - r["net_premium"] for r in underpriced)
    premium = sum(r["net_premium"] for r in underpriced)
    if shortfall <= 0:
        return None

    worst = sorted(underpriced, key=lambda r: -(r["incurred_claims"] - r["net_premium"]))[:5]
    return _tip(
        "underpriced_accounts",
        f"{len(underpriced)} accounts are priced below their own claims",
        "Pricing",
        f"Together they carry AED {premium:,.0f} of net premium against "
        f"AED {sum(r['incurred_claims'] for r in underpriced):,.0f} of incurred claims - a "
        f"shortfall of AED {shortfall:,.0f} that the rest of the book is funding.",
        "Take the required increase to each at renewal rather than spreading the cost across "
        "accounts that are already paying their way. The Loss Ratio board's shed-or-reprice "
        "table gives the increase each one needs and what it does to the book.",
        shortfall,
        "The measured gap between what these accounts cost and what they pay - not an "
        "assumption. Recovering it in full assumes every client accepts their increase, "
        "which none will entirely; treat it as the size of the problem, not the answer.",
        [
            {
                "master_client": r["master_client"],
                "net_premium": r["net_premium"],
                "incurred_claims": r["incurred_claims"],
                "shortfall": round(r["incurred_claims"] - r["net_premium"], 2),
                "net_loss_ratio": round(r["incurred_claims"] / r["net_premium"], 4),
            }
            for r in worst
        ],
    )
