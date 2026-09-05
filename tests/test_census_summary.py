from app.scoring.rules.census_summary import census_demographic_summary


def _member(age, gender, marital_status="single", relation="employee", nationality_zone="zone_1_asia", nationality=None):
    return {
        "age": age,
        "gender": gender,
        "marital_status": marital_status,
        "relation": relation,
        "nationality_zone": nationality_zone,
        "nationality": nationality,
    }


def test_empty_census_returns_zero_total():
    assert census_demographic_summary([]) == {"total_members": 0}


def test_age_band_counts_and_pct():
    census = [
        _member(10, "M", relation="child"),
        _member(25, "F"),
        _member(50, "M"),
        _member(65, "F"),
        _member(80, "M"),
    ]
    summary = census_demographic_summary(census)
    assert summary["total_members"] == 5
    assert summary["age_band_counts"] == {"0-17": 1, "18-40": 1, "41-59": 1, "60-69": 1, "70-99": 1}
    assert summary["age_band_pct"]["0-17"] == 0.2
    assert summary["age_band_gender_counts"]["0-17"] == {"M": 1, "F": 0}
    assert summary["age_band_gender_counts"]["18-40"] == {"M": 0, "F": 1}


def test_married_female_and_maternity_risk_counts():
    census = [
        _member(30, "F", marital_status="married"),  # maternity risk
        _member(55, "F", marital_status="married"),  # married but out of maternity band
        _member(30, "F", marital_status="single"),
        _member(30, "M", marital_status="married"),
    ]
    summary = census_demographic_summary(census)
    assert summary["married_female_count"] == 2
    assert summary["maternity_risk_count"] == 1
    assert summary["maternity_risk_pct"] == 0.25


def test_relation_and_gender_breakdown():
    census = [
        _member(35, "M", relation="employee"),
        _member(35, "F", relation="employee"),
        _member(34, "F", relation="spouse"),
        _member(5, "M", relation="child"),
    ]
    summary = census_demographic_summary(census)
    assert summary["relation_counts"] == {"Employee": 2, "Spouse": 1, "Child": 1}
    assert summary["relation_gender_counts"]["Employee"] == {"M": 1, "F": 1}
    assert summary["relation_gender_counts"]["Spouse"] == {"M": 0, "F": 1}
    assert summary["gender_counts"] == {"M": 2, "F": 2, "Other": 0}
    assert summary["employee_count"] == 2
    assert summary["male_employees"] == 1
    assert summary["male_ratio_employees"] == 0.5


def test_marital_status_gender_breakdown_reveals_data_gaps_by_gender():
    census = [
        _member(30, "F", marital_status="married"),
        _member(30, "M", marital_status=None),
        _member(28, "M", marital_status=None),
    ]
    summary = census_demographic_summary(census)
    assert summary["marital_status_counts"] == {"Married": 1, "Unknown": 2}
    assert summary["marital_status_gender_counts"]["Married"] == {"M": 0, "F": 1}
    assert summary["marital_status_gender_counts"]["Unknown"] == {"M": 2, "F": 0}


def test_infant_vs_favorable_children():
    census = [
        _member(1, "M", relation="child"),
        _member(5, "F", relation="child"),
    ]
    summary = census_demographic_summary(census)
    assert summary["infant_count"] == 1
    assert summary["favorable_children_count"] == 1


def test_youth_share_counts_ages_2_to_25_of_both_sexes_and_leaves_infants_out():
    # The population the youth discount is decided on: 2-25, either sex,
    # any relation. The infant is not young in the favourable sense and
    # the 26-year-old is just outside.
    census = [
        _member(1, "M", relation="child"),
        _member(2, "F", relation="child"),
        _member(17, "M", relation="child"),
        _member(25, "F", relation="employee"),
        _member(26, "M", relation="employee"),
        _member(None, "M", relation="employee"),
    ]
    summary = census_demographic_summary(census)
    assert (summary["youth_age_min"], summary["youth_age_max"]) == (2, 25)
    assert summary["youth_count"] == 3
    assert summary["youth_pct"] == 0.5
    assert summary["youth_gender_counts"] == {"M": 1, "F": 2}


def test_unrecognized_or_missing_zone_folds_into_middle_east():
    # There is no 4th zone - a record with no zone, or a zone string left
    # over from before the 4th zone was folded away, must not KeyError and
    # must count toward Middle East.
    census = [
        _member(30, "M", nationality_zone=None),
        _member(30, "M", nationality_zone="zone_4_other"),
        _member(30, "M", nationality_zone="zone_2_middle_east"),
    ]
    summary = census_demographic_summary(census)
    assert summary["nationality_zone_counts"]["zone_2_middle_east"] == 3
    assert set(summary["nationality_zone_counts"].keys()) == {"zone_1_asia", "zone_2_middle_east", "zone_3_europe_americas"}


def test_top_5_nationalities_per_zone():
    census = [
        _member(30, "M", nationality_zone="zone_1_asia", nationality="Indian"),
        _member(31, "M", nationality_zone="zone_1_asia", nationality="Indian"),
        _member(32, "M", nationality_zone="zone_1_asia", nationality="Indian"),
        _member(33, "F", nationality_zone="zone_1_asia", nationality="Filipino"),
        _member(34, "F", nationality_zone="zone_1_asia", nationality="Nepali"),
        _member(40, "M", nationality_zone="zone_2_middle_east", nationality="Egyptian"),
    ]
    summary = census_demographic_summary(census)
    zone_1_top = summary["nationality_zone_top5"]["zone_1_asia"]
    assert zone_1_top[0] == {"nationality": "Indian", "count": 3}
    assert {"nationality": "Filipino", "count": 1} in zone_1_top
    assert summary["nationality_zone_top5"]["zone_2_middle_east"] == [{"nationality": "Egyptian", "count": 1}]
    assert summary["nationality_zone_top5"]["zone_3_europe_americas"] == []
