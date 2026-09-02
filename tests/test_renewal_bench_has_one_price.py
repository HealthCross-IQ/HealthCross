"""The Renewal Bench shows one renewal premium, however many panels it
takes to explain it.

The tab has thirteen panels and several of them quote a price. Each was
free to compute its own, and they did:

    step bar / rating / scorecard / hero / scenarios   1,384,300
    repricing panel                                    1,456,375
    premium build-up                                     441,587

All on one screen, for one account, on the same day. Three different
routes to a renewal premium and nothing on the page to say which one the
house would actually quote.

They diverged for three separate reasons, and each is worth a sentence
because each will be tempting again:

  the repricing panel annualised the same book claims a SECOND way -
  complete months x 12 where the rating scales by the exposure actually
  run, 1,048,000 against 905,373;

  the build-up never annualised AT ALL, pricing a twelve-month policy on
  whatever fraction of a year had elapsed - 130 days on Nomada;

  and both trended by MULTIPLYING the claims where the house ladder adds
  inflation to the LOSS RATIO in points, which agree only at a 100% loss
  ratio.

This test exists so the next panel that wants a price has to go through
renewal_from_loss_ratio to get one.
"""
from datetime import date

import pytest

from app.models import db_models as models

HOUSE_FEES = {"tpa_fee_pct": 0.10, "commission_pct": 0.05,
              "hc_fee_pct": 0.065, "qic_fee_pct": 0.0}

MEMBERS_HEADER = [
    "CONTRACT", "MASTERCONTRACT", "POLICYNUMBER", "MSH_POLICYNUMBER", "BENEFICIARYID",
    "DOB", "GENDER", "MARITALSTATUS", "NATIONALITY", "DEPENDENCY",
    "PERSONRESIDENCEEMIRATE", "CATEGORY", "NETWORKTYPE",
    "Eff Date", "Exp Date", "EndoDate (Member Start Date)", "EndoDate (Member End Date)",
    "GrossPremium", "ActualGrossPremium", "NETPREMIUM", "ACTUALNETPREMIUM", "TPA FEE",
]
CLAIMS_HEADER = [
    "PATIENT_ID", "CLAIM_ID", "Claim Status", "GROUP_NAME", "CLIENT_NAME", "MSH_POLICY_NUMBER",
    "POLICY_START_DATE", "POLICY_END_DATE", "Member Start Date", "Member End Date",
    "DATE_OF_TREATMENT", "RELATION", "IP_OP_MATERNITY", "MEDICAL_CATEGORY", "PROVIDER_NAME",
    "DIAGNOSIS_CODE", "DIAGNOSIS_DESCRIPTION", "Claimed Amount AED", "Final Amount in AED",
]

ACCOUNT = "Bench Consistency LLC"
TERM_START = "2026-04-24"
TERM_END = "2027-04-24"
AS_OF = "2026-08-31"           # 130 of 365 days: a genuinely part year


