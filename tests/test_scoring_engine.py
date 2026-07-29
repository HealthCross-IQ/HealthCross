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


def test_score_within_bounds_and_has_tier():
    weights = ScoringWeights()
    census = _census()
    plan = _plan()

    result = compute_scorecard(census, [plan], [], "technology", weights)
    assert 0 <= result["composite_score"] <= 100
    assert result["risk_tier"] in {"Preferred", "Standard", "Substandard", "Decline/Refer"}
