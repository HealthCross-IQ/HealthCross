import random

import pytest

from app.feedback.recalibration import MIN_SAMPLE_SIZE, recalibrate_weights, recalibrate_zone_multipliers
from app.reference.nationality_zones import ALL_ZONES

CURRENT_WEIGHTS = {
    "w_demographic": 0.30,
    "w_claims_experience": 0.35,
    "w_benefit_richness": 0.20,
    "w_industry": 0.15,
}

CURRENT_ZONE_MULTIPLIERS = {zone: 1.0 for zone in ALL_ZONES}


def test_weight_recalibration_refuses_with_too_few_samples():
    samples = [
        {
            "demographic_risk": 1.0,
            "claims_experience_risk": 1.0,
            "benefit_richness_risk": 1.0,
            "industry_risk": 1.0,
            "profitable": True,
        }
        for _ in range(5)
    ]
    result = recalibrate_weights(samples, CURRENT_WEIGHTS)
    assert result["recalibrated"] is False


def test_weight_recalibration_refuses_without_both_outcome_classes():
    samples = [
        {
            "demographic_risk": 1.0,
            "claims_experience_risk": 1.0,
            "benefit_richness_risk": 1.0,
            "industry_risk": 1.0,
            "profitable": True,
        }
        for _ in range(MIN_SAMPLE_SIZE)
    ]
    result = recalibrate_weights(samples, CURRENT_WEIGHTS)
    assert result["recalibrated"] is False


def test_weight_recalibration_learns_that_claims_experience_predicts_profitability():
    random.seed(42)
    samples = []
    for _ in range(200):
        claims_risk = random.uniform(0.8, 2.0)
        demographic_risk = random.uniform(0.8, 1.5)
        benefit_risk = random.uniform(0.8, 1.3)
        industry_risk = random.uniform(0.85, 1.3)
        profitable = claims_risk < 1.3  # claims experience is the true driver in this synthetic data
        samples.append(
            {
                "demographic_risk": demographic_risk,
                "claims_experience_risk": claims_risk,
                "benefit_richness_risk": benefit_risk,
                "industry_risk": industry_risk,
                "profitable": profitable,
            }
        )

    result = recalibrate_weights(samples, CURRENT_WEIGHTS)

    assert result["recalibrated"] is True
    assert sum(result["weights"].values()) == pytest.approx(1.0, abs=1e-3)
    assert result["weights"]["w_claims_experience"] > result["weights"]["w_demographic"]


def test_zone_recalibration_refuses_with_too_few_samples():
    samples = [{"zone_mix": {"zone_1_asia": 1.0}, "profitable": True} for _ in range(5)]
    result = recalibrate_zone_multipliers(samples, CURRENT_ZONE_MULTIPLIERS)
    assert result["recalibrated"] is False


def test_zone_recalibration_learns_a_riskier_zone():
    random.seed(7)
    samples = []
    for _ in range(200):
        # Cases dominated by zone_2 tend to be unprofitable; others profitable.
        zone_2_fraction = random.uniform(0.0, 1.0)
        zone_mix = {
            "zone_1_asia": (1 - zone_2_fraction) * 0.6,
            "zone_2_middle_east": zone_2_fraction,
            "zone_3_europe_americas": (1 - zone_2_fraction) * 0.4,
        }
        profitable = zone_2_fraction < 0.5
        samples.append({"zone_mix": zone_mix, "profitable": profitable})

    result = recalibrate_zone_multipliers(samples, CURRENT_ZONE_MULTIPLIERS)

    assert result["recalibrated"] is True
    assert result["multipliers"]["zone_2_middle_east"] > result["multipliers"]["zone_1_asia"]
