"""Keeping a census's ages honest about which date they were struck at.

Age is derived once, when a census file is uploaded, from whatever the
case's policy_start_date held at that moment - and then stored. Nothing
recomputed it afterwards, so setting the policy start date AFTER
uploading left every age a year out with nothing on screen saying so.
The field's own hint read "Policy start date is used to work out member
ages", present tense, as though it were live. It was not.

On KIKO MILANO that put two members in the 18-40 band who belong in
41-59: AED 16,250 of expiring premium not charged, and about 27,000 off
the ask once the renewal increase was applied to the understated base.

Two halves, both here so they cannot disagree about what "stale" means:
recompute, and report.
"""
from datetime import date
from typing import List, Optional

from sqlalchemy.orm import Session

from app.ingestion.census import _calc_age
from app.models import db_models as models


#: The bands the Member Rates grid buckets by - mirrors RATE_AGE_BANDS in
#: app/static/index.html. A member only costs something different when
#: they cross one of these, which is what makes a stale age basis worth
#: reporting rather than merely untidy.
RATE_AGE_BANDS = [(0, 17), (18, 40), (41, 59), (60, 999)]


def _rate_band(age: Optional[int]) -> Optional[tuple]:
    if age is None:
        return None
    for low, high in RATE_AGE_BANDS:
        if low <= age <= high:
            return (low, high)
    return None


def reage_census_records(case: models.Case, as_of: Optional[date], db: Session) -> int:
    """Re-derive every census age from its own date of birth, against
    `as_of`. Returns how many rows changed.

    Only rows carrying a date_of_birth are touched. An age that came from
    the file's own Age column has no DOB behind it and no basis we know
    of - re-deriving it would be inventing one, so it is left exactly as
    the broker supplied it.
    """
    if as_of is None:
        return 0
    changed = 0
    for record in case.census_records:
        if record.date_of_birth is None:
            continue
        new_age = _calc_age(record.date_of_birth, as_of)
        if record.age != new_age or record.age_as_of != as_of:
            record.age = new_age
            record.age_as_of = as_of
            changed += 1
    if changed:
        db.commit()
    return changed


def renewal_term_looks_wrong(case: models.Case, today: Optional[date] = None) -> Optional[dict]:
    """A renewal being priced should carry the term it is being priced
    INTO, which is in the future.

    KIKO's policy start date was the EXPIRING term - a year behind - so
    every age was struck at the year that was ending rather than the one
    being quoted. That is not a typo anyone would spot: the date looks
    perfectly reasonable, it is simply last year's.
    """
    today = today or date.today()
    if (case.business_type or "").strip().lower() != "existing":
        return None
    if not case.policy_start_date or case.policy_start_date >= today:
        return None
    days = (today - case.policy_start_date).days
    return {
        "policy_start_date": case.policy_start_date.isoformat(),
        "days_in_the_past": days,
        "message": (
            f"This renewal's policy start date, "
            f"{case.policy_start_date.strftime('%d %b %Y')}, is {days} days in the past. "
            f"A renewal is priced into the term it is starting, so this is usually the "
            f"EXPIRING term rather than the one being quoted - which ages every member a "
            f"year young and can put them in a cheaper rate band than they belong in."
        ),
    }


def stale_age_basis(case: models.Case) -> Optional[dict]:
    """Whether this case's stored ages were struck at a different date to
    the one the case now carries, and what that costs.

    Returns None when there is nothing to say - no census, no policy start
    date, no re-derivable ages, or the two already agree.
    """
    if not case.policy_start_date or not case.census_records:
        return None

    bases: List[date] = sorted({
        r.age_as_of for r in case.census_records if r.age_as_of is not None
    })
    if not bases or bases == [case.policy_start_date]:
        return None

    # How many members move RATE BAND, not how many change age. Across a
    # year almost everyone changes age and it means nothing; what costs
    # money is crossing a band boundary, because the band is what carries
    # the rate. On KIKO all three of a sample changed age and only two
    # changed band - and it was the two that mattered.
    would_change = sum(
        1 for r in case.census_records
        if r.date_of_birth is not None
        and _rate_band(r.age)
        != _rate_band(_calc_age(r.date_of_birth, case.policy_start_date))
    )
    return {
        "age_basis": [b.isoformat() for b in bases],
        "members_whose_band_changes": would_change,
        "case_policy_start_date": case.policy_start_date.isoformat(),
        "message": (
            f"These ages were worked out as of "
            f"{', '.join(b.strftime('%d %b %Y') for b in bases)}, but this case's policy start "
            f"date is {case.policy_start_date.strftime('%d %b %Y')}. "
            + (f"{would_change} member{'s' if would_change != 1 else ''} would move to a "
               f"different rate band, changing their rate and this account's premium. "
               if would_change else "No member changes rate band, so no rate moves. ")
            + "Save the policy start date again to re-derive them from each member's date of birth."
        ),
    }
