"""A free-text Q&A assistant over HealthCross's own data - the booked
portfolio (app/scoring/rules/portfolio_analysis.py) and the case pipeline
(cases, scorecards, outcomes) - answered by an LLM (Anthropic's Claude)
given a pre-computed snapshot of both as context, rather than a fixed set
of dashboard views. Requires an ANTHROPIC_API_KEY environment variable
(see .env, loaded at startup by app/main.py) - never hardcoded, never
logged, never sent anywhere except directly to Anthropic's API.

Reuses the same portfolio-analysis machinery the Portfolio Insights
dashboard already runs (book_analysis.run_analysis et al. from routes_portfolio_analysis)
rather than re-deriving book-wide figures a second way, so the assistant's
answers are always consistent with what the dashboard itself shows.
"""
import os
from typing import Optional

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import db_models as models
from app.models import schemas
from app.scoring.rules.portfolio_analysis import (
    summarize_burning_cost_by_product_network,
    summarize_burning_cost_overall,
    summarize_population_mix,
    summarize_portfolio,
)
from app.book import repository as book_repo
from app.book import analysis as book_analysis

router = APIRouter(prefix="/chat", tags=["chat"])

CHAT_MODEL = "claude-sonnet-5"
MAX_ANSWER_TOKENS = 1024

SYSTEM_PROMPT = """You are HealthCross's underwriting portfolio assistant, answering questions \
for an internal underwriter about the company's booked insurance portfolio and case pipeline.

Answer ONLY from the DATA SNAPSHOT provided below - never invent a figure that isn't in it. \
If the data doesn't cover what's being asked, say so plainly rather than guessing or estimating. \
State currency figures in AED unless the data itself uses another currency. Be concise: a \
direct answer first, then at most a couple of sentences of relevant detail - not a full report \
unless asked for one. This snapshot is a point-in-time summary, not a live query interface - \
if a question needs a breakdown finer than what's given (e.g. one specific member), say the \
dashboard's own filters are needed for that instead of guessing.

DATA SNAPSHOT:
{context}
"""


def _pct(ratio: Optional[float]) -> str:
    return f"{ratio * 100:.1f}%" if ratio is not None else "n/a"


def _portfolio_context(db: Session) -> str:
    try:
        results = book_analysis.run_analysis(db, as_of=book_repo.stored_as_of(db))
    except HTTPException:
        return "PORTFOLIO BOOK: No portfolio membership/claims data has been uploaded yet."

    lines = ["PORTFOLIO BOOK (HealthCross's own already-booked business):"]

    for group_by, heading, row_cap in (
        ("product", "By Product", 20),
        ("network", "By Network", 20),
        ("nationality_zone", "By Nationality Zone", 20),
        ("category", "By Category", 20),
        ("master_client", "By Master Client", 150),
        ("client", "By Client / Subgroup", 150),
    ):
        rows = summarize_portfolio(results, group_by)
        if not rows:
            continue
        rows = sorted(rows, key=lambda r: -r["member_count"])
        lines.append(f"\n{heading}:")
        for row in rows[:row_cap]:
            key = row.get(group_by, "Unknown")
            lines.append(
                f"  {key}: {row['member_count']} members, standard premium AED {row['standard_premium']:,.0f}, "
                f"actual premium AED {row['actual_premium']:,.0f}, actual claims AED {row['actual_claims']:,.0f}, "
                f"loss ratio vs actual {_pct(row.get('loss_ratio_vs_actual'))}, "
                f"burning cost AED {row.get('burning_cost')}/member/year"
            )
        if len(rows) > row_cap:
            lines.append(f"  ... and {len(rows) - row_cap} more, smaller by member count - not listed here.")

    overall = summarize_burning_cost_overall(results)
    if overall:
        lines.append(
            f"\nWhole-book burning cost (all products/networks combined): "
            f"AED {overall['burning_cost']:,.2f}/member/year "
            f"({overall['member_count']} members, {overall['earned_member_years']} earned member-years)"
        )

    by_product_network = summarize_burning_cost_by_product_network(results)
    if by_product_network:
        lines.append("\nBurning cost by Product x Network (actual claims / earned member-years):")
        for row in by_product_network[:20]:
            lines.append(
                f"  {row['product']} / {row['network']}: AED {row['burning_cost']}/member/year "
                f"({row['member_count']} members)"
            )

    mix = summarize_population_mix(results)
    if mix:
        lines.append(
            f"\nBook population: avg age {mix.get('avg_age')}, "
            f"zones: {mix.get('nationality_zone_mix')}, gender: {mix.get('gender_mix')}"
        )

    return "\n".join(lines)


def _status_str(case: models.Case) -> str:
    return case.status.value if hasattr(case.status, "value") else str(case.status)


def _case_context(db: Session) -> str:
    cases = db.query(models.Case).all()
    if not cases:
        return "CASE PIPELINE: No cases created yet."

    lines = [f"CASE PIPELINE: {len(cases)} total cases."]

    by_status: dict = {}
    by_business_type: dict = {}
    for c in cases:
        by_status[_status_str(c)] = by_status.get(_status_str(c), 0) + 1
        bt = c.business_type or "unspecified"
        by_business_type[bt] = by_business_type.get(bt, 0) + 1

    lines.append(f"By status: {by_status}")
    lines.append(f"By business type: {by_business_type}")

    upcoming_renewals = sorted(
        (c for c in cases if c.renewal_date),
        key=lambda c: c.renewal_date,
    )[:15]
    if upcoming_renewals:
        lines.append("\nUpcoming renewals (earliest first):")
        for c in upcoming_renewals:
            lines.append(f"  #{c.id} {c.company_name} ({c.broker_name}) - renewal {c.renewal_date}, status {_status_str(c)}")

    outcomes = db.query(models.Outcome).all()
    if outcomes:
        bound = sum(1 for o in outcomes if o.bound)
        profitable = sum(1 for o in outcomes if o.profitable)
        lines.append(
            f"\nRecorded outcomes: {len(outcomes)} total, {bound} bound, "
            f"{len(outcomes) - bound} declined, {profitable} labeled profitable."
        )

    lines.append("\nAll cases (id, company, broker, industry, status, business_type):")
    for c in cases[:100]:
        lines.append(f"  #{c.id} {c.company_name} | {c.broker_name} | {c.industry} | {_status_str(c)} | {c.business_type}")

    return "\n".join(lines)


def _build_context(db: Session) -> str:
    return _portfolio_context(db) + "\n\n" + _case_context(db)


@router.post("/ask", response_model=schemas.ChatAnswerOut)
def ask(payload: schemas.ChatQuestionIn, db: Session = Depends(get_db)):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY isn't set on the server - add it to a local .env file and restart.",
        )
    if not payload.question or not payload.question.strip():
        raise HTTPException(status_code=400, detail="Question can't be empty")

    context = _build_context(db)
    client = anthropic.Anthropic(api_key=api_key)

    try:
        message = client.messages.create(
            model=CHAT_MODEL,
            max_tokens=MAX_ANSWER_TOKENS,
            system=SYSTEM_PROMPT.format(context=context),
            messages=[{"role": "user", "content": payload.question}],
        )
    except anthropic.APIError as exc:
        raise HTTPException(status_code=502, detail=f"Chat request failed: {exc}")

    answer = "".join(block.text for block in message.content if hasattr(block, "text"))
    return schemas.ChatAnswerOut(answer=answer)
