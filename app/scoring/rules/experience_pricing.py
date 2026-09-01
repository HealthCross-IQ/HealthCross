"""Pricing a new enquiry off its OWN claims, not only off the book.

The burning-cost cube says what members *like these* cost across the
whole book. A claims report from the incumbent says what *these actual
people* cost. Where the group is big enough for its own experience to
mean something, the second answer is worth more than the first - and
until this module existed the portal held the report and priced off the
first anyway.

The gap is not academic. On a 108-life food factory the book's
demographic estimate came to AED 660,714 a year and the group's own
claims to AED 1,095,506 - the demographic price was 36% light, and the
quote that went out matched the demographic price almost exactly.

Four things have to happen before a claims report can be compared with a
cube estimate at all, and skipping any of them biases the answer in a
known direction:

  Incurred, not paid. A report's paid figure excludes claims already
  reported but unsettled, and claims incurred that nobody has reported
  yet. Both are cost that has already happened. Pricing off paid alone
  understates every period, and understates a SHORT period most.

  Annualised on its own days. A nine-month report is not three quarters
  of a year to the nearest month, and the members on risk change between
  the opening and closing census. Both get measured rather than assumed.

  Credibility. 80 member-years is not 800. The group's own experience
  gets the weight its exposure earns and the cube's estimate takes the
  rest - the same square-root rule used everywhere else in this codebase,
  against the same FULL_CREDIBILITY_MEMBER_YEARS.

  Trend. The experience period has already happened; the policy being
  priced has not.

One thing this module deliberately does NOT do: adjust for the benefit
design the claims were incurred under. Those claims happened on the
incumbent's plan and network, and if HealthCross is proposing something
richer the experience understates. That adjustment needs the
existing-vs-proposed comparison, so it is reported as a caveat here and
applied by the caller rather than guessed at silently.

Pure functions over plain dicts - no ORM, no database.
"""
import math
from typing import Any, Dict, List, Optional

from app.scoring.rules.credibility import FULL_CREDIBILITY_MEMBER_YEARS

#: A report covering less than this is too short to annualise honestly -
#: one large claim in a two-month window annualises into a rate nobody
#: should price from. The DHA's own minimum for a mandatory report is
#: nine months, so anything under a quarter is well outside what the
#: format was designed to say.
MIN_REPORT_DAYS = 90

#: Reported below this, an own-experience rate is shown as an
#: observation and never blended into a price. It is the same threshold
#: the burning-cost cube uses for its own thin cells.
MIN_CREDIBLE_MEMBER_YEARS = 25.0

DEFAULT_TREND_PCT = 0.10


def incurred_claims(report: Dict[str, Any]) -> Optional[float]:
    """Paid + reported-but-unpaid + incurred-but-not-reported.

    A period's real cost, not the part of it that happened to have been
    settled by the report date. The two reserve figures are what the
    insurer itself says is still to come; ignoring them is not
    conservatism, it is just being wrong in one direction.
    """
    paid = report.get("total_paid")
    if paid is None:
        return None
    return float(paid) + float(report.get("reported_not_paid") or 0.0) + float(
        report.get("incurred_not_reported") or 0.0
    )


def report_member_years(report: Dict[str, Any]) -> Optional[float]:
    """Exposure the report actually covers.

    Average of the opening and closing census over the report's own days.
    Both ends are given in a DHA report precisely because the population
    moves during the period, and taking either one alone biases the rate:
    the opening census on a growing scheme overstates cost per member,
    the closing census understates it.
    """
    days = report_days(report)
    if not days:
        return None
    opening = report.get("opening_members")
    closing = report.get("closing_members")
    counts = [c for c in (opening, closing) if c]
    if not counts:
        return None
    return (sum(counts) / len(counts)) * days / 365.0


def report_days(report: Dict[str, Any]) -> Optional[int]:
    start, end = report.get("report_period_start"), report.get("report_period_end")
    if not start or not end or end <= start:
        return None
    return (end - start).days


def own_experience_rate(report: Dict[str, Any]) -> Optional[dict]:
    """This group's own claims cost per member-year, with the workings.

    Returns None only when the report cannot support the question at all
    - no paid figure, no dates, no census. Everything else is reported
    with a `credible` flag rather than silently withheld, because a rate
    that is too thin to price from is still worth an underwriter seeing.
    """
    incurred = incurred_claims(report)
    days = report_days(report)
    member_years = report_member_years(report)
    if incurred is None or not days or not member_years:
        return None

    return {
        "incurred_claims": round(incurred, 2),
        "paid": round(float(report.get("total_paid") or 0.0), 2),
        "reported_not_paid": round(float(report.get("reported_not_paid") or 0.0), 2),
        "incurred_not_reported": round(float(report.get("incurred_not_reported") or 0.0), 2),
        "report_days": days,
        "member_years": round(member_years, 1),
        "opening_members": report.get("opening_members"),
        "closing_members": report.get("closing_members"),
        "annualised_claims": round(incurred / days * 365.0, 2),
        "claims_per_member_year": round(incurred / member_years, 2),
        "long_enough_to_annualise": days >= MIN_REPORT_DAYS,
        "credible": member_years >= MIN_CREDIBLE_MEMBER_YEARS,
    }


def credibility_weight(member_years: float) -> float:
    """The square-root rule, capped at 1 - the same one the cube and the
    nationality engine use, against the same full-credibility standard.
    """
    if member_years <= 0:
        return 0.0
    return min(1.0, math.sqrt(member_years / FULL_CREDIBILITY_MEMBER_YEARS))


