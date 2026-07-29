import io

import pandas as pd

from app.ingestion.benefits import parse_table_of_benefits


def test_parse_table_of_benefits_normalizes_fields():
    df = pd.DataFrame(
        [
            {
                "Plan": "Gold",
                "Annual Limit": "1,000,000",
                "Room & Board": "Private",
                "Deductible": 0,
                "Coinsurance %": 100,
                "Area of Cover": "Worldwide",
                "Maternity Cover": "Yes",
                "Dental": "No",
            }
        ]
    )
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)

    plans = parse_table_of_benefits(buf, "tob.xlsx")

    assert len(plans) == 1
    plan = plans[0]
    assert plan["annual_limit"] == 1_000_000.0
    assert plan["room_type"] == "private"
    assert plan["network_type"] == "worldwide"
    assert plan["maternity_covered"] is True
    assert plan["dental_covered"] is False
