"""A document nobody can open is not a document -
app/reports/underwriting_report.py.

The tests that matter here are about the two ways this has actually
broken: a case that is not fully set up yet, and a figure the payload
does not have. Both used to produce either an exception or a blank
section, and a blank section is worse - it reads as "nothing to report"
rather than "not asked yet".
"""
from datetime import date

import pytest

from app.reports.underwriting_report import (
    aed,
    area_chart,
    donut,
    gauge,
    month_amount,
    month_label,
    pct,
    render_underwriting_report,
    signed_pct,
)


def _payload(**overrides) -> dict:
    base = {
        "case": {"id": 32, "company_name": "Freshly Frozen Foods Factory (L.L.C)",
                 "broker_name": "Marsh", "industry": "manufacturing", "member_count": 108},
        "experience": {
            "has_experience": True, "loading_pct": 0.265, "quoted_price": 900_000.0,
            "break_even_premium": 1_556_339.0, "suggested_premium": 1_830_987.0,
            "target_loss_ratio": 0.85, "member_count": 108, "expected_claims": 1_143_909.0,
            "implied_loss_ratio_at_quote": 1.729,
            "experience": {
                "own_experience": {"paid": 742_182.0, "reported_not_paid": 11_096.0,
                                   "incurred_not_reported": 63_099.0, "incurred_claims": 816_377.0,
                                   "report_days": 272, "member_years": 81.6,
                                   "claims_per_member_year": 10_005.0},
                "blend": {"blended_rate": 9_629.0, "credibility": 0.903, "own_rate": 10_005.0,
                          "book_rate": 6_118.0, "basis": "mostly own experience"},
                "trend_pct": 0.10, "census_size": 108, "expected_claims": 1_143_909.0,
                "caveats": ["Incurred on the incumbent's plan."],
            },
        },
        "scorecard": {
            "rows": [
                {"key": "claims_experience", "label": "Claims experience", "weight": 0.25,
                 "score": 15.0, "band": "high", "measure": "1.64x the book's prediction"},
                {"key": "group_size", "label": "Group size", "weight": 0.20,
                 "score": 85.0, "band": "low", "measure": "108 lives"},
                {"key": "benefit_design", "label": "Benefit design", "weight": 0.10,
                 "score": 40.0, "band": "medium", "measure": "no outpatient deductible; pharmacy uncapped"},
                {"key": "chronic_pre_existing", "label": "Chronic / pre-existing", "weight": 0.10,
                 "score": 20.0, "band": "high", "measure": "22% of claims are chronic; covered from day one"},
            ],
            "overall_score": 44.0, "overall_band": "medium",
            "weight_scored": 0.65, "weight_unscored": 0.35,
        },
        "pricing_bridge": {"card_price": 988_824.0, "technical_price": 1_830_987.0,
                           "commercial_price": 900_000.0, "break_even": 1_556_339.0,
                           "card_to_technical_pct": 0.85, "technical_to_commercial_pct": -0.51},
        "sensitivity": [
            {"stress_pct": 0.0, "expected_claims": 1_143_909.0,
             "loss_ratios": {"quoted": 1.729, "technical": 0.85, "break_even": 1.0}},
            {"stress_pct": 0.20, "expected_claims": 1_372_691.0,
             "loss_ratios": {"quoted": 2.075, "technical": 1.02, "break_even": 1.2}},
        ],
        "stress_absorbed": {"quoted": -0.42, "technical": 0.1765},
        "chronic_share_of_claims": 0.215,
        "claims_report": {
            "report_period_start": "2025-10-12", "report_period_end": "2026-07-10",
            # The shape the claims-report parsers really emit: "paid", and
            # a three-letter month. A fixture using "amount" hid a crash
            # that only ever happened on real reports.
            "total_paid": 742_182.0, "monthly_paid": [
                {"year": 2025, "month": "Nov", "paid": 58_000, "partial": True},
                {"year": 2025, "month": "Dec", "paid": 71_000, "partial": False},
                {"year": 2026, "month": "Jan", "paid": 66_000, "partial": False},
                {"year": 2026, "month": "Feb", "paid": 88_000, "partial": False},
            ],
            "diagnosis_breakdown": [{"label": "Diabetes mellitus", "value": 121_000.0}],
            "claims_by_member_type_value": [],
        },
        "target_premiums": {"85%": 1_830_987.0},
        "census": {
            "total_members": 108, "avg_age": 35.9,
            "age_band_counts": {"0-17": 19, "18-40": 52, "41-60": 37, "61-99": 0},
            "gender_counts": {"M": 71, "F": 37, "Other": 0},
            "relation_counts": {"Employee": 67, "Child": 26, "Spouse": 15},
            "nationality_zone_counts": {"zone_1_isc": 68, "zone_2_middle_east": 40},
            "employee_count": 67, "male_employees": 59,
            "maternity_risk_count": 17, "maternity_risk_pct": 0.157,
            "married_female_count": 21, "married_female_pct": 0.194,
            "female_spouse_count": 15, "infant_count": 2,
        },
        "by_category": None,
        "benefits": {"categories": [{
            "category": "A", "existing_plan_name": "Cigna COMPREHENSIVE",
            "product": "Platinum", "network": "MSH Platinum",
            "rows": [
                {"field": "annual_limit", "label": "Annual Limit", "existing": "US$ 7,500,000",
                 "proposed": "USD 500,000", "direction": "reduced"},
                {"field": "optical_limit", "label": "Optical", "existing": "US 200",
                 "proposed": "USD 300 Co-pay: 20%", "direction": "improved"},
            ],
        }]},
        "decision": {
            "verdict": "decline", "headline": "Below break-even - do not issue at this price",
            "recommended_minimum": 1_830_987.0, "break_even": 1_556_339.0, "quoted": 900_000.0,
            "room_vs_minimum_pct": -0.5085, "discount_authority_floor": 1_739_437.65,
            "referral_required": True, "risk_band": "medium", "risk_score": 44.0,
            "expected_claims": 1_143_909.0, "loading_pct": 0.265,
        },
    }
    base.update(overrides)
    return base


