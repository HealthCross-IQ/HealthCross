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
    "relation": ["relation", "relationship", "member type", "dependency"],
    "emirates": ["emirates", "location", "emirate"],
    "salary_band": ["salary", "salary band", "salary category"],
    "nationality": ["nationality", "country"],
    "dependents_count": ["dependents", "no of dependents", "number of dependents", "dependants"],
    "join_date": ["date joined", "join date", "hire date", "employment date"],
    # The scheme's own fixed policy term (same value on every row) vs. each
    # member's own endorsement dates onto the scheme - see
    # app/scoring/rules/exposed_risk_population.py.
    "policy_start_date": ["eff date", "effective date", "policy start date"],
    "policy_end_date": ["exp date", "expiry date", "policy end date"],
    "member_start_date": ["endodate (member start date)", "member start date"],
    "member_end_date": ["endodate (member end date)", "member end date"],
}

# "main insured" is HealthCross's own book's word for a principal - it is
# what the Membership export's DEPENDENCY column and the claims export's
# RELATION column both carry (see app/ingestion/portfolio_members.py). It
# belongs here rather than in "other": a broker census saying "Self" and
# the book saying "Main Insured" describe the same person, and classifying
# them differently splits one population in two everywhere relation is
# grouped on - census movement, relation mix, exposed-risk population, and
# the member-for-member renewal matching in
# app/scoring/rules/member_movement.py, which cannot pair a principal
# against themselves if the two sources disagree about what they are.
_EMPLOYEE_RELATIONS = {
    "employee", "employees", "principal", "principle", "main member", "member",
    "self", "main insured", "main", "insured", "primary", "primary insured",
    "staff", "employee/self", "self/employee",
}
_SPOUSE_RELATIONS = {"wife", "husband", "spouse", "partner"}
_CHILD_RELATIONS = {"son", "daughter", "child", "children", "dependent child"}


def _calc_age(dob: date, as_of: date | None = None) -> int:
    """Floored at 0 - age_as_of policy_start_date rather than today (see
    callers) can go negative for a newborn added mid-term whose DOB falls
    after the scheme's own start date. A negative age isn't meaningful
    and silently drops the member from every age band in
    census_demographic_summary (0 <= age <= 17 never matches -1), so
    treat any pre-inception DOB as age 0 (infant) instead."""
    as_of = as_of or date.today()
    age = as_of.year - dob.year - ((as_of.month, as_of.day) < (dob.month, dob.day))
    return max(age, 0)


def _classify_relation(relation: Any) -> str:
    if not relation or (isinstance(relation, float) and pd.isna(relation)):
        return "other"
    value = str(relation).strip().lower()
    if value in _EMPLOYEE_RELATIONS:
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


def parse_census(file: BinaryIO, filename: str, default_policy_start_date: date | None = None) -> List[dict]:
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file)

    df = map_columns(df, CENSUS_ALIASES)

    records = []
    for _, row in df.iterrows():
        def _date_col(col_name):
            if col_name not in df.columns:
                return None
            parsed = pd.to_datetime(row.get(col_name), errors="coerce")
            return parsed.date() if pd.notna(parsed) else None

        policy_start_date = _date_col("policy_start_date") or default_policy_start_date

        age = row.get("age")
        date_of_birth = None
        if "date_of_birth" in df.columns:
            parsed_dob = pd.to_datetime(row.get("date_of_birth"), errors="coerce")
            date_of_birth = parsed_dob.date() if pd.notna(parsed_dob) else None
        if pd.isna(age) and "date_of_birth" in df.columns:
            dob = pd.to_datetime(row.get("date_of_birth"), errors="coerce")
            if pd.notna(dob):
                # Age as of the scheme's own inception/renewal date, not
                # today's date - underwriting age bands are fixed at
                # policy start, not recomputed on every ingestion run.
                age = _calc_age(dob.date(), policy_start_date)

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
                "category": (str(row.get("category")).strip().upper() or None) if pd.notna(row.get("category")) else None,
                "date_of_birth": date_of_birth,
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
                "policy_start_date": policy_start_date,
                "policy_end_date": _date_col("policy_end_date"),
                "member_start_date": _date_col("member_start_date"),
                "member_end_date": _date_col("member_end_date"),
            }
        )
    return records
