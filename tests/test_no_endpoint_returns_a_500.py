"""No GET endpoint may crash, in any of the states a real portal is in.

A 4xx is an answer: "you have not uploaded a book", "that case does not
exist". A 500 is the server saying it does not know what happened, and
it is always a bug - it blanks whatever panel asked for it, with no way
for the underwriter to tell a missing upload from a broken screen. That
is exactly how "member premium gone" and "not working at all" happened:
one short dict, nine panels down at once.

So every GET is walked in three states, because each has taken a
different endpoint down before:

  empty     nothing uploaded at all - a brand new install
  bare      a case that exists and has nothing else
  loaded    a case with a census, a book, claims and a fee split

If this file fails, something reachable from the UI is throwing.
"""
from datetime import date


from app.main import app
from app.models import db_models as models

TERM_START = date(2025, 10, 1)
TERM_END = date(2026, 9, 30)
AS_OF = date(2026, 8, 15)

#: Endpoints whose path parameter is not a case id, with a value that
#: exercises them rather than a 404 by construction.
PATH_VALUES = {
    "case_id": "1",
    "master_client": "ACME TRADING",
    "client": "ACME TRADING",
    "report_id": "1",
    "plan_id": "1",
    "record_id": "1",
    "quote_id": "1",
}


def _get_routes():
    """Every GET route the app serves, with its path parameters filled in.

    Read off the OpenAPI schema rather than app.routes: FastAPI keeps
    included routers nested rather than flattened, and walking app.routes
    directly found one endpoint out of ninety while reporting success.
    """
    out = []
    for path, operations in app.openapi()["paths"].items():
        if "get" not in operations:
            continue
        if path in ("/", "/health"):
            continue
        concrete = path
        for name, value in PATH_VALUES.items():
            concrete = concrete.replace("{" + name + "}", value)
        if "{" in concrete:            # a parameter we have no sensible value for
            continue
        out.append((path, concrete))

    # The two printed documents are include_in_schema=False, so they are
    # absent from the schema - and they are exactly the pages that have
    # broken before. Walked by name rather than not at all.
    for hidden in ("/cases/{case_id}/renewal-report.html",
                   "/cases/{case_id}/underwriting-report.html"):
        out.append((hidden, hidden.replace("{case_id}", PATH_VALUES["case_id"])))

    return sorted(set(out))


ROUTES = _get_routes()


def _bare_case(client):
    return client.post("/cases", json={
        "broker_name": "Broker", "company_name": "ACME TRADING", "industry": "trading",
    }).json()["id"]


def _load(client, case_id):
    """A case with everything a renewal needs, and a book behind it."""
    client.patch(f"/cases/{case_id}", json={
        "business_type": "existing", "current_annual_premium": 500_000.0,
        "tpa_fee_pct": 0.065, "commission_pct": 0.15,
        "hc_fee_pct": 0.065, "qic_fee_pct": 0.05,
    })
    db = client.db_session_local()
    db.bulk_insert_mappings(models.PortfolioMember, [
        {"beneficiary_id": f"B{i}", "contract": "ACME TRADING",
         "master_contract": "ACME TRADING", "master_client_name": "ACME TRADING",
         "relation": "employee", "age": 35, "gender": "M", "category": "A",
         "product_name": "Gold", "nationality_zone": "Asia", "region": "Dubai",
         "policy_start_date": TERM_START, "policy_end_date": TERM_END,
         "member_start_date": TERM_START, "member_end_date": TERM_END,
         "gross_premium": 10_000.0, "actual_gross_premium": 9_500.0}
        for i in range(12)])
    db.bulk_insert_mappings(models.PortfolioClaimEntry, [
        {"patient_id": f"B{i}", "final_amount": 3_000.0, "claim_status": "Paid Claims",
         "date_of_treatment": date(2026, 3, 10), "group_name": "ACME TRADING",
         "client_name": "ACME TRADING", "provider_name": "Clinic",
         "diagnosis_description": "Consultation", "ip_op_maternity": "OP"}
        for i in range(12)])
    db.add(models.PortfolioDataSnapshot(data_as_of_date=AS_OF))
    db.add_all([
        models.CensusRecord(case_id=case_id, employee_ref=f"B{i}", category="A",
                            age=35, gender="M", relation="employee",
                            existing_annual_rate=10_000.0)
        for i in range(12)])
    db.add_all([
        models.ClaimsLedgerEntry(
            case_id=case_id, patient_id=f"B{i}", claim_id=f"C{i}",
            claim_status="Paid Claims", policy_start_date=TERM_START,
            policy_end_date=TERM_END, date_of_treatment=date(2026, m, 10),
            ip_op_maternity="OP", final_amount=2_000.0)
        for i in range(6) for m in range(1, 7)])
    db.commit()
    db.close()


def _walk(client, label):
    crashes = []
    for path, concrete in ROUTES:
        try:
            resp = client.get(concrete)
        except Exception as exc:                     # noqa: BLE001 - reporting, not handling
            crashes.append(f"{label}: GET {path} raised {type(exc).__name__}: {exc}")
            continue
        if resp.status_code >= 500:
            detail = resp.text[:300].replace("\n", " ")
            crashes.append(f"{label}: GET {path} -> {resp.status_code}  {detail}")
    return crashes


def test_there_are_routes_to_walk():
    # A guard on the guard: if route collection silently returns nothing,
    # every test below passes while checking not one endpoint.
    assert len(ROUTES) > 40, f"only found {len(ROUTES)} GET routes to walk"


def test_no_endpoint_crashes_on_an_empty_install(client):
    crashes = _walk(client, "empty")
    assert not crashes, "\n".join(crashes)


def test_no_endpoint_crashes_on_a_bare_case(client):
    _bare_case(client)
    crashes = _walk(client, "bare")
    assert not crashes, "\n".join(crashes)


def test_no_endpoint_crashes_on_a_fully_loaded_case(client):
    case_id = _bare_case(client)
    _load(client, case_id)
    crashes = _walk(client, "loaded")
    assert not crashes, "\n".join(crashes)


def test_no_endpoint_crashes_on_a_case_with_no_fee_split(client):
    # The state the loading gate creates: everything uploaded, price
    # withheld. Nine panels read the rating dict, and they must all
    # survive it having no price in it.
    case_id = _bare_case(client)
    _load(client, case_id)
    client.patch(f"/cases/{case_id}", json={
        "tpa_fee_pct": None, "commission_pct": None,
        "hc_fee_pct": None, "qic_fee_pct": None,
    })
    crashes = _walk(client, "no fee split")
    assert not crashes, "\n".join(crashes)
