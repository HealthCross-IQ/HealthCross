"""Parses the "Client Master" reference sheet for Portfolio Analysis
(app/scoring/rules/portfolio_analysis.py) - a plain, underwriting-
maintained sheet keyed by master client name, principally carrying each
client's own real OPEX/Loading % (commission + TPA + admin + HC/
management fees, as a fraction of premium). Used in place of the flat
DEFAULT_EXPENSE_RATIO_PCT assumption for Combined Ratio wherever a
client's own real figure is on file - see
app/scoring/rules/portfolio_analysis.py's executive_portfolio_summary.
Product/Start Date are carried along for reference/display only.
"""
from typing import BinaryIO, Dict, List, Optional

import pandas as pd

from app.ingestion.column_mapping import find_header_row, map_columns

CLIENT_MASTER_ALIASES: Dict[str, List[str]] = {
    "master_client_name": [
        "client name (master)", "client name master", "master client name", "client (master)",
        "master client", "client name", "client",
    ],
    "opex_pct": ["opex", "opex %", "opex%", "opex pct", "loading", "loading %", "expense ratio", "expense ratio %"],
    "product": ["product", "product name"],
    "start_date": ["start date", "eff date", "effective date", "policy start date"],
}


def _parse_opex_pct(value) -> Optional[float]:
    """Accepts a fraction (0.275), a bare percent number (27.5), or a
    percent string ("27.5%") - whichever way underwriting happens to type
    it into the sheet - and always returns a fraction (0.275).
    """
    if pd.isna(value):
        return None
    if isinstance(value, str):
        value = value.strip().rstrip("%").strip()
        if not value:
            return None
        try:
            value = float(value)
        except ValueError:
            return None
    else:
        value = float(value)
    return value / 100 if value > 1 else value


def parse_client_master(file: BinaryIO, filename: str) -> List[dict]:
    if filename.lower().endswith(".csv"):
        raw = pd.read_csv(file, header=None)
    else:
        raw = pd.read_excel(file, header=None)

    header_row = find_header_row(raw, CLIENT_MASTER_ALIASES)
    if header_row is None:
        df = raw.iloc[1:].copy()
        df.columns = raw.iloc[0]
    else:
        df = raw.iloc[header_row + 1 :].copy()
        df.columns = raw.iloc[header_row]
    df = df.loc[:, df.columns.notna()]
    df = map_columns(df, CLIENT_MASTER_ALIASES)

    if "start_date" in df.columns:
        df["start_date"] = pd.to_datetime(df["start_date"], errors="coerce")

    rows = []
    for _, row in df.iterrows():
        master_client_name = row.get("master_client_name")
        if pd.isna(master_client_name) or not str(master_client_name).strip():
            continue
        start_date = row.get("start_date") if "start_date" in df.columns else None
        product = row.get("product") if "product" in df.columns else None
        rows.append(
            {
                "master_client_name": str(master_client_name).strip(),
                "opex_pct": _parse_opex_pct(row.get("opex_pct")) if "opex_pct" in df.columns else None,
                "product": str(product).strip() if pd.notna(product) else None,
                "start_date": start_date.date() if pd.notna(start_date) else None,
                "source_filename": filename,
            }
        )
    return rows
