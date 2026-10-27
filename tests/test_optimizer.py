"""Optimiser: methods, limits, data validation and the walk-forward cost accounting."""

import numpy as np
import pandas as pd
import pytest

from src.analytics import optimizer as opt


def _cov(vols, corr):
    vols = np.asarray(vols, dtype=float)
    c = np.full((len(vols), len(vols)), corr)
    np.fill_diagonal(c, 1.0)
    return c * np.outer(vols, vols)


def _gbm(n_days, vols, seed=7, start="2022-01-03"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n_days)
    rets = rng.normal(0.0003, np.asarray(vols) / np.sqrt(252), size=(n_days, len(vols)))
    return pd.DataFrame(100 * np.exp(np.cumsum(rets, axis=0)), index=idx)


def _loader(frame):
    return lambda sym: pd.DataFrame({"Close": frame[sym].dropna()})


# ── Methods ──


@pytest.mark.parametrize("n", [3, 4, 5])
def test_equal_vol_uncorrelated_gives_equal_weights(n):
    cov = _cov([0.2] * n, 0.0)
    for w in (opt.risk_parity(cov), opt.min_variance(cov, cap=1.0), opt.hrp(cov)):
        np.testing.assert_allclose(w, np.full(n, 1 / n), atol=1e-6)


def test_risk_parity_contributions_are_equal():
    cov = _cov([0.1, 0.2, 0.3, 0.4], 0.3)
    w = opt.risk_parity(cov)
    np.testing.assert_allclose(opt.risk_contributions(cov, w), 0.25, atol=1e-4)
    assert w[0] > w[-1]  # the low-vol asset carries the most weight


def test_min_variance_respects_cap_when_reachable():
    cov = _cov([0.05, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3], 0.2)
    w = opt.min_variance(cov, cap=0.2)
    assert w.max() <= 0.2 + 1e-6
    assert w.min() >= 0.0
    assert w.sum() == pytest.approx(1.0)


def test_unknown_method_raises():
    with pytest.raises(ValueError, match="Unknown method"):
        opt.weights_for("kelly", _cov([0.2, 0.2], 0.0), 0.5)


# ── Limits ──


def test_clamps_only_shrink_and_never_redistribute():
    syms = ["A", "B", "C", "D", "E", "F", "G", "H"]
    w = np.array([0.4, 0.2, 0.1, 0.1, 0.05, 0.05, 0.05, 0.05])
    res = opt.apply_limits(syms, w, cap=0.15, cash_floor=0.02)
    scaled = w * 0.98
    assert np.all(res.weights <= scaled + 1e-12)
    assert np.all(res.weights <= 0.15 + 1e-12)
    np.testing.assert_allclose(res.weights[2:], scaled[2:])  # unclamped rows untouched
    assert [c["symbol"] for c in res.clamps] == ["A", "B"]
    assert res.feasibility == "clamped"
    assert res.intentional_cash == 0.02
    assert res.forced_cash == pytest.approx(scaled[0] - 0.15 + scaled[1] - 0.15)


def test_infeasible_full_investment_separates_forced_cash():
    res = opt.apply_limits(list("ABCDE"), np.full(5, 0.2), cap=0.15, cash_floor=0.02)
    assert res.feasibility == "infeasible_full_investment"
    assert res.intentional_cash == 0.02
    assert res.forced_cash == pytest.approx(0.98 - 5 * 0.15)
    assert res.weights.sum() + res.intentional_cash + res.forced_cash == pytest.approx(1.0)


def test_feasible_unclamped_is_ok_with_no_forced_cash():
    res = opt.apply_limits(list("ABCDEFGHIJ"), np.full(10, 0.1), cap=0.15, cash_floor=0.02)
    assert res.feasibility == "ok"
    assert res.clamps == []
    assert res.forced_cash == 0.0


# ── Data validation ──


def test_load_returns_excludes_short_gappy_and_failing_symbols():
    frame = _gbm(300, [0.2, 0.25, 0.3, 0.2])
    frame.columns = ["GOOD", "IPO", "GAPPY", "GOOD2"]
    frame.iloc[:150, 1] = np.nan  # 150 days of history
    frame.iloc[::10, 2] = np.nan  # 10% missing inside the window
    bars = _loader(frame)

    def load(sym):
        if sym == "BROKEN":
            raise TimeoutError("yahoo")
        return bars(sym)

    data = opt.load_returns(["GOOD", "IPO", "GAPPY", "GOOD2", "BROKEN"], load, lookback_bars=252)
    assert list(data.returns.columns) == ["GOOD", "GOOD2"]
    assert "only 150 days" in data.excluded["IPO"]
    assert "missing" in data.excluded["GAPPY"]
    assert "TimeoutError" in data.excluded["BROKEN"]
    assert len(data.returns) == 252
    assert not data.returns.isna().any().any()


