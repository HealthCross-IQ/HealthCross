"""Parses the dedicated Subgroup -> Master Group mapping sheet for
Portfolio Analysis (app/scoring/rules/portfolio_analysis.py). A member's
own MASTERCONTRACT column on the real system export is often unreliable
(observed in practice just duplicating the subgroup's own name), so
underwriting maintains this separate two-column file (Subgroup, Group
Name) as the authoritative source of which master policy each subgroup
actually rolls up under - distinct from the Group->Product mapping file,
which pairs a group with its Product rather than its master.
"""
from typing import BinaryIO, Dict, List

import pandas as pd

from app.ingestion.column_mapping import find_header_row, map_columns

SUBGROUP_MAPPING_ALIASES: Dict[str, List[str]] = {
    "subgroup": ["subgroup", "sub-group", "sub group", "contract"],
    "master_name": ["group name", "master group", "master policy", "master", "group"],
}


def parse_subgroup_mapping(file: BinaryIO, filename: str) -> List[dict]:
    if filename.lower().endswith(".csv"):
        raw = pd.read_csv(file, header=None)
    else:
        raw = pd.read_excel(file, header=None)

    # Real mapping sheets sometimes have a blank title row and/or blank
    # leading columns before the actual Subgroup/Group Name header (e.g. the
    # header starting a few rows/columns in rather than at A1) - find it
    # rather than assuming row 0, so the file isn't rejected as empty.
    header_row = find_header_row(raw, SUBGROUP_MAPPING_ALIASES)
    if header_row is None:
        df = raw.iloc[1:].copy()
        df.columns = raw.iloc[0]
    else:
        df = raw.iloc[header_row + 1 :].copy()
        df.columns = raw.iloc[header_row]
    df = df.loc[:, df.columns.notna()]
    df = map_columns(df, SUBGROUP_MAPPING_ALIASES)

    rows = []
    for _, row in df.iterrows():
        subgroup = row.get("subgroup")
        master_name = row.get("master_name")
        if pd.notna(subgroup) and pd.notna(master_name):
            rows.append(
                {
                    "subgroup_name": str(subgroup).strip(),
                    "master_name": str(master_name).strip(),
                    "source_filename": filename,
                }
            )
    return rows