def _render(**overrides) -> str:
    return render_underwriting_report(_payload(**overrides), today=date(2026, 8, 24))


# --- the numbers reach the page -----------------------------------------

def test_the_document_carries_the_figures_the_decision_rests_on():
    html = _render()
    for expected in ("Freshly Frozen Foods Factory", "DECLINE", "900,000", "1,143,909",
                     "1,830,987", "172.9%"):
        assert expected in html, expected


def test_all_four_pages_are_present():
    html = _render()
    for page in range(1, 5):
        assert f"Page {page} of 4" in html


def test_the_verdict_word_is_the_same_on_the_dashboard_and_the_recommendation():
    # These are two separate blocks reading the same field. When they
    # disagreed - a hero saying REFER above a table saying DECLINE - it
    # was the document, not the case, that had changed its mind.
    html = _render()
    assert html.count("DECLINE") >= 2
    assert "REFER" not in html


# --- a case that is not finished yet ------------------------------------

def test_a_case_with_nothing_on_it_still_renders():
    bare = {
        "case": {"id": 1, "company_name": "New Enquiry", "broker_name": None,
                 "industry": None, "member_count": 0},
        "experience": {"has_experience": False, "experience": None, "expected_claims": None,
                       "loading_pct": 0.265, "quoted_price": None, "break_even_premium": None,
                       "suggested_premium": None, "target_loss_ratio": 0.85,
                       "implied_loss_ratio_at_quote": None},
        "scorecard": {"rows": [], "overall_score": None, "overall_band": None,
                      "weight_scored": 0.0, "weight_unscored": 1.0},
        "pricing_bridge": {"card_price": None, "technical_price": None, "commercial_price": None,
                           "break_even": None, "card_to_technical_pct": None,
                           "technical_to_commercial_pct": None},
        "sensitivity": [], "stress_absorbed": {}, "chronic_share_of_claims": None,
        "claims_report": None, "target_premiums": {}, "census": None,
        "by_category": None, "benefits": {"categories": []},
        "decision": {"verdict": "incomplete", "headline": "Not enough on file to price this",
                     "recommended_minimum": None, "break_even": None, "quoted": None,
                     "room_vs_minimum_pct": None, "discount_authority_floor": None,
                     "referral_required": False, "risk_band": None, "risk_score": None,
                     "expected_claims": None, "loading_pct": 0.265},
    }
    html = render_underwriting_report(bare, today=date(2026, 8, 24))
    assert "New Enquiry" in html
    assert "Page 4 of 4" in html


