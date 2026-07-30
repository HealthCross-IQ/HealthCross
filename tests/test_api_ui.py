def test_root_serves_the_ui_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "HealthCross" in resp.text


def test_list_cases_returns_created_cases(client):
    resp = client.post("/cases", json={
        "broker_name": "Broker A",
        "company_name": "Company A",
        "industry": "trading",
    })
    assert resp.status_code == 200
    case_id = resp.json()["id"]

    resp = client.get("/cases")
    assert resp.status_code == 200
    cases = resp.json()
    assert any(c["id"] == case_id for c in cases)


def test_list_cases_empty_when_none_created(client):
    resp = client.get("/cases")
    assert resp.status_code == 200
    assert resp.json() == []
