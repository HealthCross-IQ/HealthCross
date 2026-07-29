import re
from typing import Dict, List

import pandas as pd


def _normalize(col: str) -> str:
    col = str(col).strip().lower()
    col = re.sub(r"[^a-z0-9]+", "_", col)
    return col.strip("_")


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
