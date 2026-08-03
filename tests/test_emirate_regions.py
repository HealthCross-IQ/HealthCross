from app.reference.emirate_regions import REGION_ABU_DHABI, REGION_DUBAI, REGION_NORTHERN_EMIRATES, region_for_emirate


def test_dubai_and_abu_dhabi_map_directly():
    assert region_for_emirate("Dubai") == REGION_DUBAI
    assert region_for_emirate("Abu Dhabi") == REGION_ABU_DHABI


def test_sharjah_and_other_northern_emirates_fold_into_one_region():
    assert region_for_emirate("Sharjah") == REGION_NORTHERN_EMIRATES
    assert region_for_emirate("Ras Al Khaimah") == REGION_NORTHERN_EMIRATES
    assert region_for_emirate("Fujairah") == REGION_NORTHERN_EMIRATES


def test_unmapped_or_missing_falls_back_to_dubai():
    assert region_for_emirate("") == REGION_DUBAI
    assert region_for_emirate(None) == REGION_DUBAI
    assert region_for_emirate("Some Unknown Place") == REGION_DUBAI


def test_case_and_whitespace_insensitive():
    assert region_for_emirate("  ABU dhabi  ") == REGION_ABU_DHABI
