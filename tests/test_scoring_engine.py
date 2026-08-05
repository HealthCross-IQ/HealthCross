from app.scoring.engine import ScoringWeights, compute_scorecard


def _census(n=50, age=35, gender_split=True):
    members = []
    for i in range(n):
        gender = "M" if (gender_split and i % 2 == 0) else "F"
        members.append({"age": age, "gender": gender, "marital_status": "single", "relation": "employee"})
    return members


def _plan(**overrides):
    plan = {
        "annual_limit": 200_000,
        "deductible": 200,
        "co_insurance_pct": 90,
        "room_type": "semi_private",
        "network_type": "in_country",
        "maternity_covered": False,
        "dental_covered": False,
        "optical_covered": False,
        "pre_existing_covered": False,
        "chronic_covered": True,
        "member_count": 50,
    }
    plan.update(overrides)
    return plan


def test_richer_benefits_increase_composite_score():
    weights = ScoringWeights()
    census = _census()

    lean_plan = _plan()
    rich_plan = _plan(
        annual_limit=2_000_000,
        deductible=0,
        co_insurance_pct=100,
        room_type="private",
        network_type="worldwide",
        maternity_covered=True,
        pre_existing_covered=True,
    )

    lean_result = compute_scorecard(census, [lean_plan], [], "technology", weights)
    rich_result = compute_scorecard(census, [rich_plan], [], "technology", weights)

    assert rich_result["composite_score"] > lean_result["composite_score"]


def test_high_loss_ratio_pushes_score_up():
    weights = ScoringWeights()
    census = _census(n=100)
    plan = _plan(member_count=100)

    low_claims = [{"amount_paid": 100, "policy_year": 2025} for _ in range(10)]
    high_claims = [{"amount_paid": 5000, "policy_year": 2025} for _ in range(100)]

    low_result = compute_scorecard(census, [plan], low_claims, "technology", weights, estimated_annual_premium=300_000)
    high_result = compute_scorecard(census, [plan], high_claims, "technology", weights, estimated_annual_premium=300_000)

    assert high_result["composite_score"] > low_result["composite_score"]
    assert high_result["details"]["claims_experience"]["loss_ratio"] > 1.0


def test_industry_risk_influences_score():
    weights = ScoringWeights()
    census = _census()
    plan = _plan()

    low_risk_industry = compute_scorecard(census, [plan], [], "financial_services", weights)
    high_risk_industry = compute_scorecard(census, [plan], [], "mining", weights)

    assert high_risk_industry["composite_score"] > low_risk_industry["composite_score"]


def test_zone_network_multiplier_scales_with_case_network_tier():
    weights_loaded = ScoringWeights(zone_network_multipliers={"zone_2_middle_east": 1.5})
    census = [
        {"age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "nationality_zone": "zone_2_middle_east"}
    ]

    cheap_plan = _plan(network_type="Essential", member_count=1)
    rich_plan = _plan(network_type="MSH Platinum", member_count=1)

    cheap_result = compute_scorecard(census, [cheap_plan], [], "technology", weights_loaded)
    rich_result = compute_scorecard(census, [rich_plan], [], "technology", weights_loaded)

    assert rich_result["details"]["network_tier_score"] > cheap_result["details"]["network_tier_score"]
    assert rich_result["demographic_risk"] > cheap_result["demographic_risk"]


def test_score_within_bounds_and_has_tier():
    weights = ScoringWeights()
    census = _census()
    plan = _plan()

    result = compute_scorecard(census, [plan], [], "technology", weights)
    assert 0 <= result["composite_score"] <= 100
    assert result["risk_tier"] in {"Preferred", "Standard", "Substandard", "Decline/Refer"}


def test_claims_weight_is_dropped_and_redistributed_when_no_claims_exist():
    # Most New Business cases have no claims history at all (an incumbent
    # insurer's claims either don't exist or aren't shared) - claims_
    # experience_risk already returns a forced-neutral 1.0 in that case, but
    # that neutral value shouldn't still occupy 35% of the composite,
    # diluting the real demographic/benefit/industry signal down to 65%
    # of its natural weight.
    weights = ScoringWeights()  # 0.30/0.35/0.20/0.15
    census = _census()
    plan = _plan()

    no_claims_result = compute_scorecard(census, [plan], [], "technology", weights)
    used = no_claims_result["details"]["weights_used"]
    assert used["claims_experience"] == 0.0
    # The other three still sum to the full original total (1.0), just
    # redistributed proportionally rather than diluted by a dead weight.
    assert round(used["demographic"] + used["benefit_richness"] + used["industry"], 4) == 1.0
    # Proportions between the three are preserved: demographic (0.30) is
    # still 1.5x benefit_richness (0.20) after redistribution.
    assert round(used["demographic"] / used["benefit_richness"], 2) == round(0.30 / 0.20, 2)


def test_real_claims_history_keeps_the_configured_weights_unchanged():
    weights = ScoringWeights()
    census = _census(n=100)
    plan = _plan(member_count=100)
    claims = [{"amount_paid": 100, "policy_year": 2025} for _ in range(10)]

    result = compute_scorecard(census, [plan], claims, "technology", weights, estimated_annual_premium=300_000)
    used = result["details"]["weights_used"]
    assert used["claims_experience"] == weights.w_claims_experience
    assert used["demographic"] == weights.w_demographic


def test_dropping_claims_weight_amplifies_the_remaining_signal():
    # With claims weight redistributed away (not just neutrally diluting),
    # a case with an elevated demographic risk should score MORE extreme
    # (further from neutral) than if that same 35% had instead been
    # spent on a forced-neutral claims placeholder.
    weights = ScoringWeights()
    older_census = _census(n=50, age=55)  # 41-59 age band -> elevated risk
    plan = _plan()

    result = compute_scorecard(older_census, [plan], [], "technology", weights)
    raw_composite_diluted = (
        weights.w_demographic * result["demographic_risk"]
        + weights.w_claims_experience * result["claims_experience_risk"]
        + weights.w_benefit_richness * result["benefit_richness_risk"]
        + weights.w_industry * result["industry_risk"]
    )
    diluted_score = max(0.0, min(100.0, (raw_composite_diluted - 0.5) / (2.0 - 0.5) * 100))
    assert result["composite_score"] > round(diluted_score, 2)
