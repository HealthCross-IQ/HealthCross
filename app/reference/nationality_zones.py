"""Nationality-to-underwriting-zone classification.

Zones follow the broker convention: Asian nationalities in one zone, Middle
East in a second, Europe/Americas in a third. A fourth "other" zone catches
nationalities that don't fit those three groups (mainly Sub-Saharan Africa)
so nothing silently disappears from the risk calculation.

The per-zone risk multiplier is intentionally left neutral (1.0) here -
direction and magnitude are not asserted by underwriting policy, so they are
stored on the active ScoringWeightSet and adjusted over time by the
feedback/recalibration loop as real case outcomes accumulate.
"""

ZONE_ASIA = "zone_1_asia"
ZONE_MIDDLE_EAST = "zone_2_middle_east"
ZONE_EUROPE_AMERICAS = "zone_3_europe_americas"
ZONE_OTHER = "zone_4_other"

ALL_ZONES = [ZONE_ASIA, ZONE_MIDDLE_EAST, ZONE_EUROPE_AMERICAS, ZONE_OTHER]

# Keys are lower-cased nationality/country strings as they commonly appear on
# broker census templates (adjective form or bare country name - both show
# up in practice, plus a few common typos seen on real submissions).
_NATIONALITY_ZONE_MAP = {
    # Zone 1: Asia
    "indian": ZONE_ASIA,
    "india": ZONE_ASIA,
    "sri lankan": ZONE_ASIA,
    "sri lanka": ZONE_ASIA,
    "nepali": ZONE_ASIA,
    "nepal": ZONE_ASIA,
    "filipino": ZONE_ASIA,
    "filipina": ZONE_ASIA,
    "philippines": ZONE_ASIA,
    "indonesian": ZONE_ASIA,
    "indonesia": ZONE_ASIA,
    "myanmar": ZONE_ASIA,
    "burmese": ZONE_ASIA,
    "pakistani": ZONE_ASIA,
    "pakistan": ZONE_ASIA,
    "bangladeshi": ZONE_ASIA,
    "bangladesh": ZONE_ASIA,
    "kyrgyzstan": ZONE_ASIA,
    "kyrgyz": ZONE_ASIA,
    "uzbek": ZONE_ASIA,
    "uzbekistan": ZONE_ASIA,
    "kazakh": ZONE_ASIA,
    "kazakhstan": ZONE_ASIA,
    "tajik": ZONE_ASIA,
    "tajikistan": ZONE_ASIA,
    "chinese": ZONE_ASIA,
    "china": ZONE_ASIA,
    "japanese": ZONE_ASIA,
    "japan": ZONE_ASIA,
    "thai": ZONE_ASIA,
    "thailand": ZONE_ASIA,
    "vietnamese": ZONE_ASIA,
    "vietnam": ZONE_ASIA,
    "malaysian": ZONE_ASIA,
    "malaysia": ZONE_ASIA,
    "singaporean": ZONE_ASIA,
    "singapore": ZONE_ASIA,
    "korean": ZONE_ASIA,
    "afghan": ZONE_ASIA,
    "afghanistan": ZONE_ASIA,
    "cambodian": ZONE_ASIA,
    "bhutanese": ZONE_ASIA,

    # Zone 2: Middle East (incl. North Africa / MENA, per broker convention)
    "emirati": ZONE_MIDDLE_EAST,
    "uae": ZONE_MIDDLE_EAST,
    "saudi": ZONE_MIDDLE_EAST,
    "saudi arabian": ZONE_MIDDLE_EAST,
    "egyptian": ZONE_MIDDLE_EAST,
    "egypt": ZONE_MIDDLE_EAST,
    "jordanian": ZONE_MIDDLE_EAST,
    "jordan": ZONE_MIDDLE_EAST,
    "lebanese": ZONE_MIDDLE_EAST,
    "lebanon": ZONE_MIDDLE_EAST,
    "syrian": ZONE_MIDDLE_EAST,
    "syria": ZONE_MIDDLE_EAST,
    "yemeni": ZONE_MIDDLE_EAST,
    "yemen": ZONE_MIDDLE_EAST,
    "omani": ZONE_MIDDLE_EAST,
    "oman": ZONE_MIDDLE_EAST,
    "qatari": ZONE_MIDDLE_EAST,
    "qatar": ZONE_MIDDLE_EAST,
    "bahraini": ZONE_MIDDLE_EAST,
    "bahrain": ZONE_MIDDLE_EAST,
    "kuwaiti": ZONE_MIDDLE_EAST,
    "kuwait": ZONE_MIDDLE_EAST,
    "iraqi": ZONE_MIDDLE_EAST,
    "iraq": ZONE_MIDDLE_EAST,
    "iranian": ZONE_MIDDLE_EAST,
    "iran": ZONE_MIDDLE_EAST,
    "palestinian": ZONE_MIDDLE_EAST,
    "palestine": ZONE_MIDDLE_EAST,
    "palestinian territory, occupied": ZONE_MIDDLE_EAST,
    "palestinian territories": ZONE_MIDDLE_EAST,
    "libyan": ZONE_MIDDLE_EAST,
    "libya": ZONE_MIDDLE_EAST,
    "moroccan": ZONE_MIDDLE_EAST,
    "morocco": ZONE_MIDDLE_EAST,
    "algerian": ZONE_MIDDLE_EAST,
    "algeria": ZONE_MIDDLE_EAST,
    "tunisian": ZONE_MIDDLE_EAST,
    "tunisia": ZONE_MIDDLE_EAST,
    "turkish": ZONE_MIDDLE_EAST,
    "turkey": ZONE_MIDDLE_EAST,

    # Zone 3: Europe & Americas
    "french": ZONE_EUROPE_AMERICAS,
    "france": ZONE_EUROPE_AMERICAS,
    "italian": ZONE_EUROPE_AMERICAS,
    "italy": ZONE_EUROPE_AMERICAS,
    "russian": ZONE_EUROPE_AMERICAS,
    "russia": ZONE_EUROPE_AMERICAS,
    "ukrainian": ZONE_EUROPE_AMERICAS,
    "ukraine": ZONE_EUROPE_AMERICAS,
    "greek": ZONE_EUROPE_AMERICAS,
    "greece": ZONE_EUROPE_AMERICAS,
    "colombia": ZONE_EUROPE_AMERICAS,
    "colombian": ZONE_EUROPE_AMERICAS,
    "brazil": ZONE_EUROPE_AMERICAS,
    "brazilian": ZONE_EUROPE_AMERICAS,
    "argentina": ZONE_EUROPE_AMERICAS,
    "argentinian": ZONE_EUROPE_AMERICAS,
    "argentinean": ZONE_EUROPE_AMERICAS,
    "mexican": ZONE_EUROPE_AMERICAS,
    "mexicans": ZONE_EUROPE_AMERICAS,
    "mexico": ZONE_EUROPE_AMERICAS,
    "romanian": ZONE_EUROPE_AMERICAS,
    "romania": ZONE_EUROPE_AMERICAS,
    "british": ZONE_EUROPE_AMERICAS,
    "uk": ZONE_EUROPE_AMERICAS,
    "united kingdom": ZONE_EUROPE_AMERICAS,
    "belgian": ZONE_EUROPE_AMERICAS,
    "belgium": ZONE_EUROPE_AMERICAS,
    "croatia": ZONE_EUROPE_AMERICAS,
    "croatian": ZONE_EUROPE_AMERICAS,
    "canadian": ZONE_EUROPE_AMERICAS,
    "canada": ZONE_EUROPE_AMERICAS,
    "cuba": ZONE_EUROPE_AMERICAS,
    "cuban": ZONE_EUROPE_AMERICAS,
    "slovakia": ZONE_EUROPE_AMERICAS,
    "slovak": ZONE_EUROPE_AMERICAS,
    "portuguese": ZONE_EUROPE_AMERICAS,
    "portugal": ZONE_EUROPE_AMERICAS,
    "serbian": ZONE_EUROPE_AMERICAS,
    "surbia": ZONE_EUROPE_AMERICAS,  # common misspelling of Serbia
    "serbia": ZONE_EUROPE_AMERICAS,
    "german": ZONE_EUROPE_AMERICAS,
    "germany": ZONE_EUROPE_AMERICAS,
    "spanish": ZONE_EUROPE_AMERICAS,
    "spain": ZONE_EUROPE_AMERICAS,
    "american": ZONE_EUROPE_AMERICAS,
    "usa": ZONE_EUROPE_AMERICAS,
    "united states": ZONE_EUROPE_AMERICAS,
    "european unknown": ZONE_EUROPE_AMERICAS,
    "peruvian": ZONE_EUROPE_AMERICAS,
    "peru": ZONE_EUROPE_AMERICAS,
    "ecuadorian": ZONE_EUROPE_AMERICAS,
    "ecuador": ZONE_EUROPE_AMERICAS,
    "belarus": ZONE_EUROPE_AMERICAS,
    "belarusian": ZONE_EUROPE_AMERICAS,
    "moldava": ZONE_EUROPE_AMERICAS,  # common misspelling of Moldova
    "moldovan": ZONE_EUROPE_AMERICAS,
    "moldova": ZONE_EUROPE_AMERICAS,
    "albanian": ZONE_EUROPE_AMERICAS,
    "albania": ZONE_EUROPE_AMERICAS,
    "australian": ZONE_EUROPE_AMERICAS,
    "australia": ZONE_EUROPE_AMERICAS,
    "new zealander": ZONE_EUROPE_AMERICAS,
    "polish": ZONE_EUROPE_AMERICAS,
    "poland": ZONE_EUROPE_AMERICAS,
    "dutch": ZONE_EUROPE_AMERICAS,
    "netherlands": ZONE_EUROPE_AMERICAS,
    "swiss": ZONE_EUROPE_AMERICAS,
    "swedish": ZONE_EUROPE_AMERICAS,
    "sweden": ZONE_EUROPE_AMERICAS,
    "irish": ZONE_EUROPE_AMERICAS,
    "ireland": ZONE_EUROPE_AMERICAS,

    # Zone 4: other (predominantly Sub-Saharan Africa in the sample data)
    "kenyan": ZONE_OTHER,
    "kenya": ZONE_OTHER,
    "zimbabwe": ZONE_OTHER,
    "zimbabwean": ZONE_OTHER,
    "ghanian": ZONE_OTHER,  # common spelling seen on census templates
    "ghanaian": ZONE_OTHER,
    "ghana": ZONE_OTHER,
    "ugandan": ZONE_OTHER,
    "uganda": ZONE_OTHER,
    "zambian": ZONE_OTHER,
    "zambia": ZONE_OTHER,
    "nigerian": ZONE_OTHER,
    "nigeria": ZONE_OTHER,
    "south african": ZONE_OTHER,
    "south africa": ZONE_OTHER,
    "ethiopian": ZONE_OTHER,
    "ethiopia": ZONE_OTHER,
    "tanzanian": ZONE_OTHER,
    "tanzania": ZONE_OTHER,
}


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())


def classify_zone(nationality: str) -> str:
    """Return the underwriting zone for a nationality string.

    Falls back to ZONE_OTHER for anything unmapped rather than raising, so a
    single unfamiliar nationality never blocks scoring a whole census.
    """
    if not nationality:
        return ZONE_OTHER
    return _NATIONALITY_ZONE_MAP.get(_normalize(str(nationality)), ZONE_OTHER)
