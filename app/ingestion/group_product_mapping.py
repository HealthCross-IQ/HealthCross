"""Parses underwriting's own Group -> Product mapping spreadsheet for
Portfolio Analysis (app/scoring/rules/portfolio_analysis.py) - which New
Business Product (Platinum/Gold/Silver/Bronze) each already-booked
group is actually on, since that isn't captured on the membership export
itself. A manually-prepared file (unlike the fixed system exports in
app/ingestion/portfolio_members.py/portfolio_claims.py), so this uses
alias-based column matching like app/ingestion/census.py rather than
fixed column names.

The file carries the sub-group (PortfolioMember.contract) and master
group (PortfolioMember.master_contract) as two DISTINCT columns rather
than one - both get their own mapping row when present, since
resolve_group_product() looks a member's own sub-group up first and
falls back to its master group only when the sub-group itself has no
entry.
"""
from typing import BinaryIO, Dict, List

import pandas as pd

from app.ingestion.column_mapping import map_columns

GROUP_PRODUCT_ALIASES: Dict[str, List[str]] = {
    "subgroup": ["subgroup", "sub-group", "sub group", "contract", "group name", "group"],
    "master_group": ["master group", "master contract", "mastercontract", "master_contract"],
    "product": ["product", "product name", "product tier", "tier"],
}


def parse_group_product_mapping(file: BinaryIO, filename: str) -> List[dict]:
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file)

    df = map_columns(df, GROUP_PRODUCT_ALIASES)

    rows = []
    for _, row in df.iterrows():
        product = row.get("product")
        if pd.isna(product):
            continue
        product = str(product).strip()

        subgroup = row.get("subgroup")
        if pd.notna(subgroup):
            rows.append({"group_name": str(subgroup).strip(), "product": product, "source_filename": filename})

        master_group = row.get("master_group")
        if pd.notna(master_group):
            rows.append({"group_name": str(master_group).strip(), "product": product, "source_filename": filename})
    return rows
