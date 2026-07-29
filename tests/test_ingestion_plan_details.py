import io

import pandas as pd

from app.ingestion.plan_details import parse_plan_details


def _client_plan_xlsx() -> io.BytesIO:
    rows = [
        ["MANDATORY DETAILS", None],
        ["Broker", "Acme Brokers"],
        ["Renewal Date", "2026-01-01"],
        ["Existing Insurer", "Daman"],
        ["No of Years with Existing Insurer", 3],
        ["Target Premium", "USD 120,000"],
        ["Claims Available", "Yes"],
        ["Location", "DXB,AUH,NE"],
        ["Industry", "Trading"],
    ]
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="CLIENT & PLAN details", index=False, header=False)
    buf.seek(0)
    return buf


def test_parse_plan_details_extracts_mandatory_fields():
    extracted = parse_plan_details(_client_plan_xlsx(), "plan.xlsx")

    assert extracted["broker_name"] == "Acme Brokers"
    assert extracted["existing_insurer"] == "Daman"
    assert extracted["years_with_existing_insurer"] == 3
    assert extracted["target_premium"] == 120000.0
    assert extracted["claims_available"] is True
    assert extracted["region"] == "DXB,AUH,NE"
    assert extracted["industry"] == "Trading"
