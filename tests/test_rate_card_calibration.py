"""Rate card vs. what the book actually costs -
app/scoring/rules/rate_card_calibration.py.
"""
import pytest

from app.scoring.rules.burning_cost_cube import burning_cost_cube
from app.scoring.rules.rate_card_calibration import (
    calibration_summary_by_product,
    rate_card_calibration,
)

CARD = [{
    "product": "Gold", "region": "Dubai", "network": "GN", "tpa": "NAS",
    "from_age": 18, "to_age": 45,
    "male_price": 4000.0, "female_price": 5000.0, "married_female_surcharge": 3000.0,
}]


def _member(claims, gender="M", age=30, exposure=1.0, **over):
    base = {
        "in_scope": True, "product": "Gold", "network": "GN", "age": age, "gender": gender,
        "relation": "employee", "nationality_zone": "Zone 3",
        "actual_claims": claims, "earned_premium_fraction": exposure,
    }
    base.update(over)
    return base


def _cube(members):
    return burning_cost_cube(members, CARD)


def _cell(result, gender):
    return next(c for c in result["cells"] if c["gender"] == gender)


def test_implied_loss_ratio_uses_what_is_left_after_expenses_not_the_gross_price():
    # 2000 of claims against a 4000 price at 33% loading: only 2680 is
    # available to pay claims, so the cell runs at 74.6%, not 50%.
    # Comparing claims against the gross price flatters every cell by the
    # whole expense load.
    result = rate_card_calibration(CARD, _cube([_member(2000.0) for _ in range(200)]), loading_pct=0.33)
    male = _cell(result, "male")
    assert male["available_for_claims"] == pytest.approx(2680.0)
    assert male["implied_loss_ratio"] == pytest.approx(0.7463, abs=0.001)


def test_a_cell_that_costs_more_than_it_charges_is_flagged_underpriced():
    result = rate_card_calibration(CARD, _cube([_member(5000.0) for _ in range(200)]), loading_pct=0.33)
    male = _cell(result, "male")
    assert male["implied_loss_ratio"] > 1.0
    assert male["verdict"] == "underpriced"
    assert male["suggested_price"] > male["card_price"]
    assert male["price_change_pct"] > 0


def test_the_suggested_price_lands_the_cell_on_its_target():
    result = rate_card_calibration(
        CARD, _cube([_member(5000.0) for _ in range(200)]), loading_pct=0.33, target_loss_ratio=0.85
    )
    male = _cell(result, "male")
    # Re-deriving the loss ratio from the suggested price returns the target.
    assert male["expected_cost"] / (male["suggested_price"] * (1 - 0.33)) == pytest.approx(0.85, abs=0.001)


def test_a_cell_priced_far_above_its_cost_is_flagged_too():
    # Leaving business on the table is a finding as much as losing money.
    result = rate_card_calibration(CARD, _cube([_member(500.0) for _ in range(200)]), loading_pct=0.33)
    assert _cell(result, "male")["verdict"] == "overpriced"


def test_married_female_is_compared_against_the_surcharged_price():
    result = rate_card_calibration(CARD, _cube([_member(2000.0, gender="F") for _ in range(200)]), loading_pct=0.33)
    female = _cell(result, "female")
    married = _cell(result, "married_female")
    assert female["card_price"] == 5000.0
    assert married["card_price"] == 8000.0   # 5000 + 3000 surcharge
    # Same underlying cost, so the surcharged cell runs at a lower ratio.
    assert married["implied_loss_ratio"] < female["implied_loss_ratio"]


def test_a_card_with_no_surcharge_produces_no_married_female_cell():
    card = [{**CARD[0], "married_female_surcharge": None}]
    result = rate_card_calibration(card, _cube([_member(2000.0) for _ in range(200)]), loading_pct=0.33)
    assert "married_female" not in {c["gender"] for c in result["cells"]}