# ── Walk-forward cost accounting ──


def test_walk_forward_charges_only_the_initial_build_on_flat_prices():
    idx = pd.bdate_range("2022-01-03", periods=400)
    prices = pd.DataFrame(100.0, index=idx, columns=["A", "B", "C", "D"])
    target = np.full(4, 0.245)
    res = opt.walk_forward_allocation(
        prices, "current_targets", 0.3, 0.02, notional=1_000_000, fixed_weights=target
    )
    expected = opt._trade_cost_fraction(target, 1_000_000, 10.0)
    assert res.costs_pct == pytest.approx(round(expected * 100, 3))
    assert expected > 0.001  # STT alone is 0.1% of what is bought
    trading_days = 399 - 252
    assert res.rebalances == -(-trading_days // 21)
    assert res.turnover_pct_per_year == pytest.approx(
        0.98 / 2 / (trading_days / 252) * 100, abs=0.1
    )
    assert res.max_drawdown_pct == pytest.approx(-expected * 100, abs=0.01)  # costs are a loss


def test_slippage_adds_basis_points_on_traded_value():
    trades = np.array([0.5, -0.3])
    base = opt._trade_cost_fraction(trades, 1_000_000, 0.0)
    with_slip = opt._trade_cost_fraction(trades, 1_000_000, 10.0)
    assert with_slip - base == pytest.approx(0.8 * 10 / 10_000)


def test_walk_forward_reports_short_history():
    prices = _gbm(200, [0.2, 0.2])
    res = opt.walk_forward_allocation(prices, "equal_weight", 0.5, 0.02)
    assert res.cagr_pct is None
    assert "needs" in res.note


# ── Suggestion ──


def _holdings(syms):
    return [
        {"symbol": s, "instrument_token": i + 1, "weight_pct": 10.0} for i, s in enumerate(syms)
    ]


def test_build_suggestion_end_to_end():
    frame = _gbm(560, [0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.2, 0.25])
    syms = [f"S{i}" for i in range(8)]
    frame.columns = syms
    calls = []

    def load(sym):
        calls.append(sym)
        return _loader(frame)(sym)

    out = opt.build_suggestion(
        _holdings(syms),
        "risk_parity",
        load,
        cap_pct=15.0,
        cash_floor_pct=2.0,
        current_targets_pct={s: 12.0 for s in syms},
    )
    assert out["status"] == "ok"
    assert out["feasibility"] in ("ok", "clamped")
    total = sum(w["suggested_pct"] for w in out["weights"])
    assert total + out["intentional_cash_pct"] + out["forced_cash_pct"] == pytest.approx(
        100, abs=0.05
    )
    assert all(w["suggested_pct"] <= 15.0 + 1e-6 for w in out["weights"])
    assert [r["method"] for r in out["walk_forward"]] == [*opt.METHODS, "current_targets"]
    assert all(r["costs_pct"] > 0 for r in out["walk_forward"])
    assert sorted(calls) == sorted(syms)  # each symbol loaded once


def test_build_suggestion_needs_two_usable_holdings():
    frame = _gbm(300, [0.2])
    frame.columns = ["ONLY"]
    out = opt.build_suggestion(_holdings(["ONLY"]), "hrp", _loader(frame), walk_forward=False)
    assert out["status"] == "not_enough_holdings"
    assert out["weights"] == []


def test_build_suggestion_rejects_unknown_method():
    with pytest.raises(ValueError):
        opt.build_suggestion([], "magic", lambda s: None)


def test_excluded_holdings_keep_their_current_weight():
    frame = _gbm(300, [0.2, 0.25, 0.3, 0.2])
    frame.columns = ["A", "B", "C", "NEW"]
    frame.iloc[:200, 3] = np.nan  # 100 days of history
    holdings = _holdings(["A", "B", "C", "NEW"])
    holdings[3]["weight_pct"] = 20.0
    out = opt.build_suggestion(
        holdings, "equal_weight", _loader(frame), cap_pct=40, walk_forward=False
    )
    kept = [w for w in out["weights"] if w["kept"]]
    assert [(w["symbol"], w["suggested_pct"]) for w in kept] == [("NEW", 20.0)]
    optimised = sum(w["suggested_pct"] for w in out["weights"] if not w["kept"])
    assert optimised == pytest.approx(78.0, abs=0.05)  # 100 − 2% cash floor − 20% kept
    assert out["kept_at_current_pct"] == 20.0
    assert out["forced_cash_pct"] == 0.0
