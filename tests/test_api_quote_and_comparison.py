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


def test_list_benefit_plans_returns_existing_and_quoted_plans_with_ids(client):
    case_id = _create_case(client)
    existing = _insert_existing_plan(client, case_id)
    quoted = _insert_quoted_plan(client, case_id)

    resp = client.get(f"/cases/{case_id}/benefit-plans")
    assert resp.status_code == 200
    body = resp.json()
    assert {p["id"] for p in body} == {existing.id, quoted.id}
    assert {p["role"] for p in body} == {"existing", "quoted"}


def test_multiple_existing_plans_auto_match_quoted_plans_by_category_letter_regardless_of_upload_order(client):
    # Two existing plans and two quoted plans, uploaded in opposite order -
    # position-based pairing would wrongly cross-match Category 1 against
    # CAT B and Category 2 against CAT A. The category letter is what
    # actually ties them together.
    case_id = _create_case(client)
    _insert_existing_plan(client, case_id, plan_name="Category 1", category="A")
    _insert_existing_plan(client, case_id, plan_name="Category 2", category="B")
    _insert_quoted_plan(client, case_id, category="B", plan_name="Gold - CAT B", gross_premium=1328222.0)
    _insert_quoted_plan(client, case_id, category="A", plan_name="Gold - CAT A", gross_premium=918950.0)

    resp = client.get(f"/cases/{case_id}/benefits-comparison")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    by_existing_name = {row["existing_plan_name"]: row for row in body}
    assert by_existing_name["Category 1"]["quoted_category"] == "A"
    assert by_existing_name["Category 2"]["quoted_category"] == "B"
    assert all(row["existing_plan_reused"] is False for row in body)


def test_manual_match_overrides_the_automatic_category_letter_match(client):
    # The existing plan's own category doesn't line up with the quote's at
    # all (a real-world case: an incumbent's own tier naming vs
    # HealthCross's quote categories) - PUT .../match pins the pairing by
    # plan id instead of relying on any naming match.
    case_id = _create_case(client)
    existing = _insert_existing_plan(client, case_id, plan_name="Bronze", category=None)
    quoted = _insert_quoted_plan(client, case_id, category="A", plan_name="Gold - CAT A")

    resp = client.put(
        f"/cases/{case_id}/benefits/{existing.id}/match", json={"quoted_plan_id": quoted.id}
    )
    assert resp.status_code == 200
    assert resp.json()["matched_quote_plan_id"] == quoted.id

    # Add a second existing/quoted pair with no naming overlap either -
    # only the manually-matched pair above should resolve.
    existing2 = _insert_existing_plan(client, case_id, plan_name="Silver", category=None)
    quoted2 = _insert_quoted_plan(client, case_id, category="B", plan_name="Gold - CAT B")

    resp = client.get(f"/cases/{case_id}/benefits-comparison")
    assert resp.status_code == 200
    body = resp.json()
    by_existing_name = {row["existing_plan_name"]: row for row in body}
    assert by_existing_name["Bronze"]["quoted_plan_name"] == "Gold - CAT A"


def test_match_endpoint_rejects_a_quoted_plan_from_a_different_case(client):
    case_id = _create_case(client)
    other_case_id = _create_case(client)
    existing = _insert_existing_plan(client, case_id)
    other_quoted = _insert_quoted_plan(client, other_case_id)

    resp = client.put(
        f"/cases/{case_id}/benefits/{existing.id}/match", json={"quoted_plan_id": other_quoted.id}
    )
    assert resp.status_code == 404


