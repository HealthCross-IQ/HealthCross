from app.reference.nationality_zones import (
    ZONE_ASIA,
    ZONE_EUROPE_AMERICAS,
    ZONE_MIDDLE_EAST,
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


def test_sub_saharan_and_unmapped_nationalities_fold_into_middle_east():
    # There is no 4th zone - Sub-Saharan Africa and anything else unmapped
    # counts toward Zone 2 (Middle East) instead.
    for nationality in ["Kenyan", "Nigerian", "South African", "Atlantis"]:
        assert classify_zone(nationality) == ZONE_MIDDLE_EAST


def test_classification_is_case_and_whitespace_insensitive():
    assert classify_zone("  iNdIaN  ") == ZONE_ASIA


def test_empty_nationality_falls_back_to_middle_east():
    assert classify_zone("") == ZONE_MIDDLE_EAST
    assert classify_zone(None) == ZONE_MIDDLE_EAST


def test_country_names_from_the_membership_export_do_not_fall_to_the_default():
    # These are the exact strings on the real export; every one of them
    # used to fall through to the Middle East default.
    for nationality in ["UNITED STATES OF AMERICA", "SWITZERLAND", "AUSTRIA", "NORWAY", "DENMARK",
                        "NEW ZEALAND", "CYPRUS", "HUNGARY", "BOSNIA HERZEGOVINA", "DOMINICAN REPUBLIC"]:
        assert classify_zone(nationality) == ZONE_EUROPE_AMERICAS, nationality
    for nationality in ["SOUTH KOREA", "PHILLIPINES", "PHILIPPINE"]:
        assert classify_zone(nationality) == ZONE_ASIA, nationality
    for nationality in ["T¿RKIYE", "UNITED ARAB EMIRATES", "PALESTINIAN TERRIT.", "SUDAN", "CAMEROON", "ARMENIA"]:
        assert classify_zone(nationality) == ZONE_MIDDLE_EAST, nationality
