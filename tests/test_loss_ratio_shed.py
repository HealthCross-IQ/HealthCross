"""What the book's loss ratio becomes if an account is not renewed -
portfolio_analysis's loss_ratio_shed_impact / loss_ratio_shed_cumulative.
"""
import pytest

from app.scoring.rules.portfolio_analysis import (
    account_loss_ratio_totals,
    loss_ratio_shed_cumulative,
    loss_ratio_shed_impact,
)


def _row(name, earned, incurred, members=10, loading=0.0):
    net = earned * (1 - loading)
    return {
        "master_client": name,
        "member_count": members,
        "claim_count": members,
        "paid": incurred,
        "outstanding": 0.0,
        "ibnr": 0.0,
        "incurred_claims": incurred,
        "gross_premium": earned,
        "earned_premium": earned,
        "net_premium": net,
        "loading_pct": loading,
        "gross_loss_ratio": round(incurred / earned, 4) if earned else None,
        "net_loss_ratio": round(incurred / net, 4) if net else None,
    }


def test_shedding_a_worse_than_book_account_improves_the_book():
    rows = [_row("BAD", 100_000, 150_000), _row("GOOD", 100_000, 50_000)]
    assert account_loss_ratio_totals(rows)["net_loss_ratio"] == pytest.approx(1.0)

    impact = loss_ratio_shed_impact(rows)
    bad = next(a for a in impact["accounts"] if a["master_client"] == "BAD")
    assert bad["book_net_loss_ratio_without"] == pytest.approx(0.5)
    assert bad["net_lr_change"] == pytest.approx(-0.5)


def test_shedding_a_better_than_book_account_makes_the_book_worse():
    rows = [_row("BAD", 100_000, 150_000), _row("GOOD", 100_000, 50_000)]
    impact = loss_ratio_shed_impact(rows)
    good = next(a for a in impact["accounts"] if a["master_client"] == "GOOD")
    assert good["book_net_loss_ratio_without"] == pytest.approx(1.5)
    assert good["net_lr_change"] > 0


def test_a_terrible_but_tiny_account_barely_moves_the_book():
    # The whole reason ranking by an account's OWN loss ratio answers the
    # wrong question: 400% on two lives is not the problem to solve.
    rows = [
        _row("TINY_TERRIBLE", 1_000, 4_000, members=2),
        _row("BIG_MEDIOCRE", 1_000_000, 1_150_000, members=900),
    ]
    impact = loss_ratio_shed_impact(rows)
    tiny = next(a for a in impact["accounts"] if a["master_client"] == "TINY_TERRIBLE")
    big = next(a for a in impact["accounts"] if a["master_client"] == "BIG_MEDIOCRE")
    assert tiny["net_loss_ratio"] > big["net_loss_ratio"]        # worse ratio
    assert abs(tiny["net_lr_change"]) < abs(big["net_lr_change"])  # smaller impact


def test_accounts_are_ranked_by_improvement_offered_not_by_their_own_ratio():
    rows = [
        _row("TINY_TERRIBLE", 1_000, 4_000, members=2),
        _row("BIG_BAD", 1_000_000, 1_400_000, members=900),
        _row("GOOD", 500_000, 200_000, members=400),
    ]
    impact = loss_ratio_shed_impact(rows)
    assert impact["accounts"][0]["master_client"] == "BIG_BAD"
    assert impact["accounts"][-1]["master_client"] == "GOOD"


def test_premium_at_risk_is_reported_so_the_trade_off_stays_visible():
    rows = [_row("BAD", 2_000_000, 3_000_000), _row("GOOD", 8_000_000, 4_000_000)]
    impact = loss_ratio_shed_impact(rows)
    bad = next(a for a in impact["accounts"] if a["master_client"] == "BAD")
    assert bad["premium_at_risk"] == 2_000_000
    assert bad["share_of_book_premium"] == pytest.approx(0.2)


def test_single_account_impacts_do_not_simply_add_up():
    # Each standalone figure is measured against the ORIGINAL book, so
    # summing them is not what shedding several actually achieves - and
    # the error does not run reliably in one direction, which is why the
    # cumulative walk exists rather than a correction factor.
    rows = [
        _row("BAD1", 100_000, 200_000),
        _row("BAD2", 100_000, 200_000),
        _row("GOOD", 800_000, 400_000),
    ]
    impact = loss_ratio_shed_impact(rows)
    standalone_sum = sum(
        a["net_lr_change"] for a in impact["accounts"]
        if a["master_client"] in ("BAD1", "BAD2")
    )
    steps = loss_ratio_shed_cumulative(rows, max_accounts=2)
    actual = steps[-1]["book_net_loss_ratio"] - impact["book_net_loss_ratio"]

    assert standalone_sum == pytest.approx(-0.2666, abs=0.001)
    assert actual == pytest.approx(-0.30, abs=0.001)
    assert actual != pytest.approx(standalone_sum, abs=0.01)


def test_cumulative_shedding_walks_worst_first_and_reports_the_price():
    rows = [
        _row("BAD1", 100_000, 300_000),
        _row("BAD2", 100_000, 150_000),
        _row("GOOD", 800_000, 400_000),
    ]
    steps = loss_ratio_shed_cumulative(rows, max_accounts=3)
    assert [s["master_client"] for s in steps][:2] == ["BAD1", "BAD2"]
    assert steps[0]["premium_given_up"] == 100_000
    assert steps[1]["cumulative_premium_given_up"] == 200_000
    # The book improves at every step, by construction.
    assert steps[1]["book_net_loss_ratio"] < steps[0]["book_net_loss_ratio"]


def test_cumulative_stops_once_nothing_left_helps():
    # A book where every remaining account is better than the book average
    # has nothing left to shed - the walk must stop rather than keep
    # dropping profitable accounts.
    rows = [_row("A", 100_000, 50_000), _row("B", 100_000, 50_000)]
    assert loss_ratio_shed_cumulative(rows, max_accounts=5) == []


def test_an_account_with_no_premium_does_not_break_the_calculation():
    rows = [_row("NORMAL", 100_000, 80_000), _row("UNRATED", 0.0, 30_000)]
    impact = loss_ratio_shed_impact(rows)
    unrated = next(a for a in impact["accounts"] if a["master_client"] == "UNRATED")
    # Removing it takes claims out but no premium, so the book improves.
    assert unrated["net_lr_change"] < 0
    assert unrated["premium_at_risk"] == 0.0


def test_shedding_the_only_account_leaves_no_book_to_measure():
    impact = loss_ratio_shed_impact([_row("ONLY", 100_000, 150_000)])
    only = impact["accounts"][0]
    assert only["book_net_loss_ratio_without"] is None
    assert only["net_lr_change"] is None