def test_a_missing_section_says_what_is_missing_rather_than_going_blank():
    # A blank section reads as "nothing to report". It is not the same
    # statement as "no claims report has been uploaded", and an
    # underwriter acts differently on each.
    html = _render(claims_report=None, experience={**_payload()["experience"], "experience": None})
    assert "No incumbent claims report is on file" in html


def test_no_incumbent_plan_says_so_instead_of_showing_an_empty_table():
    html = _render(benefits={"categories": []})
    assert "No incumbent table of benefits" in html


def test_no_census_does_not_take_the_rest_of_the_document_with_it():
    html = _render(census=None)
    assert "No census has been uploaded" in html
    assert "Page 3 of 4" in html


# --- what must never be invented ----------------------------------------

def test_an_unknown_figure_prints_a_dash_rather_than_a_zero():
    # Zero is a number a reader acts on. "We do not know" is not.
    assert aed(None) == "&mdash;"
    assert pct(None) == "&mdash;"
    assert signed_pct(None) == "&mdash;"
    assert aed(0) == "0"


def test_an_unscored_card_draws_an_empty_gauge_rather_than_a_zero_score():
    svg = gauge(None, None, "NOT SCORED")
    assert "&mdash;" in svg
    assert ">0<" not in svg


# --- the charts ----------------------------------------------------------

def test_the_donut_segments_add_up_to_the_whole_circle():
    svg = donut([{"value": 67, "colour": "#1c2947"}, {"value": 26, "colour": "#4ab0e3"},
                 {"value": 15, "colour": "#a4d7f1"}], "108", "LIVES")
    assert svg.count("<circle") == 3
    assert "108" in svg


def test_a_single_month_is_not_drawn_as_a_trend():
    # Two points make a line; one makes a claim about a direction that
    # the data cannot support.
    assert area_chart([{"month": "Jan", "paid": 5}]) == ""
    assert area_chart([]) == ""
    assert "<polyline" in area_chart([{"month": "Jan", "paid": 5},
                                     {"month": "Feb", "paid": 9}])


def test_the_chart_reads_whichever_name_the_month_arrived_under():
    # The claims-report parsers say "paid" and "Jan"; every other monthly
    # series in the portal says "amount" and "2026-01". Reading only one
    # of them raised on real reports and drew fine on fixtures.
    assert month_amount({"paid": 58_000}) == 58_000
    assert month_amount({"amount": 58_000}) == 58_000
    assert month_amount({}) == 0.0
    assert month_label({"month": "Jan"}) == "Jan"
    assert month_label({"month": "2026-01"}) == "Jan"
    assert month_label({}) == ""


def test_a_real_parser_month_row_reaches_the_chart():
    parser_rows = [{"year": 2025, "month": "Nov", "paid": 58_000, "partial": True},
                   {"year": 2025, "month": "Dec", "paid": 71_000, "partial": False}]
    svg = area_chart(parser_rows)
    assert "<polyline" in svg
    assert ">Nov<" in svg and ">Dec<" in svg


def test_a_bar_never_runs_past_the_end_of_its_track():
    html = _render()
    widths = [float(w.split("%")[0]) for w in html.split("width:")[1:] if "%" in w.split(";")[0].split('"')[0]]
    assert len(widths) > 20, "the bars did not render, so this proves nothing"
    assert max(widths) <= 100.0


# --- the page is well formed --------------------------------------------

