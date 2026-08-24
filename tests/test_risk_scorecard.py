"""One number an underwriter can argue with -
app/scoring/rules/risk_scorecard.py.
"""
import pytest

from app.scoring.rules.risk_scorecard import (
    BAND_HIGH_RISK,
    WEIGHTS,
    age_profile_score,
    band,
    benefit_design_score,
    build_scorecard,
    chronic_score,
    claims_experience_score,
    gender_maternity_score,
    group_size_score,
    rate_adequacy_score,
    sensitivity,
    stress_absorbed,
)


def test_the_weights_are_a_hundred_percent():
    assert round(sum(WEIGHTS.values()), 6) == 1.0


# --- higher is safer, on every single factor ----------------------------

def test_every_factor_scores_higher_when_the_risk_is_lower():
    # The one property that makes the card readable. A scale that means
    # "good" on one row and "bad" on the next gets misread in exactly the
    # meeting where the number matters.
    assert claims_experience_score(0.8)["score"] > claims_experience_score(1.8)["score"]
    assert group_size_score(300)["score"] > group_size_score(25)["score"]
    assert age_profile_score(30)["score"] > age_profile_score(50)["score"]
    assert gender_maternity_score(0.05, True)["score"] > gender_maternity_score(0.30, True)["score"]
    assert chronic_score(0.05, False)["score"] > chronic_score(0.30, False)["score"]
    assert rate_adequacy_score(1_300_000, 1_000_000)["score"] > rate_adequacy_score(700_000, 1_000_000)["score"]


def test_an_uncapped_benefit_scores_worse_than_the_same_exposure_capped():
    # The share alone cannot tell a young female population with a USD
    # 4,000 maternity limit from one with no limit at all.
    assert gender_maternity_score(0.20, maternity_capped=False)["score"] < \
           gender_maternity_score(0.20, maternity_capped=True)["score"]


def test_a_waiting_period_scores_better_than_cover_from_day_one():
    assert chronic_score(0.20, covered_day_one=False)["score"] > \
           chronic_score(0.20, covered_day_one=True)["score"]


def test_scores_never_leave_the_scale():
    assert claims_experience_score(9.0)["score"] == 0.0
    assert claims_experience_score(0.1)["score"] == 100.0
    assert group_size_score(100_000)["score"] == 100.0


# --- benefit design -----------------------------------------------------

def test_controls_in_the_plan_raise_the_score_and_their_absence_lowers_it():
    strong = benefit_design_score(has_deductible=True, pharmacy_capped=True)
    weak = benefit_design_score(has_deductible=False, pharmacy_capped=False)
    assert strong["score"] > weak["score"]
    assert "deductible" in strong["measure"]


def test_buying_up_against_the_incumbent_costs_the_design_score():
    plain = benefit_design_score(True, True, richer_than_incumbent_count=0)
    richer = benefit_design_score(True, True, richer_than_incumbent_count=3)
    assert richer["score"] < plain["score"]


def test_the_buy_up_penalty_is_capped():
    many = benefit_design_score(True, True, richer_than_incumbent_count=20)
    four = benefit_design_score(True, True, richer_than_incumbent_count=4)
    assert many["score"] == four["score"]


# --- the card ------------------------------------------------------------

def _freshly_frozen():
    """The real case #32 inputs."""
    return {
        "claims_experience": claims_experience_score(1.64),
        "group_size": group_size_score(108),
        "age_profile": age_profile_score(43.1),
        "gender_maternity": gender_maternity_score(0.157, maternity_capped=True),
        "benefit_design": benefit_design_score(has_deductible=True, pharmacy_capped=False),
        "chronic_pre_existing": chronic_score(0.22, covered_day_one=True),
        "rate_adequacy": rate_adequacy_score(900_000, 1_556_339),
    }


def test_a_bad_account_scores_as_a_bad_account():
    card = build_scorecard(_freshly_frozen())
    assert card["overall_score"] < BAND_HIGH_RISK + 20
    assert card["overall_band"] in {"high", "medium"}
    # Claims experience and rate adequacy are the worst two, and both
    # are the reason this case exists.
    by_key = {r["key"]: r for r in card["rows"]}
    assert by_key["rate_adequacy"]["band"] == "high"
    assert by_key["claims_experience"]["score"] < by_key["group_size"]["score"]


def test_every_row_carries_the_measurement_it_came_from():
    # A score that cannot be traced to a figure is an opinion wearing a
    # number's clothes.
    card = build_scorecard(_freshly_frozen())
    assert all(r["measure"] for r in card["rows"])
    assert "1.64x" in next(r for r in card["rows"] if r["key"] == "claims_experience")["measure"]


def test_an_unmeasurable_factor_is_left_out_rather_than_scored_at_fifty():
    # Scoring a missing measurement at 50 would quietly pull every
    # account toward the middle and hide the gap.
    card = build_scorecard({**_freshly_frozen(), "claims_experience": None})
    row = next(r for r in card["rows"] if r["key"] == "claims_experience")
    assert row["score"] is None
    assert "not measurable" in row["measure"]
    assert card["weight_unscored"] == pytest.approx(0.25)


def test_the_overall_is_out_of_the_weights_that_actually_scored():
    only_size = build_scorecard({"group_size": group_size_score(108)})
    assert only_size["overall_score"] == group_size_score(108)["score"]
    assert only_size["weight_scored"] == pytest.approx(WEIGHTS["group_size"])


def test_nothing_measurable_gives_no_score_rather_than_zero():
    card = build_scorecard({})
    assert card["overall_score"] is None
    assert card["overall_band"] is None


def test_the_bands_are_three_not_five():
    assert band(20) == "high"
    assert band(55) == "medium"
    assert band(85) == "low"
    assert band(None) is None


# --- sensitivity ---------------------------------------------------------

def test_sensitivity_prices_every_candidate_at_every_stress():
    rows = sensitivity(1_143_909, {"quoted": 900_000, "technical": 1_830_987}, 0.265)
    assert [r["stress_pct"] for r in rows] == [0.0, 0.10, 0.20, 0.30, 0.40]
    assert round(rows[0]["loss_ratios"]["quoted"], 3) == 1.729
    assert round(rows[0]["loss_ratios"]["technical"], 2) == 0.85


def test_a_higher_stress_always_gives_a_worse_loss_ratio():
    rows = sensitivity(1_000_000, {"quoted": 1_500_000}, 0.265)
    ratios = [r["loss_ratios"]["quoted"] for r in rows]
    assert ratios == sorted(ratios)


def test_the_cushion_is_reported_as_a_hole_when_there_is_no_cushion():
    # Negative is the honest way to say it.
    assert stress_absorbed(1_143_909, 1_830_987, 0.265) > 0.15
    assert stress_absorbed(1_143_909, 900_000, 0.265) < 0


def test_no_claims_estimate_gives_no_sensitivity_rather_than_a_table_of_zeros():
    assert sensitivity(None, {"quoted": 900_000}, 0.265) == []
    assert stress_absorbed(None, 900_000, 0.265) is None


def test_an_unconfigured_plan_is_not_scored_as_a_plan_with_no_controls():
    # A blank form is not a finding. Scoring an empty design as "no
    # deductible, pharmacy uncapped" turns a case nobody has configured
    # yet into a bad risk.
    assert benefit_design_score(False, False, plan_designed=False) is None
    assert benefit_design_score(False, False, plan_designed=True)["score"] == 25.0
