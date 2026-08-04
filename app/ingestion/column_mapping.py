import re
from typing import Dict, List, Optional

import pandas as pd


def _normalize(col: str) -> str:
    col = str(col).strip().lower()
    col = re.sub(r"[^a-z0-9]+", "_", col)
    return col.strip("_")


def find_header_row(raw: pd.DataFrame, alias_map: Dict[str, List[str]], max_rows_to_scan: int = 20) -> Optional[int]:
    """Some real-world spreadsheets have a title/blank row (and blank
    leading columns) before the actual header - pd.read_excel's default
    header=0 then treats that blank/title row as the header, producing
    "Unnamed: N" columns that never match any alias, making a real file
    look completely empty and get rejected. `raw` must be read with
    header=None so every row is available to scan; this returns the index
    of the first row containing recognizable header text for at least two
    of the mapped fields (or all of them, if there's only one), or None if
    no such row is found in the first `max_rows_to_scan` rows.
    """
    required_hits = min(2, len(alias_map))
    for row_idx in range(min(max_rows_to_scan, len(raw))):
        normalized_cells = {_normalize(v) for v in raw.iloc[row_idx] if pd.notna(v)}
        hits = 0
        for canonical, aliases in alias_map.items():
            candidates = {_normalize(c) for c in [canonical] + aliases}
            if normalized_cells & candidates:
                hits += 1
        if hits >= required_hits:
            return row_idx
    return None


def map_columns(df: pd.DataFrame, alias_map: Dict[str, List[str]]) -> pd.DataFrame:
    """Rename df columns to canonical names using alias_map {canonical: [aliases]}.

    Broker census/benefit templates vary in header wording, so lookups are
    done on a normalized (lowercased, non-alphanumeric-stripped) form of both
    the actual columns and every alias candidate.
    """
    normalized = {_normalize(c): c for c in df.columns}
    rename = {}
    for canonical, aliases in alias_map.items():
        candidates = [canonical] + aliases
        for candidate in candidates:
            norm_candidate = _normalize(candidate)
            if norm_candidate in normalized:
                rename[normalized[norm_candidate]] = canonical
                break
    return df.rename(columns=rename)
