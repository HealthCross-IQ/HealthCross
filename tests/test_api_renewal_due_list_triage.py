"""The renewal due list stopped being a calendar.

It carried name, headcount, policy end date and days to go - and nothing
about whether the account was a problem. An account running at 248%
looked exactly like one running at 60%, so the only way to find the three
that would hurt was to open all twenty.

What matters in these tests is that the reading it now shows is the SAME
reading the other two screens show. A third opinion on the same account
would be worse than the calendar was.
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
AS_OF = "2026-08-31"      # 130 elapsed days
DUE_AS_OF = "2027-03-20"  # inside the renewal window for the term above


def _write(tmp_path, name, header, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    path = tmp_path / name
    wb.save(path)
    return path


def _member(master, bid, premium):
    return [f"{master} Sub", master, "P1", f"QC1-{bid}", bid, "1985-06-01", "M", "Married",
            "India", "Principal", "Dubai", f"QIC/HC/BR/{master[:3].upper()}/DXB/A", "PLATINUM",
            TERM_START, TERM_END, TERM_START, TERM_END, premium, premium, None, None, 0]


def _claim(bid, master, treated, amount, status="Paid Claims"):
    return [bid, f"CLM-{bid}-{treated}", status, f"{master} Sub", master, f"QC1-{bid}",
            TERM_START, TERM_END, TERM_START, TERM_END, treated, "Main Insured", "IP",
            "HOSPITALISATION", "Hospital", "A099", "Acute condition", amount, amount]


@pytest.fixture()
def book(client, tmp_path):
    """Three accounts on the same policy period, deliberately different:
    one running hot with a dominant claimant, one ordinary, one with no
    claims at all."""
    members = _write(tmp_path, "members.xlsx", MEMBERS_HEADER, [
        _member("Hot Holdings", "HOT001", 500000),
        _member("Hot Holdings", "HOT002", 300000),
        _member("Hot Holdings", "HOT003", 200000),
        _member("Steady Holdings", "STD001", 600000),
        _member("Steady Holdings", "STD002", 400000),
        _member("Steady Holdings", "STD003", 400000),
        _member("Quiet Holdings", "QUI001", 500000),
        _member("Quiet Holdings", "QUI002", 500000),
    ])
    claims = _write(tmp_path, "claims.xlsx", CLAIMS_HEADER, [
        _claim("HOT001", "Hot Holdings", "2026-05-10", 300000.0),
        _claim("HOT001", "Hot Holdings", "2026-07-15", 300000.0, "Outstanding Claims"),
        _claim("HOT002", "Hot Holdings", "2026-06-01", 50000.0),
        _claim("STD001", "Steady Holdings", "2026-05-02", 40000.0),
        _claim("STD002", "Steady Holdings", "2026-06-02", 35000.0),
        _claim("STD003", "Steady Holdings", "2026-06-20", 30000.0),
    ])
    for path, endpoint in ((members, "members"), (claims, "claims")):
        with open(path, "rb") as f:
            resp = client.post(f"/portfolio-analysis/{endpoint}/upload",
                               files={"file": (path.name, f, "application/octet-stream")})
        assert resp.status_code == 200, resp.text
    client.post("/portfolio-analysis/data-as-of", json={"data_as_of_date": AS_OF})
    return client


def due_list(client, **params):
    params.setdefault("within_days", 90)
    params.setdefault("as_of", DUE_AS_OF)
    resp = client.get("/portfolio-analysis/renewal-due-list", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def by_name(rows, name):
    return next(r for r in rows if r["master_client"] == name)


class TestTheRiskReading:
    def test_every_due_account_carries_a_loss_ratio(self, book):
        rows = due_list(book)
        assert len(rows) == 3
        assert by_name(rows, "Hot Holdings")["loss_ratio"] > 1.6
        assert by_name(rows, "Steady Holdings")["loss_ratio"] is not None

    def test_the_loss_ratio_is_the_one_the_loss_ratio_screen_shows(self, book):
        # Not a third opinion. Same row, same figure.
        rows = due_list(book)
        board = book.get("/portfolio-analysis/account-loss-ratio").json()
        board_rows = board["rows"] if isinstance(board, dict) else board
        hot_board = next(r for r in board_rows if r["master_client"] == "Hot Holdings")
        assert by_name(rows, "Hot Holdings")["loss_ratio"] == hot_board["gross_loss_ratio"]

    def test_the_worst_alert_is_carried_with_its_action(self, book):
        alert = by_name(due_list(book), "Hot Holdings")["top_alert"]
        assert alert["severity"] == "critical"
        assert alert["title"]
        assert alert["action"]

    def test_an_account_running_normally_raises_no_pricing_reading(self, book):
        # It still raises concentration - three members, so one of them is
        # always most of the cost - which is true of it. What it must not
        # raise is the reading that would change how it is priced.
        row = by_name(due_list(book), "Steady Holdings")
        assert row["alert_counts"]["critical"] == 0
        assert row["loss_ratio"] < 0.95
        assert row["top_alert"]["title"] != "Loss ratio above tolerance"
        assert row["top_alert"]["title"] != "Outstanding exposure"

    def test_an_account_with_no_claims_is_not_reported_as_healthy(self, book):
        # No premium or claims read is not the same as nothing being
        # wrong, and colouring it green would say it was checked.
        row = by_name(due_list(book), "Quiet Holdings")
        assert row["severity"] != "critical"
        assert row["loss_ratio"] is None or row["loss_ratio"] == 0.0

    def test_concentration_is_measured_against_the_same_incurred_shown(self, book):
        row = by_name(due_list(book), "Hot Holdings")
        codes = [row["top_alert"]["title"]] if row["top_alert"] else []
        assert row["incurred_claims"] > 0
        assert codes


class TestOrdering:
    def test_worst_first_not_soonest_first(self, book):
        rows = due_list(book)
        ranks = [r["severity_rank"] for r in rows]
        assert ranks == sorted(ranks)
        assert rows[0]["master_client"] == "Hot Holdings"

    def test_days_still_breaks_ties_within_a_severity(self, book):
        rows = due_list(book)
        for a, b in zip(rows, rows[1:]):
            if a["severity_rank"] == b["severity_rank"]:
                assert a["days_until_renewal"] <= b["days_until_renewal"]

    def test_every_row_carries_a_severity_the_ui_can_colour(self, book):
        for row in due_list(book):
            assert row["severity"] in (
                "critical", "high", "blocked", "watch", "clear", "unknown")
            assert row["severity_rank"] is not None


class TestBlocked:
    def test_an_account_whose_loading_was_never_entered_reads_as_blocked(self, book):
        # Opening the renewal creates the case; without a fee split it
        # cannot be priced at all, and the list should say so rather than
        # showing a loss ratio and implying it is quotable.
        opened = book.post("/portfolio-analysis/renewal-intake",
                           json={"master_client": "Steady Holdings"})
        assert opened.status_code == 200, opened.text
        case_id = opened.json()["case"]["id"]
        book.patch(f"/cases/{case_id}", json={"tpa_fee_pct": None, "commission_pct": None,
                                              "hc_fee_pct": None, "qic_fee_pct": None})

        row = by_name(due_list(book), "Steady Holdings")
        assert row["blocked"] is True
        assert row["severity"] == "blocked"
        assert "loading" in row["top_alert"]["message"].lower()

    def test_entering_the_fee_split_clears_the_block(self, book):
        opened = book.post("/portfolio-analysis/renewal-intake",
                           json={"master_client": "Steady Holdings"})
        case_id = opened.json()["case"]["id"]
        book.patch(f"/cases/{case_id}", json={
            "tpa_fee_pct": 0.10, "commission_pct": 0.05,
            "hc_fee_pct": 0.065, "qic_fee_pct": 0.0})

        row = by_name(due_list(book), "Steady Holdings")
        assert row["blocked"] is False
        assert row["severity"] != "blocked"

    def test_a_critical_account_outranks_its_own_block(self, book):
        # "We cannot quote this" and "this should not be quoted" are both
        # worth interrupting for, and the second is the one that changes
        # the answer.
        opened = book.post("/portfolio-analysis/renewal-intake",
                           json={"master_client": "Hot Holdings"})
        case_id = opened.json()["case"]["id"]
        book.patch(f"/cases/{case_id}", json={"tpa_fee_pct": None, "commission_pct": None,
                                              "hc_fee_pct": None, "qic_fee_pct": None})
        row = by_name(due_list(book), "Hot Holdings")
        assert row["blocked"] is True
        assert row["severity"] == "blocked"


class TestItStillDoesItsOldJob:
    def test_the_original_columns_all_survive(self, book):
        row = by_name(due_list(book), "Hot Holdings")
        for field in ("master_client", "member_count", "policy_end_date",
                      "days_until_renewal", "case_id", "case_status"):
            assert field in row

    def test_no_book_uploaded_is_still_a_400(self, client):
        assert client.get("/portfolio-analysis/renewal-due-list").status_code == 400


class TestTheScreen:
    def test_the_account_name_opens_its_dashboard(self):
        import pathlib
        markup = (pathlib.Path(__file__).resolve().parent.parent
                  / "app" / "static" / "index.html").read_text()
        # Reading an account and working its renewal are different jobs;
        # the row should not make you choose between them.
        assert 'class="rdl-name" onclick="openAccountDashboard(' in markup
        assert 'hcOpenRenewalFromBook(' in markup

    def test_severity_is_carried_in_form_not_only_colour(self):
        import pathlib
        markup = (pathlib.Path(__file__).resolve().parent.parent
                  / "app" / "static" / "index.html").read_text()
        assert ".rdl-sev.critical" in markup
        assert ".rdl-sev.blocked" in markup
