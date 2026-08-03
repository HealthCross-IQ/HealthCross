"""Maps a census row's own `emirates` value onto the 3 regions the New
Business rate card (app/scoring/rules/new_business_rating.py) is actually
priced by: Dubai, Abu Dhabi, and Northern Emirates (Sharjah, Ajman, Ras Al
Khaimah, Fujairah, Umm Al Quwain, and Al Ain - grouped together the same way
insurers themselves price them, as one "Northern Emirates" tier distinct
from Dubai and Abu Dhabi).
"""

REGION_DUBAI = "Dubai"
REGION_ABU_DHABI = "Abu Dhabi"
REGION_NORTHERN_EMIRATES = "Northern Emirates"

_NORTHERN_EMIRATES_NAMES = {
    "sharjah",
    "ajman",
    "ras al khaimah",
    "rak",
    "fujairah",
    "umm al quwain",
    "uaq",
    "al ain",
    "northern emirates",
    "ne",
}


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())


def region_for_emirate(emirate: str) -> str:
    """Falls back to Dubai for anything unmapped (including missing/blank
    values) rather than raising - the most common case on a real census by
    far, so a single unfamiliar/missing emirate value never blocks pricing
    a whole census.
    """
    if not emirate:
        return REGION_DUBAI
    normalized = _normalize(str(emirate))
    if normalized == "abu dhabi":
        return REGION_ABU_DHABI
    if normalized in _NORTHERN_EMIRATES_NAMES:
        return REGION_NORTHERN_EMIRATES
    return REGION_DUBAI
