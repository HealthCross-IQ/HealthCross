"""The alerts are readings, and a reading that disagrees with the figure
it is drawn from is worse than no reading at all.

So every alert here is built from one row of
portfolio_analysis.account_loss_ratio_rows - the same row the Loss Ratio
screen and the renewal working use - and these tests pin the thresholds,
the ordering, and the one property that matters most: nothing in this
module changes a premium.

The worked example is K A F International Trading, whose 31/08/2026
figures are the reason the module exists: paid 346,212, outstanding
451,519, IBNR 79,895, incurred 877,626 against 353,053 of earned premium
on 130 elapsed days - four separate readings on one account, and the
screen showed all four as plain numbers of equal weight.
"""
import pytest

from app.scoring.rules.experience_pricing import HOUSE_TARGET_LOSS_RATIO
from app.scoring.rules.underwriting_alerts import (
    CREDIBILITY_FLOOR_DAYS,
    CRITICAL_LOSS_RATIO,
    OUTSTANDING_SHARE_HIGH,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_ORDER,
    SEVERITY_WATCH,
    TOP_CLAIMANT_SHARE_CRITICAL,
    TOP_CLAIMANT_SHARE_HIGH,
    alert_counts,
    underwriting_alerts,
)

KAF = {
    "master_client": "K A F INTERNATIONAL TRADING L.L.C",
    "member_count": 123,
    "days": 130,
    "expired": False,
    "paid": 346212.0,
    "outstanding": 451519.0,
    "ibnr": 79895.0,
    "incurred_claims": 877626.0,
    "loading_pct": 0.215,
    "gross_premium": 991265.0,
    "earned_premium": 353053.0,
    "net_premium": 277147.0,
    "gross_loss_ratio": 2.486,
    "net_loss_ratio": 3.167,
}


def healthy_row(**overrides):
    """An account nothing should fire on: on target, settled claims, a
    full year run."""
    row = {
        "member_count": 200,
        "days": 300,
        "expired": False,
        "paid": 600000.0,
        "outstanding": 80000.0,
        "ibnr": 20000.0,
        "incurred_claims": 700000.0,
        "gross_premium": 1200000.0,
        "earned_premium": 986301.0,
        "gross_loss_ratio": 0.71,
        "net_loss_ratio": 0.92,
    }
    row.update(overrides)
    return row


def codes(alerts):
    return [a["code"] for a in alerts]


def by_code(alerts, code):
    return next(a for a in alerts if a["code"] == code)


class TestNothingToSay:
    def test_a_healthy_account_raises_nothing(self):
        assert underwriting_alerts(healthy_row()) == []

    def test_no_row_at_all_is_not_an_error(self):
        assert underwriting_alerts(None) == []

    def test_an_account_with_no_row_still_reports_concentration(self):
        # Claims can be on the book for an account whose policy period
        # has no premium row yet; the concentration reading does not
        # depend on the row.
        alerts = underwriting_alerts(None, top_claimant_share=0.44)
        assert codes(alerts) == ["claim_concentration"]


