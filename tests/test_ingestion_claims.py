import io

import pandas as pd

from app.ingestion.claims import parse_claims


def test_parse_claims_basic():
    df = pd.DataFrame(
        [
            {"Member ID": "M1", "Date of Service": "2025-03-01", "Claim Type": "Inpatient", "Paid Amount": 15000},
            {"Member ID": "M2", "Date of Service": "2025-05-10", "Claim Type": "Outpatient", "Paid Amount": 500},
        ]
    )
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)

    claims = parse_claims(buf, "claims.xlsx")

    assert len(claims) == 2
    assert claims[0]["amount_paid"] == 15000.0
    assert claims[0]["policy_year"] == 2025
