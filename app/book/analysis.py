"""What the book means: every member priced against the rate card with
their own claims attached, and the loss ratios read off that.

This is the one place the book is analysed. Anything that needs an
account's loss ratio CALLS account_loss_ratio_rows_for_book rather than
building its own Paid/Outstanding/IBNR figures - the Renewal Bench
scorecard used to do the latter and reported NOMADA at 75.6% while the
Portfolio Loss Ratio screen had the same account, on the same data, at
83.6%. A second implementation drifts, and the reader has no way to know
which screen to believe.

Results are cached on the uploaded data plus the arguments (see
app/book/cache.py) and must be treated as read-only by callers -
the list is shared between everyone who asked the same question.
"""
from datetime import date
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.book import cache
from app.book import repository as book
from app.scoring.rules.portfolio_analysis import (
    DEFAULT_EXPENSE_RATIO_PCT,
    account_loss_ratio_rows,
    analyze_portfolio_member,
    group_claims_by_beneficiary,
)

#: Result-level fields (present on analyze_portfolio_member's output, only
#: known after pricing/network resolution) that can be filtered on directly
#: - e.g. product=Gold AND network=... to stack more than one filter at
#: once, unlike group_by which only picks what's shown in rows.
FILTERABLE_RESULT_FIELDS = (
    "product", "network", "region", "nationality_zone",
    "gender", "relation", "category", "master_client",
)


def run_analysis(
    db: Session,
    as_of: Optional[date] = None,
    policy_year: Optional[str] = None,
    client: Optional[str] = None,
    filters: Optional[Dict[str, str]] = None,
    require_rate_card: bool = True,
) -> List[dict]:
    """Every member priced against the rate card, with their own claims.

    Cached on the uploaded data plus these arguments. A single screen -
    and a single printed report especially - asks for the same analysis
    several times over, and re-pricing the whole book each time is what
    made the report slow enough for the browser to block its own print
    tab.

    Callers must treat the returned rows as read-only: the list is
    shared between everyone who asked the same question.
    """
    key = (
        "run_analysis",
        as_of,
        policy_year,
        client,
        tuple(sorted((k, v) for k, v in (filters or {}).items() if v)),
        require_rate_card,
    )
    return cache.cached(db, key, lambda: _analyse(
        db, as_of=as_of, policy_year=policy_year, client=client,
        filters=filters, require_rate_card=require_rate_card,
    ))


def analysis_with_cube(
    db: Session,
    as_of: Optional[date] = None,
    policy_year: Optional[str] = None,
    client: Optional[str] = None,
    filters: Optional[Dict[str, str]] = None,
    require_rate_card: bool = True,
) -> tuple:
    """The analysis and the burning-cost cube built from it, both cached.

    The cube is a pure function of the analysis and the rate card, so it
    is keyed on the same arguments the analysis was - which is what lets
    one printed report build it once instead of once per endpoint it
    happens to touch.
    """
    from app.scoring.rules.burning_cost_cube import burning_cost_cube

    results = run_analysis(
        db, as_of=as_of, policy_year=policy_year, client=client,
        filters=filters, require_rate_card=require_rate_card,
    )
    key = (
        "cube",
        as_of,
        policy_year,
        client,
        tuple(sorted((k, v) for k, v in (filters or {}).items() if v)),
        require_rate_card,
    )
    cube = cache.cached(db, key, lambda: burning_cost_cube(results, book.rate_cards(db)))
    return results, cube


def _analyse(
    db: Session,
    as_of: Optional[date] = None,
    policy_year: Optional[str] = None,
    client: Optional[str] = None,
    filters: Optional[Dict[str, str]] = None,
    require_rate_card: bool = True,
) -> List[dict]:
    # NOTE: the HTTPExceptions below are the book layer reaching up into
    # HTTP, which it should not do - a "no rate card uploaded" is a fact
    # about the book, not a status code. Thirty call sites depend on the
    # 400s today, so this stays as-is until it can be changed on its own.
    members = book.members(db)
    if not members:
        raise HTTPException(status_code=400, detail="No portfolio members uploaded yet")
    if policy_year:
        members = [m for m in members
                   if m.get("policy_start_date") and str(m["policy_start_date"].year) == policy_year]
        if not members:
            raise HTTPException(status_code=400,
                                detail=f"No members found whose policy started in {policy_year}")
    if client:
        members = [m for m in members if (m.get("contract") or m.get("master_contract")) == client]
        if not members:
            raise HTTPException(status_code=400, detail=f"No members found for client '{client}'")
    rate_cards = book.rate_cards(db)
    if not rate_cards and require_rate_card:
        raise HTTPException(status_code=400, detail="No rate card uploaded yet")

    claims_by_beneficiary = group_claims_by_beneficiary(book.claims(db))
    # Read once, outside the loop. Every one of these is a query, and the
    # book runs to tens of thousands of members.
    variant_rates = book.variant_rates(db)
    group_product_by_name = book.group_product_by_name(db)
    subgroup_master_by_name = book.subgroup_master_by_name(db)

    results = [
        analyze_portfolio_member(
            m, group_product_by_name, rate_cards, variant_rates, claims_by_beneficiary,
            as_of=as_of, subgroup_master_by_name=subgroup_master_by_name,
        )
        for m in members
    ]

    for field, value in (filters or {}).items():
        if value:
            results = [r for r in results if str(r.get(field)) == value]
    return results


def account_loss_ratio_rows_for_book(
    db: Session,
    client: Optional[str] = None,
    as_of: Optional[date] = None,
    premium_basis: str = "actual",
    default_loading_pct: float = DEFAULT_EXPENSE_RATIO_PCT,
) -> List[dict]:
    """The Portfolio Loss Ratio rows, on the underwriting-year basis, for
    the whole book or one account.

    Extracted so anything that needs an account's loss ratio CALLS the
    loss ratio rather than recomputing it. The Renewal Bench scorecard
    used to build its own Paid/Outstanding/IBNR figures from a per-case
    claims ledger and reported NOMADA at 75.6% while this view had the
    same account at 83.6% - a second implementation drifts, and the
    reader has no way to know which screen to believe.
    """
    # No book uploaded is a normal state for a hand-built case, not an
    # error - run_analysis raises a 400 for it, which is right for the
    # Loss Ratio screen and wrong for a caller that falls back to its
    # own claims ledger. Checked here so callers get [] either way.
    if not book.has_members(db):
        return []
    effective_as_of = as_of or book.stored_as_of(db) or book.covered_to(db) or date.today()
    try:
        results = run_analysis(db, as_of=effective_as_of,
                               client=client, require_rate_card=False)
    except HTTPException:
        # An account that is not on the book is a 400 to the Loss Ratio
        # screen (the user asked for it by name) and an ordinary "no"
        # to a caller with its own fallback.
        return []
    if not results:
        return []
    rows = account_loss_ratio_rows(
        results,
        as_of=effective_as_of,
        opex_records_by_client=book.opex_records_by_client(db),
        default_loading_pct=default_loading_pct,
        premium_basis=premium_basis,
    )
    for row in rows:
        row["as_of"] = effective_as_of.isoformat()
    return rows
