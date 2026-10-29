"""
Portfolio limits (migration 032): a BUY that would push one sector or the portfolio drawdown past
the user's limit is rejected live and only flagged in dry run. Sells are never blocked.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from src.analytics.rebalancer import OrderSide, RebalanceOrder
from src.execution import gatekeeper
from src.execution.validators import portfolio_limits_check as plc
from src.execution.validators.portfolio_limits_check import validate_portfolio_limits

PL = "src.execution.validators.portfolio_limits_check"
TODAY = date(2026, 10, 28)


def _order(side=OrderSide.BUY, value="10000"):
    return RebalanceOrder(
        tradingsymbol="INFY",
        exchange="NSE",
        instrument_token=408065,
        side=side,
        quantity=10,
        estimated_price=Decimal(value) / 10,
    )


def _navs(last_dd_pct=0.0, last_date=TODAY, n=30):
    """Equity curve that rises to 120, then ends `last_dd_pct` percent below that peak."""
    days = [last_date - timedelta(days=n - 1 - i) for i in range(n)]
    navs = [100 + 20 * i / (n - 2) for i in range(n - 1)] + [120 * (1 - last_dd_pct / 100)]
    return list(zip(days, navs, strict=True))


def _check(order, live, sector="IT", sector_value=30000, total=100000, navs=None, cfg=None):
    pos = {"sector": sector, "sector_value": sector_value, "total_value": total}
    with (
        patch(f"{PL}._config", return_value=cfg or {"sector": 35.0, "drawdown": 25.0}),
        patch(f"{PL}._position", return_value=pos),
        patch(f"{PL}._nav_history", return_value=_navs() if navs is None else navs),
        patch(f"{PL}._today", return_value=TODAY),
    ):
        return validate_portfolio_limits(order, live=live)


def test_sells_are_never_blocked():
    ok, msg = _check(_order(OrderSide.SELL), live=True, sector_value=90000)
    assert ok and "never blocked" in msg


def test_buy_inside_both_limits_passes_live():
    ok, msg = _check(_order(value="5000"), live=True)  # IT 35000 / 105000 = 33.3%
    assert ok and msg.startswith("Portfolio limits OK")


def test_sector_breach_rejects_live_and_only_warns_in_dry_run():
    live_ok, live_msg = _check(_order(), live=True)
    dry_ok, dry_msg = _check(_order(), live=False)
    assert (
        not live_ok and "Sector breach: IT would be 36.4% of the portfolio (limit 35%)" in live_msg
    )
    assert live_msg.endswith("Buy not sent.")
    assert dry_ok and dry_msg.startswith("WOULD BLOCK LIVE: Sector breach")


def test_sector_exactly_at_the_cap_passes():
    # (30000 + 5000) / (95000 + 5000) = 35.0%
    ok, _ = _check(_order(value="5000"), live=True, total=95000)
    assert ok


def test_unmapped_sector_skips_only_the_sector_check():
    ok, msg = _check(_order(), live=True, sector=None)
    assert ok and "sector unknown" in msg


def test_empty_portfolio_skips_the_sector_check():
    ok, msg = _check(_order(), live=True, sector_value=0, total=0)
    assert ok and "portfolio is empty" in msg


def test_drawdown_breach_rejects_live_and_only_warns_in_dry_run():
    navs = _navs(last_dd_pct=26.0)
    ok, msg = _check(_order(value="5000"), live=True, navs=navs)
    assert not ok and "Drawdown breach: portfolio is 26.0% below its peak (limit 25%)" in msg
    dry_ok, dry_msg = _check(_order(value="5000"), live=False, navs=navs)
    assert dry_ok and dry_msg.startswith("WOULD BLOCK LIVE: Drawdown breach")


def test_drawdown_inside_the_limit_passes():
    ok, msg = _check(_order(value="5000"), live=True, navs=_navs(last_dd_pct=24.0))
    assert ok and "drawdown 24.0%" in msg


@pytest.mark.parametrize(
    "navs",
    [[], _navs(n=30)[:1], _navs(last_date=TODAY - timedelta(days=9))],
    ids=["no snapshots", "one snapshot", "stale snapshot"],
)
def test_missing_or_stale_equity_curve_is_unverified(navs):
    ok, msg = _check(_order(value="5000"), live=True, navs=navs)
    assert not ok and msg.startswith("Portfolio limits can't be confirmed")
    dry_ok, dry_msg = _check(_order(value="5000"), live=False, navs=navs)
    assert dry_ok and dry_msg.startswith("UNVERIFIED:")


def test_a_breach_wins_over_an_unverified_check():
    ok, msg = _check(_order(), live=True, navs=[])
    assert not ok and "Sector breach" in msg


def test_config_defaults_and_bad_values_fall_back():
    with patch(f"{PL}.execute_sql", return_value=[]):
        assert plc._config() == {"sector": 35.0, "drawdown": 25.0}
    rows = [
        {"key": "risk_block_max_sector_pct", "value": "40"},
        {"key": "risk_block_max_drawdown_pct", "value": "oops"},
    ]
    with patch(f"{PL}.execute_sql", return_value=rows):
        assert plc._config() == {"sector": 40.0, "drawdown": 25.0}


# ── Database reads ──


# ── Gatekeeper wiring ──

_PASS = {
    "is_market_open": True,
    "validate_margin": (True, "ok"),
    "validate_sellable": (True, "ok"),
    "validate_price_band": (True, "ok"),
    "validate_slippage": (True, "ok"),
    "validate_concentration": (True, "ok"),
    "validate_no_duplicate": (True, "ok"),
}


def _gate(limits_result, dry_run):
    from contextlib import ExitStack

    with ExitStack() as stack:
        for name, value in _PASS.items():
            if name == "is_market_open":
                stack.enter_context(patch.object(gatekeeper, name, return_value=value))
            else:
                stack.enter_context(patch.object(gatekeeper, name, return_value=value))
        limits = stack.enter_context(
            patch.object(gatekeeper, "validate_portfolio_limits", return_value=limits_result)
        )
        stack.enter_context(patch.object(gatekeeper, "_log_validation"))
        stack.enter_context(patch.object(gatekeeper, "is_dry_run_enabled", return_value=dry_run))
        report = gatekeeper.validate_order(_order(), dry_run=dry_run)
    return report, limits


def test_live_breach_rejects_after_concentration():
    report, limits = _gate(
        (False, "Sector breach: IT would be 36.4% of the portfolio. Buy not sent."), dry_run=False
    )
    limits.assert_called_once()
    assert limits.call_args.kwargs["live"] is True
    assert report.result is gatekeeper.ValidationResult.REJECTED
    assert report.checks_failed == ["PORTFOLIO_LIMITS"]
    assert report.checks_passed[-1] == "CONCENTRATION"


def test_dry_run_warning_passes_and_is_kept_as_a_note():
    note = "WOULD BLOCK LIVE: Sector breach: IT would be 36.4% of the portfolio."
    report, limits = _gate((True, note), dry_run=True)
    assert limits.call_args.kwargs["live"] is False
    assert report.result is gatekeeper.ValidationResult.SKIPPED_DRY_RUN
    assert report.check_notes["PORTFOLIO_LIMITS"] == note
    assert "PORTFOLIO_LIMITS" in report.checks_passed
