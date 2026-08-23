"""Tests for app/reference/treatment_classification.py - splitting the
PARAMEDICAL category into what it actually contains.
"""
import pytest

from app.reference.treatment_classification import (
    classify_paramedical,
    is_alternative_treatment,
    is_physiotherapy,
)


@pytest.mark.parametrize("medical_act", [
    "Ayuverdic",                     # the book's own spelling - see below
    "Ayurvedic",                     # and the correct one
    "Osteopath",
    "Chiropractor",
    "Chiropracteur",                 # French, same treatment
    "Acupuncturist",
    "Homeopath",
    "Homeopathic Treatment",
    "Traditional Chinese Medicine",
])
def test_recognizes_alternative_treatments(medical_act):
    assert is_alternative_treatment(medical_act) is True
    assert classify_paramedical(medical_act) == "Alternative Treatment"


def test_recognizes_the_books_own_misspelling_of_ayurvedic():
    # The real export spells this "Ayuverdic" and never "Ayurvedic". It is
    # the single largest alternative therapy on the book, so a pattern
    # written only from the correct spelling silently misses most of the
    # category's value.
    assert is_alternative_treatment("Ayuverdic") is True


@pytest.mark.parametrize("medical_act", [
    "Physical Therapist",
    "Physiotherapist",
    "Kinésithérapeute",              # accented French
    "Kinesitherapeute",              # and unaccented
    "Occupational Therapist",
    "Speech Therapist",
    "Podologist",
])
def test_recognizes_physiotherapy_and_allied_rehab(medical_act):
    assert is_physiotherapy(medical_act) is True
    assert classify_paramedical(medical_act) == "Physiotherapy"


def test_accented_and_unaccented_spellings_land_in_the_same_bucket():
    assert classify_paramedical("Kinésithérapeute") == classify_paramedical("Kinesitherapeute")


@pytest.mark.parametrize("medical_act", [
    "Nursing services",
    "Soins infirmiers",
    "Midwife consultation fees",
    "Something nobody has seen before",
])
def test_anything_unrecognized_stays_its_own_bucket(medical_act):
    # Deliberately NOT folded into physiotherapy: an unrecognised
    # treatment should show up as unclassified rather than quietly
    # inflating a real benefit's cost.
    assert classify_paramedical(medical_act) == "Other Paramedical"


def test_handles_missing_treatment():
    assert classify_paramedical(None) == "Other Paramedical"
    assert is_alternative_treatment(None) is False
    assert is_physiotherapy(None) is False
