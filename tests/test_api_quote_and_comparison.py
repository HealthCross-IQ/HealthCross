from app.models import db_models as models


def _create_case(client):
    resp = client.post("/cases", json={"broker_name": "AL Himayah", "company_name": "Palazzo Versace Hotel LLC", "industry": "hotel"})
    return resp.json()["id"]


def _insert_existing_plan(client, case_id, **overrides):
    db = client.db_session_local()
    defaults = dict(
        case_id=case_id,
        role="existing",
        plan_name="Category 1",
        standard_summary={
            "area_of_cover": "Worldwide Exc (USA)",
            "annual_limit": "AED 5,520,000",
            "deductible": "Not specified in source document",
            "pre_existing_chronic_limit": "Covered",
            "maternity_limit": "AED 29,440",
            "dental": "AED 13,800",
            "optical": "AED 1,564",
            "coinsurance": "20% up to a maximum of AED 50",
            "alternative_or_complementary_treatment": "AED 10,000",
            "pharmacy_limit_and_coinsurance": "Covered, exclusive of coinsurance",
        },
    )
    defaults.update(overrides)
    plan = models.BenefitPlan(**defaults)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    db.close()
    return plan


def _insert_quoted_plan(client, case_id, **overrides):
    db = client.db_session_local()
    defaults = dict(
        case_id=case_id,
        role="quoted",
        plan_name="Gold - CAT A",
        category="A",
        network_type="MSH Platinum",
        member_count=54,
        gross_premium=918950.0,
        standard_summary={
            "area_of_cover": "Worldwide Excluding USA",
            "annual_limit": "USD 1,000,000",
            "deductible": "Not specified in source document",
            "pre_existing_chronic_limit": "Covered up to Policy Limit",
            "maternity_limit": "USD 6,800",
            "dental": "USD 3,000",
            "optical": "USD 500",
            "coinsurance": "20% MAX AED 50",
            "alternative_or_complementary_treatment": "USD 1,000",
            "pharmacy_limit_and_coinsurance": "Annual Limit",
        },
    )
    defaults.update(overrides)
    plan = models.BenefitPlan(**defaults)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    db.close()
    return plan