def _write(tmp_path, name, header, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    path = tmp_path / name
    wb.save(path)
    return path


def _member(bid, premium):
    return [f"{ACCOUNT} Sub", ACCOUNT, "P1", f"QC1-{bid}", bid, "1985-06-01", "M", "Married",
            "India", "Principal", "Dubai", "QIC/HC/BR/BEN/DXB/A", "PLATINUM",
            TERM_START, TERM_END, TERM_START, TERM_END, premium, premium, None, None, 0]


def _claim(bid, treated, amount, status="Paid Claims"):
    return [bid, f"CLM-{bid}-{treated}", status, f"{ACCOUNT} Sub", ACCOUNT, f"QC1-{bid}",
            TERM_START, TERM_END, TERM_START, TERM_END, treated, "Main Insured", "IP",
            "HOSPITALISATION", "Hospital", "A099", "Acute condition", amount, amount]


@pytest.fixture()
def bench(client, tmp_path):
    """One account on the book, part way through its term, with a renewal
    case open and its fee split entered - the state in which every panel
    on the bench has something to say."""
    members = _write(tmp_path, "members.xlsx", MEMBERS_HEADER, [
        _member("BEN001", 500_000), _member("BEN002", 400_000), _member("BEN003", 370_000),
    ])
    claims = _write(tmp_path, "claims.xlsx", CLAIMS_HEADER, [
        _claim("BEN001", "2026-05-15", 95_000.0),
        _claim("BEN002", "2026-06-18", 88_000.0),
        _claim("BEN003", "2026-07-21", 79_000.0),
    ])
    for path, endpoint in ((members, "members"), (claims, "claims")):
        with open(path, "rb") as f:
            resp = client.post(f"/portfolio-analysis/{endpoint}/upload",
                               files={"file": (path.name, f, "application/octet-stream")})
        assert resp.status_code == 200, resp.text
    client.post("/portfolio-analysis/data-as-of", json={"data_as_of_date": AS_OF})

    opened = client.post("/portfolio-analysis/renewal-intake", json={"master_client": ACCOUNT})
    assert opened.status_code == 200, opened.text
    case_id = opened.json()["case"]["id"]
    client.patch(f"/cases/{case_id}", json={**HOUSE_FEES, "policy_start_date": TERM_START})

    db = client.db_session_local()
    for i, (month, amount) in enumerate([((2026, 5), 95_000.0), ((2026, 6), 88_000.0),
                                         ((2026, 7), 79_000.0)]):
        db.add(models.ClaimsLedgerEntry(
            case_id=case_id, patient_id=f"BEN00{i + 1}", claim_id=f"L{i}",
            claim_status="Paid Claims",
            policy_start_date=date(2026, 4, 24), policy_end_date=date(2027, 4, 24),
            date_of_treatment=date(month[0], month[1], 15), ip_op_maternity="IP",
            diagnosis_code="A099", diagnosis_description="Acute condition",
            final_amount=amount))
    db.commit()
    db.close()
    return case_id


def every_premium_on_the_bench(client, case_id):
    """Every renewal premium a panel on the tab puts on screen, named by
    the panel a reader would see it in."""
    rating = client.get(f"/cases/{case_id}/renewal-rating").json()
    bench = client.get(f"/cases/{case_id}/renewal-bench-summary").json()
    scen = client.get(f"/cases/{case_id}/renewal-scenarios").json()
    reprice = client.get(f"/portfolio-analysis/renewal-repricing/{ACCOUNT}").json()
    buildup = client.get(f"/cases/{case_id}/renewal-premium").json()

    return {
        "renewal rating card": rating["required_premium"],
        "scorecard Method A": rating["required_premium"],
        "scorecard Method B": rating["method_b"]["required_premium"],
        "recommended premium hero": bench["drivers"]["recommended_premium"],
        "scenarios, as reported": next(
            s for s in scen["scenarios"] if s["key"] == "as_reported")["required_premium"],
        "repricing, everyone in": reprice["as_priced"]["required_premium"],
        "premium build-up": buildup["gross_premium"],
    }


class TestOnePremium:
    def test_every_panel_quotes_the_same_renewal_premium(self, client, bench):
        premiums = every_premium_on_the_bench(client, bench)
        distinct = sorted(set(round(v, 2) for v in premiums.values() if v is not None))
        assert len(distinct) == 1, (
            "the Renewal Bench is showing more than one renewal premium:\n"
            + "\n".join(f"  {k:28} {v:,.2f}" for k, v in premiums.items()))

    def test_none_of_them_is_silently_missing(self, client, bench):
        premiums = every_premium_on_the_bench(client, bench)
        missing = [k for k, v in premiums.items() if v is None]
        assert not missing, f"panels quoting nothing at all: {missing}"

    def test_the_step_bar_agrees_with_the_card_it_summarises(self, client, bench):
        body = client.get(f"/cases/{bench}/renewal-bench-summary").json()
        rating = client.get(f"/cases/{bench}/renewal-rating").json()
        pricing = next(s for s in body["workflow"] if s["key"] == "pricing")
        assert f"{rating['required_premium']:,.0f}" in pricing["detail"]


class TestOneLossRatio:
    def test_every_panel_reports_the_same_loss_ratio(self, client, bench):
        rating = client.get(f"/cases/{bench}/renewal-rating").json()
        summary = client.get(f"/cases/{bench}/renewal-bench-summary").json()
        scen = client.get(f"/cases/{bench}/renewal-scenarios").json()
        reprice = client.get(f"/portfolio-analysis/renewal-repricing/{ACCOUNT}").json()
        overview = client.get(f"/portfolio-analysis/account-overview/{ACCOUNT}").json()

        ratios = {
            "renewal rating": rating["actual_loss_ratio"],
            "KPI strip": summary["kpis"]["actual_loss_ratio"],
            "scenarios": scen["loss_ratio"],
            "repricing": reprice["as_priced"]["loss_ratio"],
            "account dashboard": overview["kpis"]["gross_loss_ratio"],
        }
        distinct = sorted(set(round(v, 4) for v in ratios.values() if v is not None))
        assert len(distinct) == 1, (
            "the Renewal Bench is showing more than one loss ratio:\n"
            + "\n".join(f"  {k:22} {v}" for k, v in ratios.items()))

    def test_method_b_is_allowed_to_differ_and_says_why(self, client, bench):
        # The ONE difference that is by design: Method A and Method B
        # reserve differently on the same paid-and-outstanding base, which
        # is the entire point of showing both.
        rating = client.get(f"/cases/{bench}/renewal-rating").json()
        assert rating["method_b"]["actual_loss_ratio"] is not None
        summary = client.get(f"/cases/{bench}/renewal-bench-summary").json()
        assert summary["kpis"]["loss_ratio_basis"]


class TestOneSetOfAssumptions:
    def test_every_panel_carries_the_same_inflation(self, client, bench):
        rating = client.get(f"/cases/{bench}/renewal-rating").json()
        scen = client.get(f"/cases/{bench}/renewal-scenarios").json()
        reprice = client.get(f"/portfolio-analysis/renewal-repricing/{ACCOUNT}").json()
        buildup = client.get(f"/cases/{bench}/renewal-premium").json()
        inflations = {
            "rating": rating["assumptions_used"]["inflation_pct"],
            "scenarios": scen["inflation_pts"],
            "repricing": reprice["trend_pct"],
            "build-up": buildup["trend_pct"],
        }
        # The build-up defaulted to 10% while everything beside it used 7.5.
        assert len(set(inflations.values())) == 1, inflations

    def test_every_panel_carries_the_same_loading(self, client, bench):
        rating = client.get(f"/cases/{bench}/renewal-rating").json()
        scen = client.get(f"/cases/{bench}/renewal-scenarios").json()
        reprice = client.get(f"/portfolio-analysis/renewal-repricing/{ACCOUNT}").json()
        buildup = client.get(f"/cases/{bench}/renewal-premium").json()
        loadings = {
            "rating": rating["assumptions_used"]["loading_pct"],
            "scenarios": scen["loading_pct"],
            "repricing": reprice["loading_pct"],
            "build-up": buildup["loading_pct"],
        }
        assert len(set(round(v, 6) for v in loadings.values())) == 1, loadings
        # And it is the account's own split, not the house average.
        assert round(rating["assumptions_used"]["loading_pct"], 4) == 0.215

    def test_every_panel_measures_the_year_the_same_way(self, client, bench):
        rating = client.get(f"/cases/{bench}/renewal-rating").json()
        buildup = client.get(f"/cases/{bench}/renewal-premium").json()
        # The build-up defaulted its as-of to TODAY, reading the year as
        # two days longer than the loss ratio row did, which moved the
        # IBNR tail and everything resting on it.
        assert buildup["elapsed_days"] == rating["ibnr_detail"]["elapsed_days"]
        assert buildup["ibnr"] == pytest.approx(rating["ibnr_detail"]["ibnr"], abs=0.01)


class TestThePartYearItself:
    def test_the_bench_is_actually_testing_a_part_year_account(self, client, bench):
        # If the fixture ever became a full year the annualisation bug
        # would stop being visible and this file would pass while saying
        # nothing.
        rating = client.get(f"/cases/{bench}/renewal-rating").json()
        assert rating["ibnr_detail"]["elapsed_days"] < 365

    def test_the_build_up_annualises_and_shows_the_step(self, client, bench):
        buildup = client.get(f"/cases/{bench}/renewal-premium").json()
        assert buildup["annualisation_factor"] > 1
        assert "Annualise" in [s["label"] for s in buildup["build_up"]]
