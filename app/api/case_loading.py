"""The loading used to price one account, and where it comes from.

A renewal is priced on the loading entered against that account, and on
nothing else. The loading is the whole difference between the claims an
account generates and the premium it is asked for, so an assumed one
means part of every figure resting on it is invented, and no screen says
which part.

That was not one rule, it was two, disagreeing. Two functions computed
"this case's total loading" from the SAME four case fields with
different fallbacks - 26.5% in the scorecard (TPA 5 + commission 10 + HC
6.5 + QIC 5, the NEW BUSINESS constants) and 33% in renewal pricing (TPA
6.5 + commission 15 + HC 6.5 + QIC 5). On a 70% loss ratio that is 95.2%
against 104.5% of the expiring premium, and which one a screen got
depended only on which module it happened to import.

Both numbers were real; they answer different questions:

  A RENEWAL has an account. Its fee split is entered per account and
  kept up to date, and that is the loading - resolved here, never
  defaulted. Where it has not been entered, the working stops and says
  so rather than quoting on an assumption.

  NEW BUSINESS has no account yet, so there is no split to enter. The
  rate card carries its own commission and product-tier fee model (see
  new_business_rating.category_loading_pct) and that is the right number
  there.

So the 26.5% stays where it belongs and stops leaking into renewals.
"""
from typing import List, Optional, Tuple

from app.models import db_models as models
from app.scoring.rules.new_business_rating import category_loading_pct
from app.scoring.rules.renewal_rating import case_loading_pct, renewal_loading_problems


def is_renewal(case: models.Case) -> bool:
    return (case.business_type or "").strip().lower() == "existing"


def renewal_loading(case: models.Case) -> Tuple[Optional[float], List[dict]]:
    """This account's own loading for a renewal working, and why there
    isn't one when there isn't.

    Returns (None, problems) when any of the four fee fields has never
    been answered - never a default. Zero is an answer; only null blocks.
    """
    fees = (case.tpa_fee_pct, case.commission_pct, case.hc_fee_pct, case.qic_fee_pct)
    problems = renewal_loading_problems(*fees)
    if problems:
        return None, problems
    return case_loading_pct(*fees), []


def scorecard_loading(case: models.Case) -> Optional[float]:
    """The loading the risk scorecard prices this case's expected cost at.

    A renewal uses its own entered split, and None when it has not been
    entered - the scorecard then reports the risk premium without a gross
    premium built on a number nobody supplied. A new business case has no
    split to enter, so the rate card's own model applies.
    """
    if is_renewal(case):
        loading, _ = renewal_loading(case)
        return loading
    return category_loading_pct("")