def test_reuploading_the_quote_clears_stale_manual_matches(client, monkeypatch):
    from app.api import routes_cases

    case_id = _create_case(client)
    existing = _insert_existing_plan(client, case_id, plan_name="Bronze", category=None)
    quoted = _insert_quoted_plan(client, case_id, category="A", plan_name="Gold - CAT A")

    resp = client.put(f"/cases/{case_id}/benefits/{existing.id}/match", json={"quoted_plan_id": quoted.id})
    assert resp.status_code == 200

    monkeypatch.setattr(
        routes_cases,
        "parse_quote_pdf",
        lambda file, name: [
            {"category": "A", "plan_name": "Reissued Gold - CAT A", "member_count": 10, "gross_premium": 111000.0}
        ],
    )
    resp = client.post(
        f"/cases/{case_id}/quote", files={"file": ("quote.pdf", b"%PDF-1.4 fake", "application/pdf")}
    )
    assert resp.status_code == 200

    db = client.db_session_local()
    from app.models import db_models as models

    refreshed = db.query(models.BenefitPlan).filter_by(id=existing.id).first()
    assert refreshed.matched_quote_plan_id is None
    db.close()

    # The comparison still works (falls back to the automatic category
    # match against the newly-uploaded quote) rather than 404ing or
    # pointing at a deleted row.
    resp = client.get(f"/cases/{case_id}/benefits-comparison")
    assert resp.status_code == 200
    assert resp.json()[0]["quoted_plan_name"] == "Reissued Gold - CAT A - CAT A"


def test_benefits_summary_includes_plan_id_for_editing(client):
    case_id = _create_case(client)
    existing = _insert_existing_plan(client, case_id)

    resp = client.get(f"/cases/{case_id}/benefits-summary")
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == existing.id


