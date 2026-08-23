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


def test_an_account_with_claims_and_no_premium_is_a_data_gap_not_a_candidate():
    # Removing it costs nothing and takes claims out, so pure arithmetic
    # ranks it as the best decision on the book. It is almost always a
    # missing premium column or an unmapped subgroup - ranking it beside
    # real candidates points the underwriter at the wrong accounts.
    rows = [_row("NORMAL", 100_000, 80_000), _row("UNRATED", 0.0, 30_000)]
    impact = loss_ratio_shed_impact(rows)

    assert [a["master_client"] for a in impact["accounts"]] == ["NORMAL"]
    assert [a["master_client"] for a in impact["unpriced_accounts"]] == ["UNRATED"]
    assert impact["unpriced_incurred"] == 30_000


def test_an_accounts_policy_periods_are_shed_together_not_one_at_a_time():
    # On the underwriting basis a renewed account is two rows. You do not
    # lose one policy year of a client - you lose the client, so the
    # impact must be measured on both rows together.
    rows = [
        _row("MULTI", 100_000, 150_000) | {"master_client": "MULTI"},
        _row("MULTI", 100_000, 150_000) | {"master_client": "MULTI"},
        _row("GOOD", 800_000, 400_000),
    ]
    impact = loss_ratio_shed_impact(rows)
    multi = next(a for a in impact["accounts"] if a["master_client"] == "MULTI")
    assert multi["period_count"] == 2
    assert multi["incurred_claims"] == 300_000
    assert multi["earned_premium"] == 200_000
    # Book without it is the GOOD row alone: 400k / 800k.
    assert multi["book_net_loss_ratio_without"] == pytest.approx(0.5)


def test_the_cumulative_walk_never_makes_the_book_worse():
    # It once did: the impact measured removing ONE row while the walk
    # removed every row for that client, so a multi-period account was
    # picked on an understated figure and the book went backwards.
    rows = [
        _row("MULTI", 100_000, 90_000) | {"master_client": "MULTI"},
        _row("MULTI", 100_000, 90_000) | {"master_client": "MULTI"},
        _row("BAD", 200_000, 400_000),
        _row("GOOD", 700_000, 350_000),
    ]
    steps = loss_ratio_shed_cumulative(rows, max_accounts=4)
    ratios = [s["book_net_loss_ratio"] for s in steps]
    assert ratios == sorted(ratios, reverse=True), ratios


def test_repricing_shows_the_increase_that_makes_an_account_stand_alone():
    rows = [_row("BAD", 100_000, 150_000), _row("GOOD", 900_000, 450_000)]
    impact = loss_ratio_shed_impact(rows, target_net_loss_ratio=1.0)
    bad = next(a for a in impact["accounts"] if a["master_client"] == "BAD")
    # 150k of claims at a 100% target needs 150k of net premium, and with
    # no loading in this fixture that is 150k of earned premium - a 50%
    # increase on the 100k it currently charges.
    assert bad["required_earned_premium"] == pytest.approx(150_000)
    assert bad["required_increase_pct"] == pytest.approx(50.0)


def test_repricing_keeps_the_premium_where_shedding_gives_it_up():
    rows = [_row("BAD", 100_000, 150_000), _row("GOOD", 900_000, 450_000)]
    impact = loss_ratio_shed_impact(rows)
    bad = next(a for a in impact["accounts"] if a["master_client"] == "BAD")
    # Both fix the book; only one keeps the account on it.
    assert bad["book_net_loss_ratio_without"] == pytest.approx(0.5)
    assert bad["book_net_loss_ratio_repriced"] == pytest.approx(0.5714, abs=0.001)
    assert bad["premium_at_risk"] == 100_000


def test_a_target_below_one_asks_for_a_bigger_increase():
    rows = [_row("BAD", 100_000, 150_000), _row("GOOD", 900_000, 450_000)]
    at_break_even = loss_ratio_shed_impact(rows, target_net_loss_ratio=1.0)["accounts"]
    at_margin = loss_ratio_shed_impact(rows, target_net_loss_ratio=0.85)["accounts"]
    a = next(x for x in at_break_even if x["master_client"] == "BAD")
    b = next(x for x in at_margin if x["master_client"] == "BAD")
    assert b["required_increase_pct"] > a["required_increase_pct"]


def test_repricing_accounts_for_the_accounts_own_loading():
    # An account keeping only 67% of premium after expenses needs a bigger
    # gross increase than one keeping 100%, for the same claims.
    rows = [_row("LOADED", 100_000, 100_000, loading=0.33), _row("GOOD", 900_000, 400_000)]
    impact = loss_ratio_shed_impact(rows)
    loaded = next(a for a in impact["accounts"] if a["master_client"] == "LOADED")
    assert loaded["required_net_premium"] == pytest.approx(100_000)
    assert loaded["required_earned_premium"] == pytest.approx(149_253, abs=5)


def test_shedding_the_only_account_leaves_no_book_to_measure():
    impact = loss_ratio_shed_impact([_row("ONLY", 100_000, 150_000)])
    only = impact["accounts"][0]
    assert only["book_net_loss_ratio_without"] is None
    assert only["net_lr_change"] is None