def blend_with_book(
    own_rate_per_member_year: Optional[float],
    book_rate_per_member_year: Optional[float],
    member_years: float,
) -> dict:
    """The group's own experience and the book's estimate, weighted by how
    much exposure stands behind the group's own.

    Falls all the way back to the book when there is no own experience,
    and all the way to own experience only when the book has nothing to
    say - never silently to zero.
    """
    weight = credibility_weight(member_years)
    if own_rate_per_member_year is None:
        return {
            "blended_rate": book_rate_per_member_year,
            "credibility": 0.0,
            "own_rate": None,
            "book_rate": book_rate_per_member_year,
            "basis": "book only - no claims experience on file",
        }
    if book_rate_per_member_year is None:
        return {
            "blended_rate": own_rate_per_member_year,
            "credibility": 1.0,
            "own_rate": own_rate_per_member_year,
            "book_rate": None,
            "basis": "own experience only - the book has no comparable estimate",
        }

    blended = weight * own_rate_per_member_year + (1 - weight) * book_rate_per_member_year
    return {
        "blended_rate": round(blended, 2),
        "credibility": round(weight, 4),
        "own_rate": round(own_rate_per_member_year, 2),
        "book_rate": round(book_rate_per_member_year, 2),
        "basis": (
            f"{weight:.0%} the group's own claims, {1 - weight:.0%} the book's estimate "
            f"for these members"
        ),
    }


def price_from_experience(
    report: Dict[str, Any],
    book_expected_claims: Optional[float],
    census_size: int,
    trend_pct: float = DEFAULT_TREND_PCT,
    benefit_uplift_pct: float = 0.0,
) -> Optional[dict]:
    """Expected claims for the coming policy year, from both sources.

    book_expected_claims is the cube's own total for this census (not a
    rate) - the two are put on the same per-member-year footing here so
    the blend is comparing like with like.

    benefit_uplift_pct is the caller's estimate of how much richer the
    proposed plan is than the one these claims were incurred on. It is a
    parameter rather than something guessed at here: the claims cannot
    tell you what plan they happened under, and inventing an adjustment
    would put a number nobody chose into the middle of a price.
    """
    own = own_experience_rate(report)
    if own is None:
        return None

    book_rate = (book_expected_claims / census_size) if (book_expected_claims and census_size) else None
    blend = blend_with_book(own["claims_per_member_year"], book_rate, own["member_years"])
    if blend["blended_rate"] is None:
        return None

    trended = blend["blended_rate"] * (1 + trend_pct) * (1 + benefit_uplift_pct)
    return {
        "own_experience": own,
        "blend": blend,
        "trend_pct": trend_pct,
        "benefit_uplift_pct": benefit_uplift_pct,
        "expected_claims_per_member": round(trended, 2),
        "expected_claims": round(trended * census_size, 2),
        "census_size": census_size,
        "book_expected_claims": round(book_expected_claims, 2) if book_expected_claims else None,
        "gap_vs_book_pct": (
            round(trended * census_size / book_expected_claims - 1, 4)
            if book_expected_claims else None
        ),
        "caveats": _caveats(own),
    }


def _caveats(own: dict) -> List[str]:
    """What an underwriter must know before using this number.

    Written out rather than left implicit, because each one biases the
    answer in a direction somebody should be able to argue with.
    """
    notes = [
        "These claims were incurred on the incumbent's plan and network. "
        "A richer proposal will be used harder than the experience implies.",
    ]
    if not own["long_enough_to_annualise"]:
        notes.append(
            f"The report covers only {own['report_days']} days - too short to annualise safely, "
            f"since a single large claim in a short window annualises into a rate nobody should price from."
        )
    if not own["credible"]:
        notes.append(
            f"{own['member_years']} member-years is thin - the blend leans on the book's estimate "
            f"rather than on this experience."
        )
    if own["incurred_not_reported"] <= 0:
        notes.append(
            "The report states no IBNR. On a period that has only just closed that is unlikely to be "
            "true, so the incurred figure here may still be understated."
        )
    return notes


#: The loss ratio HealthCross prices new business to land on.
#:
#: A house number, set by underwriting, and the single most consequential
#: parameter in the engine: it is the divisor between what a case is
#: expected to cost and what it is quoted at, so moving it moves every
#: technical price, and the house MAXIMUM - an account priced above it
#: is not carrying its own cost. It lived as a literal in five separate
#: places, which is four too many for a figure that gets revised.
#:
#: Every endpoint that prices to a target still takes it as a query
#: parameter, so a single case can be worked at a different target
#: without changing the house position.
HOUSE_TARGET_LOSS_RATIO = 0.95


def premium_for_target_loss_ratio(
    expected_claims: float,
    loading_pct: float,
    target_loss_ratio: float = 1.0,
) -> Optional[float]:
    """The gross premium that lands on a given loss ratio.

    Grossed up, not marked up: the loading is a share of the premium, so
    the claims-funding part is premium x (1 - loading) and the premium
    that funds a given level of claims is claims / (1 - loading). Marking
    the claims up by the loading instead understates the price every
    time, and understates it more the higher the loading is.
    """
    if not expected_claims or target_loss_ratio <= 0 or loading_pct >= 1:
        return None
    return round(expected_claims / target_loss_ratio / (1 - loading_pct), 2)