def test_benefits_summary_only_returns_existing_role(client):
    case_id = _create_case(client)
    _insert_existing_plan(client, case_id)
    _insert_quoted_plan(client, case_id)

    resp = client.get(f"/cases/{case_id}/benefits-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["plan_name"] == "Category 1"


def test_premium_by_category_returns_quoted_plans_with_per_member_figure(client):
    case_id = _create_case(client)
    _insert_quoted_plan(client, case_id, category="A", member_count=54, gross_premium=918950.0, plan_name="Gold - CAT A")
    _insert_quoted_plan(client, case_id, category="B", member_count=88, gross_premium=1328222.0, plan_name="Gold - CAT B")

    resp = client.get(f"/cases/{case_id}/premium-by-category")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_members"] == 142
    assert body["total_gross_premium"] == 918950.0 + 1328222.0
    cat_a = next(c for c in body["categories"] if c["category"] == "A")
    assert cat_a["network"] == "MSH Platinum"
    assert round(cat_a["premium_per_member"], 2) == round(918950.0 / 54, 2)


def test_premium_by_category_reports_zero_not_none_for_a_zero_premium_category(client):
    # A category can genuinely have a recorded gross premium of 0 (e.g. a
    # fully subsidized rider) with real members on it - premium_per_member
    # should be 0.0, not silently None as if it couldn't be computed.
    case_id = _create_case(client)
    _insert_quoted_plan(client, case_id, category="A", member_count=54, gross_premium=0.0, plan_name="Gold - CAT A")

    resp = client.get(f"/cases/{case_id}/premium-by-category")
    assert resp.status_code == 200
    cat_a = next(c for c in resp.json()["categories"] if c["category"] == "A")
    assert cat_a["premium_per_member"] == 0.0


def test_premium_by_category_404_without_quote(client):
    case_id = _create_case(client)
    resp = client.get(f"/cases/{case_id}/premium-by-category")
    assert resp.status_code == 404


def test_benefits_comparison_flags_annual_limit_reduction(client):
    case_id = _create_case(client)
    _insert_existing_plan(client, case_id)
    _insert_quoted_plan(client, case_id)

    resp = client.get(f"/cases/{case_id}/benefits-comparison")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["existing_plan_name"] == "Category 1"
    assert body[0]["quoted_plan_name"] == "Gold - CAT A"
    assert body[0]["fields"]["annual_limit"]["direction"] == "reduced"
    assert body[0]["fields"]["maternity_limit"]["direction"] == "reduced"


def test_benefits_comparison_404_without_both_sides(client):
    case_id = _create_case(client)
    _insert_existing_plan(client, case_id)
    resp = client.get(f"/cases/{case_id}/benefits-comparison")
    assert resp.status_code == 404


def test_benefits_comparison_compares_network(client):
    case_id = _create_case(client)
    _insert_existing_plan(client, case_id, network_type="Premium")
    _insert_quoted_plan(client, case_id)

    resp = client.get(f"/cases/{case_id}/benefits-comparison")
    body = resp.json()
    assert body[0]["fields"]["network"]["existing"] == "Premium"
    assert body[0]["fields"]["network"]["quoted"] == "MSH Platinum"


def test_benefits_comparison_reuses_single_existing_plan_across_multiple_quoted_categories(client):
    # A scanned/OCR'd existing plan only ever produces ONE combined entry
    # (see app/ingestion/benefits_ocr.py) regardless of how many categories
    # the source document has - it must still get compared against EVERY
    # quoted category, not just the first, with existing_plan_reused
    # flagging the reuse so the UI can make that visible.
    case_id = _create_case(client)
    _insert_existing_plan(client, case_id, plan_name="OCR extract (verify against source)")
    _insert_quoted_plan(client, case_id, category="A", plan_name="Gold - CAT A")
    _insert_quoted_plan(client, case_id, category="B", plan_name="Gold - CAT B", standard_summary={
        "area_of_cover": "Worldwide Excluding USA",
        "annual_limit": "USD 750,000",
        "deductible": "Not specified in source document",
        "pre_existing_chronic_limit": "Covered up to Policy Limit",
        "maternity_limit": "USD 6,800",
        "dental": "USD 1,000",
        "optical": "Not Covered",
        "coinsurance": "20% MAX AED 50",
        "alternative_or_complementary_treatment": "USD 1,000",
        "pharmacy_limit_and_coinsurance": "Annual Limit",
    })

    resp = client.get(f"/cases/{case_id}/benefits-comparison")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["existing_plan_reused"] is False
    assert body[1]["existing_plan_reused"] is True
    assert body[1]["quoted_plan_name"] == "Gold - CAT B"
    assert body[1]["existing_plan_name"] == "OCR extract (verify against source)"
    # The reused existing plan's own figures must still be compared, not
    # a placeholder - CAT B's optical is "Not Covered" vs the existing
    # plan's AED 1,564, so this must be a real, non-null comparison.
    assert body[1]["fields"]["optical"]["existing"] == "AED 1,564"


def test_reuploading_benefits_does_not_delete_quoted_plans(client):
    import io

    import pandas as pd

    case_id = _create_case(client)
    _insert_quoted_plan(client, case_id)

    benefits_df = pd.DataFrame([{"Plan": "Standard", "Annual Limit": 250000, "Members": 3}])
    buf = io.BytesIO()
    benefits_df.to_excel(buf, index=False)
    buf.seek(0)
    resp = client.post(
        f"/cases/{case_id}/benefits",
        files={"file": ("tob.xlsx", buf.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200

    resp = client.get(f"/cases/{case_id}/premium-by-category")
    assert resp.status_code == 200
    assert resp.json()["total_members"] == 54
