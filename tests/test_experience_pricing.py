"""Pricing a new enquiry off its own claims -
app/scoring/rules/experience_pricing.py.

The figures below are the real Freshly Frozen Foods Factory DHA report:
108 lives at the start and 111 at the end, AED 742,182 paid over 272
days, with 11,096 reported-unpaid and 63,099 IBNR behind it.
"""
from datetime import date

import pytest

from app.scoring.rules.experience_pricing import (
    MIN_CREDIBLE_MEMBER_YEARS,
    blend_with_book,
    credibility_weight,
    incurred_claims,
    own_experience_rate,
    premium_for_target_loss_ratio,
    price_from_experience,
    report_member_years,
)

FRESHLY_FROZEN = {
    "report_period_start": date(2025, 10, 11),
    "report_period_end": date(2026, 7, 10),
    "total_paid": 742_182.0,
    "reported_not_paid": 11_096.0,
    "incurred_not_reported": 63_099.0,
    "opening_members": 108,
    "closing_members": 111,
}


# --- incurred, not paid --------------------------------------------------

def test_incurred_includes_both_reserves_not_just_what_was_settled():
    # Pricing off the paid figure alone understates every period, and
    # understates a short one most - the pipeline is never empty.
    assert incurred_claims(FRESHLY_FROZEN) == 816_377.0


def test_a_report_with_no_reserves_stated_still_gives_its_paid_figure():
    assert incurred_claims({"total_paid": 500_000.0}) == 500_000.0


def test_a_report_with_no_paid_figure_gives_nothing_rather_than_zero():
    assert incurred_claims({"reported_not_paid": 10_000.0}) is None


# --- exposure ------------------------------------------------------------

def test_exposure_averages_the_opening_and_closing_census():
    # Taking either end alone biases the rate: the opening census on a
    # growing scheme overstates cost per member, the closing understates.
    assert report_member_years(FRESHLY_FROZEN) == pytest.approx(109.5 * 272 / 365, rel=1e-6)


def test_a_report_with_only_one_census_still_measures_exposure():
    report = {**FRESHLY_FROZEN, "closing_members": None}
    assert report_member_years(report) == pytest.approx(108 * 272 / 365, rel=1e-6)


def test_an_inverted_or_missing_period_measures_nothing():
    assert report_member_years({**FRESHLY_FROZEN, "report_period_end": date(2025, 1, 1)}) is None
    assert report_member_years({**FRESHLY_FROZEN, "report_period_start": None}) is None


# --- the rate ------------------------------------------------------------

def test_the_groups_own_rate_is_incurred_over_its_own_exposure():
    own = own_experience_rate(FRESHLY_FROZEN)
    assert own["incurred_claims"] == 816_377.0
    assert own["member_years"] == 81.6
    assert round(own["claims_per_member_year"]) == 10_005
    assert round(own["annualised_claims"]) == 1_095_506


def test_a_short_report_is_flagged_rather_than_quietly_annualised():
    # One large claim in a two-month window annualises into a rate nobody
    # should price from.
    short = {**FRESHLY_FROZEN, "report_period_end": date(2025, 11, 20)}
    assert own_experience_rate(short)["long_enough_to_annualise"] is False


def test_a_thin_report_is_flagged_as_not_credible():
    thin = {**FRESHLY_FROZEN, "opening_members": 8, "closing_members": 8}
    own = own_experience_rate(thin)
    assert own["member_years"] < MIN_CREDIBLE_MEMBER_YEARS
    assert own["credible"] is False


def test_a_report_that_cannot_answer_the_question_returns_nothing():
    assert own_experience_rate({"total_paid": 500_000.0}) is None


# --- credibility ---------------------------------------------------------

def test_credibility_is_the_square_root_rule_capped_at_one():
    assert credibility_weight(100.0) == 1.0
    assert credibility_weight(400.0) == 1.0
    assert credibility_weight(25.0) == 0.5
    assert credibility_weight(0.0) == 0.0


def test_the_blend_weights_own_experience_by_its_own_exposure():
    blend = blend_with_book(10_005.0, 6_118.0, 81.6)
    assert blend["credibility"] == pytest.approx(0.9033, abs=1e-4)
    assert round(blend["blended_rate"]) == 9_629


def test_no_claims_experience_falls_back_to_the_book_not_to_zero():
    blend = blend_with_book(None, 6_118.0, 0.0)
    assert blend["blended_rate"] == 6_118.0
    assert blend["credibility"] == 0.0
    assert "book only" in blend["basis"]


def test_no_book_estimate_falls_back_to_own_experience_not_to_zero():
    blend = blend_with_book(10_005.0, None, 81.6)
    assert blend["blended_rate"] == 10_005.0
    assert blend["credibility"] == 1.0


# --- the whole build-up --------------------------------------------------

def test_the_book_alone_would_have_under_priced_this_case():
    # The finding this module exists for: the demographic estimate was
    # 660,714 and the group's own claims say 1,143,909. The quote that
    # went out matched the demographic price almost exactly.
    priced = price_from_experience(FRESHLY_FROZEN, book_expected_claims=660_714, census_size=108)
    assert round(priced["expected_claims"]) == 1_143_909
    assert priced["gap_vs_book_pct"] > 0.7


def test_a_richer_proposal_than_the_incumbents_can_be_loaded_explicitly():
    # The claims cannot tell you what plan they happened under, so the
    # uplift is the caller's number rather than one invented here.
    plain = price_from_experience(FRESHLY_FROZEN, 660_714, 108, benefit_uplift_pct=0.0)
    richer = price_from_experience(FRESHLY_FROZEN, 660_714, 108, benefit_uplift_pct=0.10)
    assert round(richer["expected_claims"] / plain["expected_claims"], 2) == 1.10


def test_the_benefit_design_caveat_is_always_stated():
    priced = price_from_experience(FRESHLY_FROZEN, 660_714, 108)
    assert any("incumbent's plan and network" in c for c in priced["caveats"])


def test_a_report_stating_no_ibnr_says_the_figure_may_be_understated():
    no_ibnr = {**FRESHLY_FROZEN, "incurred_not_reported": 0.0}
    priced = price_from_experience(no_ibnr, 660_714, 108)
    assert any("no IBNR" in c for c in priced["caveats"])


def test_no_usable_report_prices_nothing_rather_than_guessing():
    assert price_from_experience({"total_paid": 100.0}, 660_714, 108) is None


# --- the premium ---------------------------------------------------------

def test_premium_is_grossed_up_for_loading_not_marked_up():
    # premium x (1 - loading) funds claims, so the premium that funds a
    # given level of claims is claims / (1 - loading). Marking the claims
    # up by the loading instead understates the price every time.
    assert premium_for_target_loss_ratio(735_000, 0.265) == 1_000_000.0
    assert premium_for_target_loss_ratio(735_000, 0.265) > 735_000 * 1.265


def test_a_target_loss_ratio_below_one_asks_for_more_premium():
    at_100 = premium_for_target_loss_ratio(1_143_909, 0.265, 1.0)
    at_85 = premium_for_target_loss_ratio(1_143_909, 0.265, 0.85)
    assert round(at_100) == 1_556_339
    assert at_85 > at_100


def test_an_impossible_loading_prices_nothing_rather_than_dividing_by_zero():
    assert premium_for_target_loss_ratio(100_000, 1.0) is None
    assert premium_for_target_loss_ratio(0, 0.265) is None
