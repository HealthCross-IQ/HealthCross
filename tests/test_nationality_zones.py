from app.reference.nationality_zones import (
    ZONE_ASIA,
    ZONE_EUROPE_AMERICAS,
    ZONE_MIDDLE_EAST,
    ZONE_OTHER,
    classify_zone,
)


def test_asian_nationalities_map_to_zone_1():
    for nationality in ["Indian", "Filipino", "Nepali", "Sri Lankan", "Kyrgyzstan", "Indonesian", "Pakistani"]:
        assert classify_zone(nationality) == ZONE_ASIA


def test_middle_eastern_nationalities_map_to_zone_2():
    for nationality in ["Egyptian", "Saudi", "Lebanese", "Moroccan", "Turkish", "Syrian"]:
        assert classify_zone(nationality) == ZONE_MIDDLE_EAST


def test_european_and_american_nationalities_map_to_zone_3():
    for nationality in ["French", "Italian", "British", "American", "Canadian", "Russian", "Brazil"]:
        assert classify_zone(nationality) == ZONE_EUROPE_AMERICAS


def test_unmapped_nationalities_fall_back_to_other_zone():
    for nationality in ["Kenyan", "Nigerian", "South African", "Atlantis"]:
        assert classify_zone(nationality) == ZONE_OTHER


def test_classification_is_case_and_whitespace_insensitive():
    assert classify_zone("  iNdIaN  ") == ZONE_ASIA


def test_empty_nationality_falls_back_to_other():
    assert classify_zone("") == ZONE_OTHER
    assert classify_zone(None) == ZONE_OTHER
