"""
India order guards (migration 025): sellable quantity and live circuit limits.

Live mode rejects whenever the broker data can't be confirmed; dry run passes with an
"UNVERIFIED" note. Deterministic failures (too many shares, a price outside the band) reject in
both modes.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.analytics.rebalancer import OrderSide, RebalanceOrder
from src.execution.validators import price_band_check, sellable_check
from src.execution.validators.price_band_check import validate_price_band
from src.execution.validators.sellable_check import validate_sellable
from src.ingestion.market_hours import now_ist

SC = "src.execution.validators.sellable_check"
PB = "src.execution.validators.price_band_check"


def _order(side=OrderSide.SELL, qty=10, price="100.00", symbol="TCS"):
    return RebalanceOrder(
        tradingsymbol=symbol,
        exchange="NSE",
        instrument_token=2953217,
        side=side,
        quantity=qty,
        estimated_price=Decimal(price),
    )


def _holding(**over):
    h = {
        "quantity": 30,
        "t1_quantity": 0,
        "used_quantity": 0,
        "authorised_quantity": 0,
        "collateral_quantity": 0,
        "last_synced_at": now_ist(),
        "data_source": "KITE",
        "instrument_type": "EQ",
    }
    h.update(over)
    return h


def _sellable(order, live=True, holding=None, cfg=None, pending=0):
    with (
        patch(f"{SC}._holding", return_value=holding if holding is not None else _holding()),
        patch(f"{SC}._config", return_value=cfg or {}),
        patch(f"{SC}._open_sell_quantity", return_value=pending),
    ):
        return validate_sellable(order, live=live)


# ── Sellable quantity ─────────────────────────────────────────


def test_buy_orders_skip_the_sellable_check():
    assert validate_sellable(_order(side=OrderSide.BUY), live=True)[0] is True


def test_sell_within_settled_quantity_passes():
    ok, msg = _sellable(_order(qty=30))
    assert ok and msg.startswith("30 TCS sellable today")


def test_no_holding_rejects():
    with patch(f"{SC}._holding", return_value=None):
        ok, msg = validate_sellable(_order(), live=True)
    assert not ok and "No TCS delivery holding" in msg


def test_t1_shares_are_not_sellable_with_btst_off():
    ok, msg = _sellable(_order(qty=35), holding=_holding(quantity=30, t1_quantity=10))
    assert not ok
    assert (
        "exceeds the 30 sellable" in msg and "10 bought yesterday settle tomorrow (BTST off)" in msg
    )


def test_t1_shares_count_when_btst_is_enabled():
    ok, _ = _sellable(
        _order(qty=35),
        holding=_holding(quantity=30, t1_quantity=10),
        cfg={"btst_enabled": "true"},
    )
    assert ok


@pytest.mark.parametrize(
    "symbol, itype", [("GOLDENTOBC-BZ", "EQ"), ("SMALLCO-BE", "EQ"), ("SMALLCO", "BE")]
)
def test_trade_to_trade_scrips_never_count_t1(symbol, itype):
    ok, msg = _sellable(
        _order(qty=35, symbol=symbol),
        holding=_holding(quantity=30, t1_quantity=10, instrument_type=itype),
        cfg={"btst_enabled": "true"},
    )
    assert not ok and "trade-to-trade scrip" in msg


def test_sold_today_pledged_and_open_orders_are_netted():
    ok, msg = _sellable(
        _order(qty=20),
        holding=_holding(quantity=40, used_quantity=5, collateral_quantity=10),
        pending=8,
    )
    assert not ok
    assert "exceeds the 17 sellable" in msg
    assert "5 sold today" in msg and "10 pledged" in msg and "8 in open sell orders" in msg


def test_tpin_accounts_are_capped_by_todays_authorisation():
    ok, msg = _sellable(
        _order(qty=10),
        holding=_holding(quantity=30, authorised_quantity=6),
        cfg={"edis_mode": "tpin"},
    )
    assert not ok and "exceeds the 6 sellable" in msg


def test_imported_holdings_are_rejected_live_and_flagged_in_dry_run():
    imported = _holding(data_source="CSV_IMPORT")
    ok, msg = _sellable(_order(), live=True, holding=imported)
    assert not ok and "imported file" in msg
    ok, msg = _sellable(_order(), live=False, holding=imported)
    assert ok and msg.startswith("UNVERIFIED:") and "imported" in msg


def test_holdings_not_synced_today_reject_live():
    stale = _holding(last_synced_at=now_ist() - timedelta(days=1))
    ok, msg = _sellable(_order(), live=True, holding=stale)
    assert not ok and "synced from Kite today" in msg


def test_missing_order_book_rejects_live_and_is_noted_in_dry_run():
    ok, msg = _sellable(_order(), live=True, pending=None)
    assert not ok and "order book unavailable" in msg
    ok, msg = _sellable(_order(), live=False, pending=None)
    assert ok and "open sell orders not checked" in msg


def test_open_sell_quantity_reads_only_pending_sells_of_this_scrip():
    kite = MagicMock()
    kite.orders.return_value = [
        {"tradingsymbol": "TCS", "exchange": "NSE", "transaction_type": "SELL", "status": "OPEN", "pending_quantity": 4},
        {"tradingsymbol": "TCS", "exchange": "NSE", "transaction_type": "SELL", "status": "COMPLETE", "pending_quantity": 0},
        {"tradingsymbol": "TCS", "exchange": "NSE", "transaction_type": "BUY", "status": "OPEN", "pending_quantity": 9},
        {"tradingsymbol": "INFY", "exchange": "NSE", "transaction_type": "SELL", "status": "OPEN", "pending_quantity": 7},
        {"tradingsymbol": "TCS", "exchange": "NSE", "transaction_type": "SELL", "status": "TRIGGER PENDING", "pending_quantity": 2},
    ]  # fmt: skip
    with patch(f"{SC}.get_authenticated_kite", return_value=kite):
        assert sellable_check._open_sell_quantity(_order()) == 6


# ── Price band ────────────────────────────────────────────────


def _band(order, live=True, quote=None, max_age=30):
    with (
        patch(f"{PB}._quote", return_value=quote),
        patch(f"{PB}._max_age_sec", return_value=max_age),
    ):
        return validate_price_band(order, live=live)


def _quote(lower=90, upper=110, ltp=100, age_s=2):
    return {
        "lower_circuit_limit": lower,
        "upper_circuit_limit": upper,
        "last_price": ltp,
        "timestamp": (now_ist() - timedelta(seconds=age_s)).replace(tzinfo=None),
    }


def test_inside_band_passes():
    ok, msg = _band(_order(), quote=_quote())
    assert ok and "Inside today's band ₹90–₹110" in msg


def test_no_quote_rejects_live_and_never_falls_back_to_a_fixed_band():
    ok, msg = _band(_order(), live=True, quote=None)
    assert not ok and "can't be confirmed" in msg
    ok, msg = _band(_order(), live=False, quote=None)
    assert ok and msg.startswith("UNVERIFIED:")


def test_quote_without_limits_is_unverified():
    ok, _ = _band(_order(), quote={"last_price": 100, "timestamp": now_ist()})
    assert not ok


def test_stale_quote_rejects_live():
    ok, msg = _band(_order(), quote=_quote(age_s=120), max_age=30)
    assert not ok and "120s old" in msg


def test_estimate_outside_band_rejects_in_both_modes():
    for live in (True, False):
        ok, msg = _band(_order(price="120.00"), live=live, quote=_quote())
        assert not ok and "outside today's band" in msg


def test_sell_locked_at_lower_circuit_rejects():
    ok, msg = _band(_order(price="90.00"), quote=_quote(ltp=90))
    assert not ok and "lower circuit" in msg


def test_buy_locked_at_upper_circuit_rejects():
    ok, msg = _band(_order(side=OrderSide.BUY, price="110.00"), quote=_quote(ltp=110))
    assert not ok and "upper circuit" in msg


def test_quote_is_read_by_exchange_and_symbol():
    kite = MagicMock()
    kite.quote.return_value = {"NSE:TCS": {"last_price": 1}}
    with patch(f"{PB}.get_authenticated_kite", return_value=kite):
        assert price_band_check._quote(_order()) == {"last_price": 1}
    kite.quote.assert_called_once_with(["NSE:TCS"])


# ── Gatekeeper wiring ─────────────────────────────────────────


@patch("src.execution.gatekeeper._log_validation")
@patch("src.execution.gatekeeper.validate_no_duplicate", return_value=(True, "OK"))
@patch("src.execution.gatekeeper.validate_slippage", return_value=(True, "OK"))
@patch("src.execution.gatekeeper.is_market_open", return_value=True)
def test_gatekeeper_keeps_unverified_notes_and_rejects_on_a_guard(*_mocks):
    from src.execution.gatekeeper import ValidationResult, validate_order

    with (
        patch("src.execution.gatekeeper.validate_sellable", return_value=(True, "UNVERIFIED: x")),
        patch("src.execution.gatekeeper.validate_price_band", return_value=(True, "Inside")),
    ):
        report = validate_order(_order(), dry_run=True)
    assert report.result == ValidationResult.SKIPPED_DRY_RUN
    assert report.check_notes["SELLABLE"] == "UNVERIFIED: x"

    with (
        patch("src.execution.gatekeeper.validate_sellable", return_value=(False, "too many")),
        patch("src.execution.gatekeeper.validate_price_band") as band,
    ):
        report = validate_order(_order(), dry_run=False)
    assert report.result == ValidationResult.REJECTED
    assert report.checks_failed == ["SELLABLE"] and report.failure_reason == "too many"
    band.assert_not_called()  # the chain stops at the first failure


def test_quote_failures_return_none():
    with patch(f"{PB}.get_authenticated_kite", return_value=None):
        assert price_band_check._quote(_order()) is None
    with patch(f"{PB}.get_authenticated_kite", side_effect=RuntimeError("token expired")):
        assert price_band_check._quote(_order()) is None


@pytest.mark.parametrize(
    "rows, expected",
    [([{"value": "45"}], 45), ([], 30), ([{"value": "not-a-number"}], 30)],
)
def test_max_quote_age_comes_from_config_with_a_safe_default(rows, expected):
    with patch(f"{PB}.execute_sql", return_value=rows):
        assert price_band_check._max_age_sec() == expected


def test_quote_without_timestamp_is_treated_as_stale():
    q = _quote()
    q["timestamp"] = None
    ok, msg = _band(_order(), quote=q)
    assert not ok and "unknown old" in msg


def test_sellable_helpers_read_config_and_sync_time():
    with patch(f"{SC}.execute_sql", return_value=[{"key": "btst_enabled", "value": "true"}]):
        assert sellable_check._config() == {"btst_enabled": "true"}
    assert sellable_check._synced_today(None) is False
    assert sellable_check._synced_today(now_ist().replace(tzinfo=None)) is True
    with patch(f"{SC}.get_authenticated_kite", side_effect=RuntimeError("down")):
        assert sellable_check._open_sell_quantity(_order()) is None
