"""The account dashboard payload.

The endpoint computes almost nothing of its own - it assembles the loss
ratio row, the encounter split, the claimant ranking and the readings
that already exist. So what these tests are really pinning is that the
assembly does not quietly introduce a second version of any of them, and
that the one figure the whole screen turns on is the right one:

    A loss ratio against ANNUAL premium on a part-year term reads as a
    comfortable account. K A F, 130 days into a 365-day term, is 88.5%
    against annual premium and 248.6% against earned. The endpoint
    divides by earned and says so.
"""
import openpyxl
import pytest

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

TERM_START = "2026-04-24"
TERM_END = "2027-04-24"
AS_OF = "2026-08-31"  # 130 elapsed days, inclusive of the effective date


def _write_xlsx(tmp_path, name, header, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    path = tmp_path / name
    wb.save(path)
    return path


def _member(master, beneficiary_id, premium, dob="1985-06-01", gender="M"):
    return [
        f"{master} Sub", master, "P1", f"QC1-{beneficiary_id}", beneficiary_id,
        dob, gender, "Married", "India", "Principal",
        "Dubai", f"QIC/HC/BR/{master[:3].upper()}/DXB/A", "PLATINUM",
        TERM_START, TERM_END, TERM_START, TERM_END,
        premium, premium, None, None, 0,
    ]


def _claim(beneficiary_id, master, treated, amount, status="Paid Claims",
           encounter="IP", category="HOSPITALISATION", diagnosis="Acute condition"):
    return [
        beneficiary_id, f"CLM-{beneficiary_id}-{treated}", status, f"{master} Sub", master,
        f"QC1-{beneficiary_id}", TERM_START, TERM_END, TERM_START, TERM_END,
        treated, "Main Insured", encounter, category, "Some Hospital",
        "A099", diagnosis, amount, amount,
    ]


@pytest.fixture()
def book(client, tmp_path):
    """Two accounts on the same policy period: one running hot with a
    dominant claimant and a large reserve, one running normally. The
    second exists so the book comparison has a population - a percentile
    against a book of one is not a reading."""
    members = _write_xlsx(
        tmp_path, "members.xlsx", MEMBERS_HEADER,
        [
            _member("KAF Holdings", "KAF001", 500000),
            _member("KAF Holdings", "KAF002", 300000),
            _member("KAF Holdings", "KAF003", 200000, gender="F"),
            _member("Steady Holdings", "STD001", 600000),
            _member("Steady Holdings", "STD002", 400000),
        ],
    )
    claims = _write_xlsx(
        tmp_path, "claims.xlsx", CLAIMS_HEADER,
        [
            _claim("KAF001", "KAF Holdings", "2026-05-10", 300000.0, "Paid Claims"),
            _claim("KAF001", "KAF Holdings", "2026-07-15", 300000.0, "Outstanding Claims"),
            _claim("KAF002", "KAF Holdings", "2026-06-01", 50000.0, "Paid Claims",
                   encounter="OP", category="PHARMACY"),
            _claim("STD001", "Steady Holdings", "2026-05-02", 100000.0, "Paid Claims"),
            _claim("STD002", "Steady Holdings", "2026-06-02", 50000.0, "Paid Claims"),
        ],
    )
    for path, endpoint in ((members, "members"), (claims, "claims")):
        with open(path, "rb") as f:
            resp = client.post(
                f"/portfolio-analysis/{endpoint}/upload",
                files={"file": (path.name, f, "application/octet-stream")},
            )
        assert resp.status_code == 200, resp.text
    return client


def overview(client, master="KAF Holdings"):
    resp = client.get(f"/portfolio-analysis/account-overview/{master}",
                      params={"as_of": AS_OF})
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestTheStripReconciles:
    def test_the_kpis_are_one_loss_ratio_row(self, book):
        kpis = overview(book)["kpis"]
        assert kpis["member_count"] == 3
        assert kpis["days"] == 130
        assert kpis["paid"] == pytest.approx(350000.0)
        assert kpis["outstanding"] == pytest.approx(300000.0)
        assert kpis["gross_premium"] == pytest.approx(1000000.0)

    def test_incurred_is_paid_plus_outstanding_plus_ibnr(self, book):
        kpis = overview(book)["kpis"]
        assert kpis["incurred_claims"] == pytest.approx(
            kpis["paid"] + kpis["outstanding"] + kpis["ibnr"]
        )

    def test_earned_premium_is_the_part_year_share(self, book):
        kpis = overview(book)["kpis"]
        assert kpis["earned_premium"] == pytest.approx(1000000.0 * 130 / 365, abs=1.0)

    def test_the_loss_ratio_divides_by_earned_not_annual(self, book):
        data = overview(book)
        kpis = data["kpis"]
        assert data["loss_ratio_basis"] == "earned_premium"
        assert kpis["gross_loss_ratio"] == pytest.approx(
            kpis["incurred_claims"] / kpis["earned_premium"], abs=0.001
        )
        # The reading the whole screen turns on: against ANNUAL premium
        # this account looks like a 73% account.
        against_annual = kpis["incurred_claims"] / kpis["gross_premium"]
        assert against_annual < 1.0 < kpis["gross_loss_ratio"]

    def test_the_policy_block_matches_the_row(self, book):
        data = overview(book)
        assert data["policy"]["start_date"] == TERM_START
        assert data["policy"]["days_elapsed"] == 130
        assert data["policy"]["expired"] is False
        assert data["policy"]["member_count"] == 3


class TestAlerts:
    def test_this_account_raises_all_four_readings(self, book):
        codes = [a["code"] for a in overview(book)["alerts"]]
        assert codes == [
            "loss_ratio_critical",
            "claim_concentration",
            "outstanding_exposure",
            "experience_immature",
        ]

    def test_the_account_running_normally_raises_no_pricing_reading(self, book):
        # It still raises concentration (two members, so one of them is
        # always most of the cost) and immaturity (130 days) - both are
        # true of it. What it must NOT raise is the two readings that
        # would change how it is priced.
        codes = [a["code"] for a in overview(book, "Steady Holdings")["alerts"]]
        assert "loss_ratio_critical" not in codes
        assert "loss_ratio_above_target" not in codes
        assert "outstanding_exposure" not in codes

    def test_the_counts_match_the_list(self, book):
        data = overview(book)
        counts = data["alert_counts"]
        assert sum(counts.values()) == len(data["alerts"])

    def test_the_outstanding_alert_carries_the_book_median(self, book):
        data = overview(book)
        alert = next(a for a in data["alerts"] if a["code"] == "outstanding_exposure")
        assert "book median" in alert["message"]

    def test_every_alert_says_what_to_do(self, book):
        for alert in overview(book)["alerts"]:
            assert alert["action"]
            assert alert["rule"]


class TestConcentration:
    def test_the_top_claimant_share_is_against_incurred(self, book):
        data = overview(book)
        top = data["top_claimants"][0]
        assert top["beneficiary_id"] == "KAF001"
        assert top["incurred"] == pytest.approx(600000.0)
        assert data["top_claimant_share"] == pytest.approx(
            600000.0 / data["kpis"]["incurred_claims"], abs=0.001
        )

    def test_claimants_are_ranked_worst_first(self, book):
        incurred = [c["incurred"] for c in overview(book)["top_claimants"]]
        assert incurred == sorted(incurred, reverse=True)

    def test_a_member_with_no_claims_is_not_listed(self, book):
        ids = [c["beneficiary_id"] for c in overview(book)["top_claimants"]]
        assert "KAF003" not in ids


class TestShapeOfTheYear:
    def test_monthly_split_covers_every_month_in_range(self, book):
        months = [m["month"] for m in overview(book)["claims_by_month"]]
        assert months == ["2026-05", "2026-06", "2026-07"]

    def test_the_reserve_shows_as_outstanding_not_paid(self, book):
        by_month = {m["month"]: m for m in overview(book)["claims_by_month"]}
        assert by_month["2026-05"]["paid"] == pytest.approx(300000.0)
        assert by_month["2026-05"]["outstanding"] == 0.0
        assert by_month["2026-07"]["outstanding"] == pytest.approx(300000.0)
        assert by_month["2026-07"]["paid"] == 0.0

    def test_the_monthly_total_reconciles_to_paid_plus_outstanding(self, book):
        data = overview(book)
        charted = sum(m["total"] for m in data["claims_by_month"])
        assert charted == pytest.approx(data["kpis"]["paid"] + data["kpis"]["outstanding"])

    def test_the_encounter_split_reconciles_to_the_same_total(self, book):
        data = overview(book)
        split = sum(row["total_value"] for row in data["encounter_split"])
        assert split == pytest.approx(data["kpis"]["paid"] + data["kpis"]["outstanding"])

    def test_the_encounter_split_is_ranked_by_value(self, book):
        rows = overview(book)["encounter_split"]
        assert rows[0]["encounter_type"] == "Ip"
        assert [r["total_value"] for r in rows] == sorted(
            [r["total_value"] for r in rows], reverse=True
        )

    def test_the_claims_window_reports_what_the_figures_cover(self, book):
        window = overview(book)["claims_window"]
        assert window["from"] == "2026-05-10"
        assert window["to"] == "2026-07-15"


class TestBookPosition:
    def test_the_hot_account_sits_at_the_top_of_the_book(self, book):
        position = overview(book)["book_position"]
        assert position["accounts"] == 2
        assert position["loss_ratio_percentile"] == 100

    def test_the_comparison_is_drawn_against_the_same_book_the_row_came_from(self, book):
        # Both accounts must see the same book size, or the percentile
        # depends on who is being looked at.
        assert overview(book)["book_position"]["accounts"] == 2
        assert overview(book, "Steady Holdings")["book_position"]["accounts"] == 2

    def test_per_life_figures_are_reported(self, book):
        position = overview(book)["book_position"]
        assert position["premium_per_life"] == pytest.approx(1000000.0 / 3)
        assert position["claims_per_life"] > 0


class TestScoping:
    def test_one_account_does_not_see_another_accounts_claims(self, book):
        data = overview(book, "Steady Holdings")
        ids = [c["beneficiary_id"] for c in data["top_claimants"]]
        assert ids and all(i.startswith("STD") for i in ids)

    def test_the_master_client_lookup_is_case_insensitive(self, book):
        resp = book.get("/portfolio-analysis/account-overview/kaf holdings",
                        params={"as_of": AS_OF})
        assert resp.status_code == 200
        assert resp.json()["kpis"]["member_count"] == 3

    def test_an_unknown_account_is_a_404(self, book):
        resp = book.get("/portfolio-analysis/account-overview/Nobody Ltd",
                        params={"as_of": AS_OF})
        assert resp.status_code == 404

    def test_no_book_uploaded_is_a_400_not_a_500(self, client):
        resp = client.get("/portfolio-analysis/account-overview/KAF Holdings")
        assert resp.status_code == 400
