"""Tests for the free-text chat assistant (app/api/routes_chat.py). The
actual Anthropic API call is never exercised here (no real key, no network
in tests) - these cover the context-building and the error paths a missing
key / bad input hit before that call would even be made.
"""
import datetime

import pytest

from app.api.routes_chat import _build_context, _case_context, _portfolio_context
from app.models import db_models as models


def test_ask_without_api_key_returns_500(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client.post("/chat/ask", json={"question": "How many cases are there?"})
    assert resp.status_code == 500
    assert "ANTHROPIC_API_KEY" in resp.json()["detail"]


def test_ask_rejects_empty_question(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    resp = client.post("/chat/ask", json={"question": "   "})
    assert resp.status_code == 400


def test_case_context_summarizes_status_and_upcoming_renewals(client):
    db = client.db_session_local()
    db.add(
        models.Case(
            broker_name="ABC Brokers",
            company_name="Test Co",
            industry="Trading",
            business_type="new",
            status=models.CaseStatus.SUBMITTED,
            renewal_date=datetime.date(2026, 10, 1),
        )
    )
    db.add(
        models.Case(
            broker_name="XYZ Brokers",
            company_name="Test Co 2",
            industry="Retail",
            business_type="existing",
            status=models.CaseStatus.BOUND,
            renewal_date=datetime.date(2026, 9, 1),
        )
    )
    db.commit()

    context = _case_context(db)
    db.close()

    assert "2 total cases" in context
    assert "'submitted': 1" in context
    assert "'bound': 1" in context
    # Sorted earliest-first, and the enum prints as its plain value, not
    # "CaseStatus.BOUND".
    assert context.index("#2 Test Co 2") < context.index("#1 Test Co")
    assert "status bound" in context
    assert "CaseStatus." not in context


def test_case_context_handles_no_cases(client):
    db = client.db_session_local()
    context = _case_context(db)
    db.close()
    assert "No cases created yet" in context


def test_portfolio_context_handles_no_portfolio_data(client):
    db = client.db_session_local()
    context = _portfolio_context(db)
    db.close()
    assert "No portfolio membership/claims data has been uploaded yet" in context


def test_build_context_combines_both_sections(client):
    db = client.db_session_local()
    context = _build_context(db)
    db.close()
    assert "PORTFOLIO BOOK" in context
    assert "CASE PIPELINE" in context