def test_manual_summary_edit_overrides_a_field(client):
    # Mainly for OCR-extracted plans (scanned table-of-benefits PDFs),
    # where a field often comes back "Not specified in source document" -
    # an underwriter should be able to correct it by hand rather than
    # re-running OCR.
    case_id = _create_case(client)
    existing = _insert_existing_plan(client, case_id, standard_summary={"deductible": "Not specified in source document"})

    resp = client.put(
        f"/cases/{case_id}/benefits/{existing.id}/summary",
        json={"fields": {"deductible": "Nil", "area_of_cover": "Worldwide Excluding USA"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["standard_summary"]["deductible"] == "Nil"
    assert body["standard_summary"]["area_of_cover"] == "Worldwide Excluding USA"

    resp = client.get(f"/cases/{case_id}/benefits-summary")
    summary = resp.json()[0]["summary"]
    assert summary["deductible"] == "Nil"


def test_manual_summary_edit_blank_value_clears_field_back_to_unresolved(client):
    case_id = _create_case(client)
    existing = _insert_existing_plan(client, case_id, standard_summary={"deductible": "Nil"})

    resp = client.put(
        f"/cases/{case_id}/benefits/{existing.id}/summary",
        json={"fields": {"deductible": "   "}},
    )
    assert resp.status_code == 200
    assert "deductible" not in resp.json()["standard_summary"]

    resp = client.get(f"/cases/{case_id}/benefits-summary")
    assert resp.json()[0]["summary"]["deductible"] == "Not specified in source document"


def test_manual_summary_edit_ignores_unknown_fields(client):
    case_id = _create_case(client)
    existing = _insert_existing_plan(client, case_id, standard_summary={})

    resp = client.put(
        f"/cases/{case_id}/benefits/{existing.id}/summary",
        json={"fields": {"not_a_real_field": "junk"}},
    )
    assert resp.status_code == 200
    assert "not_a_real_field" not in resp.json()["standard_summary"]


def test_manual_summary_edit_404_for_wrong_case(client):
    case_id = _create_case(client)
    other_case_id = _create_case(client)
    existing = _insert_existing_plan(client, other_case_id)

    resp = client.put(
        f"/cases/{case_id}/benefits/{existing.id}/summary",
        json={"fields": {"deductible": "Nil"}},
    )
    assert resp.status_code == 404


def test_manual_summary_edit_renames_the_plan(client):
    case_id = _create_case(client)
    existing = _insert_existing_plan(client, case_id, plan_name="OCR extract (verify against source)")

    resp = client.put(
        f"/cases/{case_id}/benefits/{existing.id}/summary",
        json={"fields": {}, "plan_name": "Category A"},
    )
    assert resp.status_code == 200
    assert resp.json()["plan_name"] == "Category A"


def test_manual_summary_edit_blank_plan_name_leaves_it_unchanged(client):
    case_id = _create_case(client)
    existing = _insert_existing_plan(client, case_id, plan_name="Category A")

    resp = client.put(
        f"/cases/{case_id}/benefits/{existing.id}/summary",
        json={"fields": {}, "plan_name": "   "},
    )
    assert resp.status_code == 200
    assert resp.json()["plan_name"] == "Category A"


def test_add_manual_benefit_plan_creates_a_blank_existing_role_plan(client):
    case_id = _create_case(client)

    resp = client.post(f"/cases/{case_id}/benefits/manual", json={"plan_name": "Category B"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan_name"] == "Category B"
    assert body["standard_summary"] == {}

    resp = client.get(f"/cases/{case_id}/benefits-summary")
    plans = resp.json()
    assert any(p["plan_name"] == "Category B" for p in plans)
    new_plan_summary = next(p["summary"] for p in plans if p["plan_name"] == "Category B")
    assert all(v == "Not specified in source document" for v in new_plan_summary.values())


def test_delete_benefit_plan_removes_it(client):
    case_id = _create_case(client)
    existing = _insert_existing_plan(client, case_id)

    resp = client.delete(f"/cases/{case_id}/benefits/{existing.id}")
    assert resp.status_code == 204

    resp = client.get(f"/cases/{case_id}/benefits-summary")
    assert resp.status_code == 404


def test_delete_benefit_plan_404_for_wrong_case(client):
    case_id = _create_case(client)
    other_case_id = _create_case(client)
    existing = _insert_existing_plan(client, other_case_id)

    resp = client.delete(f"/cases/{case_id}/benefits/{existing.id}")
    assert resp.status_code == 404


def test_delete_benefit_plan_rejects_quoted_role(client):
    case_id = _create_case(client)
    quoted = _insert_quoted_plan(client, case_id)

    resp = client.delete(f"/cases/{case_id}/benefits/{quoted.id}")
    assert resp.status_code == 404


def test_manual_summary_edit_sets_category_and_new_business_pick(client):
    case_id = _create_case(client)
    existing = _insert_existing_plan(client, case_id)

    resp = client.put(
        f"/cases/{case_id}/benefits/{existing.id}/summary",
        json={"fields": {}, "category": "A", "nb_product": "Bronze", "nb_network": "Net A", "nb_tpa": "TPA X"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["category"] == "A"
    assert body["nb_product"] == "Bronze"
    assert body["nb_network"] == "Net A"
    assert body["nb_tpa"] == "TPA X"


def test_manual_summary_edit_blank_nb_fields_clear_them(client):
    case_id = _create_case(client)
    existing = _insert_existing_plan(
        client, case_id, category="A", nb_product="Bronze", nb_network="Net A", nb_tpa="TPA X"
    )

    resp = client.put(
        f"/cases/{case_id}/benefits/{existing.id}/summary",
        json={"fields": {}, "category": "", "nb_product": "", "nb_network": "", "nb_tpa": ""},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["category"] is None
    assert body["nb_product"] is None
    assert body["nb_network"] is None
    assert body["nb_tpa"] is None


def test_manual_summary_edit_omitted_nb_fields_leave_them_unchanged(client):
    case_id = _create_case(client)
    existing = _insert_existing_plan(
        client, case_id, category="A", nb_product="Bronze", nb_network="Net A", nb_tpa="TPA X"
    )

    resp = client.put(f"/cases/{case_id}/benefits/{existing.id}/summary", json={"fields": {}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["category"] == "A"
    assert body["nb_product"] == "Bronze"
    assert body["nb_network"] == "Net A"
    assert body["nb_tpa"] == "TPA X"