def test_the_company_name_cannot_break_out_of_the_markup():
    html = _render(case={"id": 1, "company_name": '<script>alert("x")</script>',
                         "broker_name": None, "industry": None, "member_count": 3})
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_entities_in_our_own_headings_render_as_characters_not_as_text():
    # An earlier version escaped its own markup, printing "&middot;" on
    # the page where a separator belonged.
    html = _render()
    assert "&amp;middot;" not in html
    assert "&amp;ndash;" not in html


@pytest.mark.parametrize("verdict,word", [
    ("decline", "DECLINE"), ("refer", "REFER"), ("proceed", "PROCEED"),
    ("incomplete", "INCOMPLETE"),
])
def test_every_verdict_has_a_word_for_it(verdict, word):
    html = _render(decision={**_payload()["decision"], "verdict": verdict})
    assert word in html


# --- the shapes real data actually arrives in ----------------------------

def test_a_census_whose_relation_is_not_the_word_employee_still_renders():
    # The membership exports say "Principal", not "Employee". Counting
    # zero employees used to take the whole page down: the ratio
    # `children / employees` was evaluated before the `if employees`
    # meant to guard it, so the report 500'd on real data while passing
    # on every fixture.
    html = _render(census={**_payload()["census"], "employee_count": 0, "male_employees": 0,
                           "relation_counts": {"Principal": 67, "Child": 26, "Spouse": 15}})
    assert "Children per employee" in html
    assert "Principal" in html


@pytest.mark.parametrize("census", [
    {"total_members": 5},          # a summary carrying only the headline
    {},                            # nothing at all
    {"total_members": 0},          # an empty census
    {"total_members": 8, "relation_counts": {}, "age_band_counts": {}, "gender_counts": {}},
    {"total_members": 8, "age_band_counts": {"Unmapped": 5, "65+": 3}},
    {"total_members": 8, "avg_age": None, "maternity_risk_pct": None},
])
def test_a_thin_census_prints_dashes_rather_than_raising(census):
    # Every one of these is a shape the portal can genuinely produce.
    # A report that raises on a half-filled case is useless exactly when
    # an underwriter wants it - early.
    html = _render(census=census)
    assert "Page 4 of 4" in html


@pytest.mark.parametrize("claims_report", [
    {},
    {"monthly_paid": [{"month": "Jan"}, {"month": "Feb"}]},          # no figure on the row
    {"monthly_paid": [{"month": "Jan", "paid": 0}, {"month": "Feb", "paid": 0}]},
    {"diagnosis_breakdown": [{"label": "X"}]},                        # no value
])
def test_a_thin_claims_report_does_not_take_the_page_down(claims_report):
    assert "Page 4 of 4" in _render(claims_report=claims_report)


@pytest.mark.parametrize("decision_patch", [
    {"quoted": 0}, {"loading_pct": 1.0}, {"break_even": 0}, {"recommended_minimum": 0},
])
def test_degenerate_prices_do_not_divide_by_zero(decision_patch):
    assert "Page 4 of 4" in _render(decision={**_payload()["decision"], **decision_patch})


# --- the uploaded quote document ----------------------------------------

def test_an_uploaded_quote_puts_the_offer_as_issued_on_the_document():
    # The per-category breakdown comes off the uploaded quotation, so it
    # is what the broker received rather than what the card would have
    # charged. A blended total hides which category carries the money.
    html = _render(by_category={
        "categories": [
            {"category": "A", "plan_name": "Platinum - CAT A", "network": "MSH Platinum",
             "member_count": 58, "gross_premium": 430_000.0, "premium_per_member": 7413.79},
            {"category": "C", "plan_name": "Enhanced - CAT C", "network": "MSH Enhanced",
             "member_count": 50, "gross_premium": 234_500.0, "premium_per_member": 4690.0},
        ],
        "total_members": 108, "total_gross_premium": 664_500.0,
        "blended_premium_per_member": 6152.78,
    })
    assert "The offer as issued" in html
    assert "MSH Enhanced" in html
    assert "664,500" in html


def test_without_an_uploaded_quote_the_offer_section_is_left_out_entirely():
    # Not an empty table with a heading over it - there is no offer to
    # show, and a heading implies there should be.
    assert "The offer as issued" not in _render(by_category=None)
