from app.reference.icd10_chapters import icd10_chapter


def test_neoplasm_codes_map_to_neoplasms():
    for code in ["C50", "C509", "D45"]:
        assert icd10_chapter(code) == "neoplasms"


def test_circulatory_codes_map_correctly():
    assert icd10_chapter("I219") == "diseases of circulatory system"


def test_cross_letter_span_resolves_correctly():
    # A00-B99 spans two starting letters - both ends must resolve correctly.
    assert icd10_chapter("A090") == "certain infectious and parasitic diseases"
    assert icd10_chapter("B99") == "certain infectious and parasitic diseases"
    # S00-T98 likewise.
    assert icd10_chapter("S720") == "injury, poisoning and external causes"
    assert icd10_chapter("T98") == "injury, poisoning and external causes"


def test_real_ledger_codes_from_service_plan_sample():
    assert icd10_chapter("F418") == "mental and behavioural disorders"
    assert icd10_chapter("R509") == "symptoms, signs and ill-defined conditions"
    assert icd10_chapter("J454") == "diseases of respiratory system"
    assert icd10_chapter("Z464") == "factors influencing health status"
    assert icd10_chapter("E559") == "endocrine, nutritional, metabolic, immunity"


def test_malformed_or_missing_code_returns_none():
    assert icd10_chapter(None) is None
    assert icd10_chapter("") is None
    assert icd10_chapter("???") is None
