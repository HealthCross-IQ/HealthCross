from datetime import date
from typing import Any, BinaryIO, Dict, List

import pandas as pd

from app.ingestion.column_mapping import map_columns
from app.reference.nationality_zones import classify_zone

# Canonical field aliases. Primary labels match the broker's real "Member
# List" template (Category, Gender, DOB, Marital Status, Relation, Emirates,
# Salary, Nationality); the extra aliases keep other brokers' templates
# working without a rewrite.
CENSUS_ALIASES: Dict[str, List[str]] = {
    "employee_ref": ["employee id", "emp id", "staff no", "id", "member id"],
    "category": ["category", "plan category", "class", "plan class"],
    "age": ["age", "current age"],
    "date_of_birth": ["dob", "date of birth", "birth date"],
    "gender": ["gender", "sex"],
    "marital_status": ["marital status", "marital"],
    "relation": ["relation", "relationship", "member type"],
    "emirates": ["emirates", "location", "emirate"],
    "salary_band": ["salary", "salary band", "salary category"],
    "nationality": ["nationality", "country"],
    "dependents_count": ["dependents", "no of dependents", "number of dependents", "dependants"],
    "join_date": ["date joined", "join date", "hire date", "employment date"],
}

_SPOUSE_RELATIONS = {"wife", "husband", "spouse"}
_CHILD_RELATIONS = {"son", "daughter", "child", "children"}


def _calc_age(dob: date) -> int:
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _classify_relation(relation: Any) -> str:
    if not relation or (isinstance(relation, float) and pd.isna(relation)):
        return "other"
    value = str(relation).strip().lower()
    if value == "employee":
        return "employee"
    if value in _SPOUSE_RELATIONS:
        return "spouse"
    if value in _CHILD_RELATIONS:
        return "child"
    return "other"


def _normalize_marital_status(value: Any) -> str:
    if not value or (isinstance(value, float) and pd.isna(value)):
        return "unknown"
    return str(value).strip().lower()


def parse_census(file: BinaryIO, filename: str) -> List[dict]:
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file)

    df = map_columns(df, CENSUS_ALIASES)

    records = []
    for _, row in df.iterrows():
        age = row.get("age")
        if pd.isna(age) and "date_of_birth" in df.columns:
            dob = pd.to_datetime(row.get("date_of_birth"), errors="coerce")
            if pd.notna(dob):
                age = _calc_age(dob.date())

        gender = row.get("gender")
        if isinstance(gender, str) and gender.strip():
            gender = gender.strip().upper()[0]
        else:
            gender = None

        dependents = row.get("dependents_count")
        dependents = int(dependents) if pd.notna(dependents) else 0

        join_date = None
        if "join_date" in df.columns:
            jd = pd.to_datetime(row.get("join_date"), errors="coerce")
            if pd.notna(jd):
                join_date = jd.date()

        nationality = row.get("nationality")
        nationality = str(nationality).strip() if pd.notna(nationality) else None

        records.append(
            {
                "employee_ref": str(row.get("employee_ref")) if pd.notna(row.get("employee_ref")) else None,
                "category": str(row.get("category")) if pd.notna(row.get("category")) else None,
                "age": int(age) if pd.notna(age) else None,
                "gender": gender,
                "marital_status": _normalize_marital_status(row.get("marital_status")),
                "relation": _classify_relation(row.get("relation")),
                "emirates": row.get("emirates") if pd.notna(row.get("emirates")) else None,
                "salary_band": row.get("salary_band") if pd.notna(row.get("salary_band")) else None,
                "nationality": nationality,
                "nationality_zone": classify_zone(nationality) if nationality else None,
                "dependents_count": dependents,
                "join_date": join_date,
            }
        )
    return records
