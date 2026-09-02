"""Maps a census row's own `emirates` value onto the 3 regions the New
Business rate card (app/scoring/rules/new_business_rating.py) is actually
priced by: Dubai, Abu Dhabi, and Northern Emirates (Sharjah, Ajman, Ras Al
Khaimah, Fujairah, Umm Al Quwain, and Al Ain - grouped together the same way
insurers themselves price them, as one "Northern Emirates" tier distinct
from Dubai and Abu Dhabi).

Real census files write the emirate as a THREE-LETTER CODE at least as
often as in full - AUH, DXB, SHJ, NE. Only the full names were mapped
here, and the fallback is Dubai, so every one of those codes except NE
resolved to Dubai: AUH priced an Abu Dhabi member on the Dubai card, and
SHJ and AJM priced Northern Emirates members on it too. DXB looked
correct and was not - it was the fallback, not a match.

Abu Dhabi is the expensive one to get wrong. It has its own regulated
rate card, and it is the only region where the married-female maternity
surcharge is priced above nil (see price_member), so an AUH member read
as Dubai is priced on the wrong card AND loses the surcharge.
"""

REGION_DUBAI = "Dubai"
REGION_ABU_DHABI = "Abu Dhabi"
REGION_NORTHERN_EMIRATES = "Northern Emirates"

_DUBAI_NAMES = {
    "dubai",
    "dxb",
}

_ABU_DHABI_NAMES = {
    "abu dhabi",
    "abudhabi",
    "auh",
    "ad",
}

_NORTHERN_EMIRATES_NAMES = {
    "sharjah",
    "shj",
    "ajman",
    "ajm",
    "ras al khaimah",
    "ras al khaima",
    "rak",
    "fujairah",
    "fuj",
    "umm al quwain",
    "uaq",
    "al ain",
    "aan",
    "northern emirates",
    "northern emirate",
    "ne",
}


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())


def region_for_emirate(emirate: str) -> str:
    """Falls back to Dubai for anything unmapped (including missing/blank
    values) rather than raising - the most common case on a real census by
    far, so a single unfamiliar/missing emirate value never blocks pricing
    a whole census.

    That fallback is also what hid AUH resolving to Dubai for as long as
    it did: an unrecognised code does not announce itself, it just prices
    as Dubai. Anything added here should be added to the sets above rather
    than left to fall through.
    """
    if not emirate:
        return REGION_DUBAI
    normalized = _normalize(str(emirate))
    if normalized in _ABU_DHABI_NAMES:
        return REGION_ABU_DHABI
    if normalized in _NORTHERN_EMIRATES_NAMES:
        return REGION_NORTHERN_EMIRATES
    if normalized in _DUBAI_NAMES:
        return REGION_DUBAI
    return REGION_DUBAI
