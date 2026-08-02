import io

import pandas as pd

from app.ingestion.census import parse_census
from app.reference.nationality_zones import ZONE_ASIA


def _xlsx(df: pd.DataFrame) -> io.BytesIO:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf


def test_parse_census_matches_real_broker_template_columns():
    df = pd.DataFrame(
        [
            {
                "Category": "D",
                "Gender": "F",
                "DOB": "1999-08-10",
                "Marital Status": "Single",
                "Relation": "Employee",
                "Emirates": "Dubai",
                "Salary": "HSB",
                "Nationality": "Indian",
            },
            {
                "Category": "D",
                "Gender": "M",
                "DOB": "1993-04-01",
                "Marital Status": "Married",
                "Relation": "Employee",
                "Emirates": "Dubai",
                "Salary": "HSB",
                "Nationality": "Nepali",
            },
        ]
    )
    records = parse_census(_xlsx(df), "census.xlsx")

    assert len(records) == 2
    assert records[0]["gender"] == "F"
    assert records[0]["relation"] == "employee"
    assert records[0]["marital_status"] == "single"
    assert records[0]["nationality_zone"] == ZONE_ASIA
    assert records[0]["age"] is not None


def test_parse_census_classifies_relation_labels():
    df = pd.DataFrame(
        [
            {"Gender": "F", "DOB": "1990-01-01", "Marital Status": "Married", "Relation": "Wife", "Nationality": "Filipino"},
            {"Gender": "F", "DOB": "2020-01-01", "Marital Status": "Single", "Relation": "Daughter", "Nationality": "Filipino"},
            {"Gender": "M", "DOB": "2018-01-01", "Marital Status": "Single", "Relation": "Son", "Nationality": "Filipino"},
        ]
    )
    records = parse_census(_xlsx(df), "census.xlsx")

    assert records[0]["relation"] == "spouse"
    assert records[1]["relation"] == "child"
    assert records[2]["relation"] == "child"


def test_parse_census_derives_age_from_dob_when_age_missing():
    df = pd.DataFrame([{"Gender": "M", "DOB": "1990-01-01", "Marital Status": "Single", "Relation": "Employee", "Nationality": "Indian"}])
    records = parse_census(_xlsx(df), "census.xlsx")
    assert records[0]["age"] is not None
    assert records[0]["age"] >= 34


def test_parse_census_derives_age_as_of_policy_start_date_not_today():
    df = pd.DataFrame(
        [
            {
                "Gender": "M",
                "DOB": "1990-06-15",
                "Marital Status": "Single",
                "Relation": "Employee",
                "Nationality": "Indian",
                "Eff Date": "2020-01-01",
            }
        ]
    )
    records = parse_census(_xlsx(df), "census.xlsx")
    # Turns 30 on 2020-06-15; policy started before that, so age at
    # policy start is 29, not the member's current-day age.
    assert records[0]["age"] == 29
    assert records[0]["policy_start_date"].isoformat() == "2020-01-01"
