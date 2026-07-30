from app.ingestion.claims_report import _parse_format2_from_rows, first_full_months

# Synthetic but structurally faithful to the real "HEALTH INSURANCE CLAIMS
# RECORD" (non-DHA-branded) format's extracted table rows: each row is
# [row_key, label, col2, col3, col4, col5, col6, col7].
SAMPLE_ROWS = [
    ["Part I", "Health Insurance Claims Record Summary", None, None, None, None, None, None],
    ["1", "Policy Number", None, None, None, None, None, None],
    ["", "OIGM202500178099 ; ; ; ; ;", None, None, None, None, None, None],
    ["2a", "Policy effective date", "08/08/2025", None, None, None, None, None],
    ["2b", "Policy expiry date", "07/08/2026", None, None, None, None, None],
    ["3a", "Report period start date", "08/08/2025", None, None, None, None, None],
    ["3b", "Report period end date", "09/05/2026", None, None, None, None, None],
    ["3c", "Report production date", "19/05/2026", None, None, None, None, None],
    ["4a", "Value of claims processed during report period only", "1,099,602", None, None, None, None, None],
    ["4c", "Value of claims incurred but not reported", "179,935", None, None, None, None, None],
    ["5a", "Male", "24", "2", "7", "34", "6", "0"],
    ["5b", "Single female", "25", "2", "5", "2", "0", "0"],
    ["5c", "Married female", "0", "0", "9", "23", "4", "0"],
    ["6a", "Male", "21", "2", "7", "35", "6", "-"],
    ["6b", "Single female", "27", "4", "4", "3", "-", "-"],
    ["6c", "Married female", "-", "-", "9", "21", "4", "-"],
    ["9a", "Dental", "-", "120,972", "-", None, None, "120,972"],
    [
        "9h",
        "Diverticulitis Of Large Intestine Without Perforation Or Abscess\nWithout Bleeding",
        "20,102",
        "-",
        "-",
        None,
        None,
        "20,102",
    ],
    ["10a", "Dental", "-", "161", "-", None, None, "161"],
    ["10h", "Diverticulitis...", "1", "-", "-", None, None, "1"],
    ["11a", "Alternative Medicine Non-Network Clinic", "-", "125,925", None, None, None, "125,925"],
    ["13a", "In Network", "147,086", "429,761", "174,537", "58,977", "3,667", "814,028"],
    ["13b", "Out of network", "-", "172,471", "15,693", "53,715", "3,551", "245,431"],
    ["16a", "08/2025", "31", None, "2025", "94,562", None, None],
    ["16b", "09/2025", "30", None, "2025", "166,262", None, None],
    ["16c", "10/2025", "31", None, "2025", "190,188", None, None],
    ["16d", "11/2025", "30", None, "2025", "135,826", None, None],
    ["16e", "12/2025", "31", None, "2025", "21,614", None, None],
    ["16f", "01/2026", "31", None, "2026", "167,572", None, None],
    ["16g", "02/2026", "28", None, "2026", "133,866", None, None],
    ["16m", "", "", None, "", "", None, None],
]


def test_parses_policy_number_from_the_row_below():
    result = _parse_format2_from_rows(SAMPLE_ROWS)
    assert result["policy_number"] == "OIGM202500178099"


def test_parses_slash_dates():
    result = _parse_format2_from_rows(SAMPLE_ROWS)
    assert str(result["policy_effective_date"]) == "2025-08-08"
    assert str(result["policy_expiry_date"]) == "2026-08-07"
    assert str(result["report_period_start"]) == "2025-08-08"
    assert str(result["report_period_end"]) == "2026-05-09"
    assert str(result["report_production_date"]) == "2026-05-19"


def test_parses_totals():
    result = _parse_format2_from_rows(SAMPLE_ROWS)
    assert result["total_paid"] == 1099602.0
    assert result["incurred_not_reported"] == 179935.0


def test_sums_the_three_population_categories_per_period():
    result = _parse_format2_from_rows(SAMPLE_ROWS)
    # Male(73) + Single female(34) + Married female(36) = 143
    assert result["opening_members"] == 143
    assert result["closing_members"] == 143


def test_keeps_multiline_wrapped_diagnosis_labels_intact():
    result = _parse_format2_from_rows(SAMPLE_ROWS)
    labels = {d["label"] for d in result["diagnosis_breakdown"]}
    assert "Diverticulitis Of Large Intestine Without Perforation Or Abscess Without Bleeding" in labels
    diverticulitis = next(d for d in result["diagnosis_breakdown"] if "Diverticulitis" in d["label"])
    assert diverticulitis["value"] == 20102.0
    assert diverticulitis["ip_value"] == 20102.0
    assert diverticulitis["count"] == 1


def test_provider_and_network_breakdowns():
    result = _parse_format2_from_rows(SAMPLE_ROWS)
    assert result["provider_breakdown"][0] == {
        "provider": "Alternative Medicine Non-Network Clinic",
        "value": 125925.0,
    }
    assert result["claims_by_type"] == [
        {"type": "In Network", "value": 814028.0},
        {"type": "Out of Network", "value": 245431.0},
    ]


def test_parses_monthly_paid_and_flags_partial_inception_month():
    result = _parse_format2_from_rows(SAMPLE_ROWS)
    monthly = result["monthly_paid"]
    assert len(monthly) == 7  # 16m is blank and skipped
    assert monthly[0]["month"] == "Aug"
    assert monthly[0]["paid"] == 94562.0
    assert monthly[0]["partial"] is True
    assert all(not m["partial"] for m in monthly[1:])


def test_first_full_months_matches_hand_worked_example():
    result = _parse_format2_from_rows(SAMPLE_ROWS)
    months = first_full_months(result["monthly_paid"], 6)
    assert months == [166262.0, 190188.0, 135826.0, 21614.0, 167572.0, 133866.0]