class TestLossRatio:
    def test_kaf_is_critical(self):
        alert = by_code(underwriting_alerts(KAF), "loss_ratio_critical")
        assert alert["severity"] == SEVERITY_CRITICAL
        assert alert["value"] == pytest.approx(2.486)
        assert "248.6%" in alert["message"]
        assert "95.0%" in alert["message"]

    def test_above_target_but_under_the_critical_line_is_high_not_critical(self):
        row = healthy_row(gross_loss_ratio=1.10)
        alert = by_code(underwriting_alerts(row), "loss_ratio_above_target")
        assert alert["severity"] == SEVERITY_HIGH

    def test_the_two_tiers_say_different_things(self):
        high = by_code(underwriting_alerts(healthy_row(gross_loss_ratio=1.10)),
                       "loss_ratio_above_target")
        critical = by_code(underwriting_alerts(healthy_row(gross_loss_ratio=1.90)),
                           "loss_ratio_critical")
        # Above target: price it harder. Above the critical line: change
        # the risk. If both said "price it harder" the tier would be
        # decoration.
        assert "ladder" in high["action"]
        assert "decline" in critical["action"]

    def test_exactly_on_target_does_not_fire(self):
        assert underwriting_alerts(healthy_row(gross_loss_ratio=HOUSE_TARGET_LOSS_RATIO)) == []

    def test_exactly_on_the_critical_line_is_high_not_critical(self):
        alerts = underwriting_alerts(healthy_row(gross_loss_ratio=CRITICAL_LOSS_RATIO))
        assert codes(alerts) == ["loss_ratio_above_target"]

    def test_a_target_override_moves_the_line(self):
        row = healthy_row(gross_loss_ratio=0.80)
        assert underwriting_alerts(row) == []
        alerts = underwriting_alerts(row, target_loss_ratio=0.75)
        assert codes(alerts) == ["loss_ratio_above_target"]

    def test_no_loss_ratio_is_silence_not_a_zero(self):
        # An account with no earned premium has gross_loss_ratio None.
        # Treating that as 0.0 would report it as healthy.
        assert underwriting_alerts(healthy_row(gross_loss_ratio=None,
                                               earned_premium=0.0)) == []


class TestOutstanding:
    def test_kaf_outstanding_share_is_reported_against_the_book(self):
        alert = by_code(
            underwriting_alerts(KAF, book_median_outstanding_share=0.18),
            "outstanding_exposure",
        )
        assert alert["severity"] == SEVERITY_HIGH
        assert alert["value"] == pytest.approx(451519.0 / 877626.0)
        assert "51.4%" in alert["message"]
        assert "18.0%" in alert["message"]

    def test_the_book_median_is_optional(self):
        alert = by_code(underwriting_alerts(KAF), "outstanding_exposure")
        assert "book median" not in alert["message"]
        assert "51.4%" in alert["message"]

    def test_an_ordinary_share_does_not_fire(self):
        row = healthy_row(outstanding=80000.0, incurred_claims=700000.0)
        assert "outstanding_exposure" not in codes(underwriting_alerts(row))

    def test_exactly_on_the_threshold_does_not_fire(self):
        row = healthy_row(outstanding=350.0, incurred_claims=1000.0,
                          gross_loss_ratio=0.5)
        assert OUTSTANDING_SHARE_HIGH == 0.35
        assert underwriting_alerts(row) == []

    def test_no_incurred_is_silence_not_a_division_by_zero(self):
        assert underwriting_alerts(healthy_row(incurred_claims=0.0,
                                               gross_loss_ratio=None)) == []


class TestConcentration:
    def test_kaf_top_claimant_is_high(self):
        alert = by_code(
            underwriting_alerts(KAF, top_claimant_share=0.259,
                                top_claimant_amount=227305.0),
            "claim_concentration",
        )
        assert alert["severity"] == SEVERITY_HIGH
        assert "25.9%" in alert["message"]
        assert "227,305" in alert["message"]

    def test_a_dominant_claimant_is_critical(self):
        alert = by_code(
            underwriting_alerts(healthy_row(), top_claimant_share=0.51),
            "claim_concentration",
        )
        assert alert["severity"] == SEVERITY_CRITICAL
        assert alert["threshold"] == TOP_CLAIMANT_SHARE_CRITICAL

    def test_under_the_line_does_not_fire(self):
        assert underwriting_alerts(healthy_row(), top_claimant_share=0.12) == []

    def test_exactly_on_the_line_does_not_fire(self):
        assert underwriting_alerts(
            healthy_row(), top_claimant_share=TOP_CLAIMANT_SHARE_HIGH) == []

    def test_the_action_points_at_the_adjustments_step(self):
        alert = by_code(
            underwriting_alerts(healthy_row(), top_claimant_share=0.30),
            "claim_concentration",
        )
        assert "strip it" in alert["action"]