def test_thin_cells_are_reported_but_not_counted_as_findings():
    # One member-year behind the cell - a mispricing claim resting on that
    # is noise, however dramatic the ratio.
    result = rate_card_calibration(
        CARD, _cube([_member(50_000.0, exposure=1.0)]), loading_pct=0.33, min_exposure_member_years=5.0
    )
    male = _cell(result, "male")
    assert male["thin"] is True
    assert result["underpriced_count"] == 0
    assert result["thin_cell_count"] > 0


def test_a_cell_with_no_experience_of_its_own_falls_back_and_says_so():
    # The card prices 18-45 Gold; the book only has claims for a different
    # network, so the cell has to lean on something broader.
    cube = _cube([_member(3000.0, network="OTHER") for _ in range(200)])
    result = rate_card_calibration(CARD, cube, loading_pct=0.33)
    assert all(c["fell_back"] for c in result["cells"])
    assert all(c["expected_cost"] is not None for c in result["cells"])


def test_rows_without_an_age_band_are_skipped_rather_than_guessed_at():
    card = [{**CARD[0], "from_age": None, "to_age": None}]
    result = rate_card_calibration(card, _cube([_member(2000.0) for _ in range(200)]), loading_pct=0.33)
    assert result["cells"] == []


def test_cells_are_ranked_worst_first():
    card = [
        {**CARD[0], "from_age": 18, "to_age": 45, "male_price": 4000.0, "female_price": None, "married_female_surcharge": None},
        {**CARD[0], "from_age": 46, "to_age": 60, "male_price": 500.0, "female_price": None, "married_female_surcharge": None},
    ]
    members = ([_member(2000.0, age=30) for _ in range(200)]
               + [_member(2000.0, age=50) for _ in range(200)])
    result = rate_card_calibration(card, burning_cost_cube(members, card), loading_pct=0.33)
    ratios = [c["implied_loss_ratio"] for c in result["cells"]]
    assert ratios == sorted(ratios, reverse=True)


def test_a_loading_of_one_hundred_percent_is_refused():
    with pytest.raises(ValueError):
        rate_card_calibration(CARD, _cube([_member(2000.0)]), loading_pct=1.0)


def test_product_summary_counts_how_much_of_the_grid_is_mispriced():
    # 5,000 of cost against male 4,000 / female 5,000 / married 8,000 at
    # 33% loading: the first two cannot fund it, the surcharged one can.
    # So two thirds of the grid is underpriced, not all of it - which is
    # exactly the kind of thing the summary exists to say.
    result = rate_card_calibration(CARD, _cube([_member(5000.0) for _ in range(200)]), loading_pct=0.33)
    summary = calibration_summary_by_product(result)
    assert summary[0]["product"] == "Gold"
    assert summary[0]["measurable_cells"] == 3
    assert summary[0]["underpriced_cells"] == 2
    assert summary[0]["underpriced_share"] == pytest.approx(2 / 3, abs=0.001)
    assert _cell(result, "married_female")["verdict"] == "in range"


def test_product_summary_ignores_thin_cells_like_the_headline_counts_do():
    result = rate_card_calibration(
        CARD, _cube([_member(50_000.0)]), loading_pct=0.33, min_exposure_member_years=5.0
    )
    assert calibration_summary_by_product(result) == []


def test_each_product_is_calibrated_at_its_own_loading_when_none_is_given():
    # A Gold row and a Bronze row on one card: Gold at 30%, Bronze at
    # 26.5%. One flat loading would misstate one of them.
    card = CARD + [{**CARD[0], "product": "Bronze"}]
    members = [_member(2000.0) for _ in range(150)] + [_member(2000.0, product="Bronze") for _ in range(150)]
    result = rate_card_calibration(card, burning_cost_cube(members, card))
    by_product = {(c["product"], c["gender"]): c for c in result["cells"]}
    assert by_product[("Gold", "male")]["loading_pct"] == pytest.approx(0.30)
    assert by_product[("Bronze", "male")]["loading_pct"] == pytest.approx(0.265)
    assert result["loading_pct"] is None
    assert result["loading_by_product"] == {"Gold": pytest.approx(0.30), "Bronze": pytest.approx(0.265)}