class TestCredibility:
    def test_kaf_is_immature_at_130_days(self):
        alert = by_code(underwriting_alerts(KAF), "experience_immature")
        assert alert["severity"] == SEVERITY_WATCH
        assert "130 of 365" in alert["message"]

    def test_a_full_year_is_credible(self):
        assert "experience_immature" not in codes(
            underwriting_alerts(healthy_row(days=300)))

    def test_an_expired_policy_is_never_immature(self):
        # A policy that ran its full term and expired has all the
        # experience it is ever going to have, however few days the
        # as-of date happens to sit past its start.
        row = healthy_row(days=40, expired=True)
        assert "experience_immature" not in codes(underwriting_alerts(row))

    def test_exactly_on_the_floor_is_credible(self):
        row = healthy_row(days=CREDIBILITY_FLOOR_DAYS)
        assert "experience_immature" not in codes(underwriting_alerts(row))


class TestLoadingNotEntered:
    def test_an_unentered_loading_is_critical(self):
        problems = [{"field": "loading_pct", "value": None,
                     "message": "The renewal loading is not set on this case: "
                                "the TPA fee has no value."}]
        alerts = underwriting_alerts(healthy_row(), loading_problems=problems)
        alert = by_code(alerts, "loading_not_entered")
        assert alert["severity"] == SEVERITY_CRITICAL
        assert "TPA fee" in alert["message"]

    def test_it_says_that_zero_is_an_answer(self):
        problems = [{"message": "unset"}]
        alert = by_code(
            underwriting_alerts(healthy_row(), loading_problems=problems),
            "loading_not_entered",
        )
        assert "Enter 0" in alert["action"]

    def test_no_problems_is_no_alert(self):
        assert underwriting_alerts(healthy_row(), loading_problems=[]) == []


class TestOrderingAndShape:
    def test_kaf_raises_all_four_readings_worst_first(self):
        alerts = underwriting_alerts(
            KAF,
            top_claimant_share=0.259,
            top_claimant_amount=227305.0,
            book_median_outstanding_share=0.18,
        )
        assert codes(alerts) == [
            "loss_ratio_critical",
            "outstanding_exposure",
            "claim_concentration",
            "experience_immature",
        ]

    def test_severities_are_non_decreasing(self):
        alerts = underwriting_alerts(
            KAF, top_claimant_share=0.259,
            loading_problems=[{"message": "unset"}],
        )
        ranks = [SEVERITY_ORDER[a["severity"]] for a in alerts]
        assert ranks == sorted(ranks)

    def test_every_alert_carries_all_four_parts(self):
        alerts = underwriting_alerts(
            KAF, top_claimant_share=0.259,
            loading_problems=[{"message": "unset"}],
        )
        assert alerts
        for alert in alerts:
            assert alert["code"]
            assert alert["severity"] in SEVERITY_ORDER
            assert alert["title"]
            assert alert["message"]
            # The rule so it can be argued with, the action so it can be
            # acted on. An alert missing either is just a number again.
            assert alert["rule"]
            assert alert["action"]

    def test_codes_are_unique_within_one_account(self):
        alerts = underwriting_alerts(KAF, top_claimant_share=0.259)
        assert len(codes(alerts)) == len(set(codes(alerts)))

    def test_the_input_row_is_never_mutated(self):
        # The row is the same dict the Loss Ratio screen renders. An
        # alert that wrote to it would change a reported figure.
        row = dict(KAF)
        underwriting_alerts(row, top_claimant_share=0.259)
        assert row == KAF


class TestCounts:
    def test_all_three_severities_are_always_keyed(self):
        assert alert_counts([]) == {SEVERITY_CRITICAL: 0, SEVERITY_HIGH: 0,
                                    SEVERITY_WATCH: 0}

    def test_kaf_tallies(self):
        alerts = underwriting_alerts(KAF, top_claimant_share=0.259)
        assert alert_counts(alerts) == {SEVERITY_CRITICAL: 1, SEVERITY_HIGH: 2,
                                        SEVERITY_WATCH: 1}
