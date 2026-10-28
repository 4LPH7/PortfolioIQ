"""Risk guard alerts (after QuantConnect Lean's risk models): alerts only, never orders."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.analytics.risk_guards import GuardThresholds, evaluate_risk_guards, load_thresholds
from src.analytics.valuator import HoldingValuation, PortfolioValuation

NOW = datetime(2026, 10, 27, 15, 0)


def _holding(sym, avg, price):
    return HoldingValuation(
        instrument_token=hash(sym) % 10_000_000,
        tradingsymbol=sym,
        exchange="NSE",
        isin=None,
        sector=None,
        industry=None,
        quantity=10,
        t1_quantity=0,
        average_price=Decimal(str(avg)),
        current_price=Decimal(str(price)),
        close_price=None,
        price_source="broker",
    )


def _bars(closes, end="2026-10-26"):
    return pd.DataFrame({"Close": closes}, index=pd.bdate_range(end=end, periods=len(closes)))


def _run(holdings, sectors=None, bars=None, th=GuardThresholds()):
    v = PortfolioValuation(holdings=holdings, sector_weights=sectors or {})
    return evaluate_risk_guards(
        v, th, lambda s: (bars or {}).get(s, _bars(np.full(60, 100.0))), NOW
    )


def test_holding_loss_past_the_limit_alerts():
    alerts = _run([_holding("TATAMOTORS", 968.2, 688.45), _holding("INFY", 1512.3, 1528.9)])
    ids = [a["id"] for a in alerts]
    assert "risk_loss_TATAMOTORS" in ids and "risk_loss_INFY" not in ids
    a = next(a for a in alerts if a["id"] == "risk_loss_TATAMOTORS")
    assert a["type"] == "RISK" and "28.9% below cost" in a["title"]


def test_trailing_stop_uses_the_sixty_day_high():
    falling = np.concatenate([np.full(50, 100.0), np.linspace(100, 85, 10)])  # 15% off the high
    alerts = _run([_holding("ITC", 400, 405)], bars={"ITC": _bars(falling)})
    trail = [a for a in alerts if a["id"] == "risk_trail_ITC"]
    assert trail and trail[0]["metadata"]["off_high_pct"] == -15.0


def test_trailing_stop_quiet_inside_the_limit():
    dip = np.concatenate([np.full(55, 100.0), np.full(5, 92.0)])  # 8% off, limit 12%
    assert not [
        a for a in _run([_holding("ITC", 400, 405)], bars={"ITC": _bars(dip)}) if "trail" in a["id"]
    ]


def test_short_history_is_skipped_with_a_stated_reason():
    alerts = _run([_holding("NEWIPO", 100, 101)], bars={"NEWIPO": _bars(np.full(12, 100.0))})
    info = next(a for a in alerts if a["id"] == "risk_trail_skipped")
    assert info["severity"] == "INFO"
    assert "NEWIPO: only 12 days of history (needs 40)" in info["message"]


def test_a_stale_price_suppresses_the_trailing_stop_with_a_reason():
    falling = np.concatenate([np.full(50, 100.0), np.linspace(100, 85, 10)])
    alerts = _run([_holding("HALTED", 100, 101)], bars={"HALTED": _bars(falling, end="2026-09-01")})
    assert not [a for a in alerts if a["id"] == "risk_trail_HALTED"]
    info = next(a for a in alerts if a["id"] == "risk_trail_skipped")
    assert "HALTED: last price is 56 days old" in info["message"]


def test_a_suspended_price_suppresses_the_trailing_stop_with_a_reason():
    frozen = np.concatenate([np.linspace(130, 100, 40), np.full(25, 100.0)])
    alerts = _run([_holding("SUSP", 100, 101)], bars={"SUSP": _bars(frozen)})
    info = next(a for a in alerts if a["id"] == "risk_trail_skipped")
    assert "SUSP: price has not moved in the last 20 days" in info["message"]


def test_sector_limit_ignores_unmapped_holdings():
    alerts = _run([], sectors={"IT": Decimal("41.2"), "Uncategorised": Decimal("90")})
    assert [a["id"] for a in alerts] == ["risk_sector_IT"]


def test_thresholds_come_from_config_with_safe_defaults():
    rows = [
        {"key": "risk_max_holding_loss_pct", "value": "20"},
        {"key": "risk_trailing_stop_pct", "value": "oops"},
    ]
    with patch("src.analytics.risk_guards.execute_sql", return_value=rows):
        th = load_thresholds()
    assert (th.max_holding_loss_pct, th.trailing_stop_pct, th.max_sector_pct) == (20.0, 12.0, 30.0)


def test_settings_are_validated_against_their_declared_type():
    import pytest

    from src.db.repository import _normalise_config_value as norm

    assert norm("btst_enabled", "boolean", " TRUE ") == "true"
    assert norm("edis_mode", "string", "TPIN") == "tpin"
    assert norm("risk_trailing_stop_pct", "float", "12.5") == "12.5"
    for key, vtype, value in (
        ("btst_enabled", "boolean", "yes please"),
        ("price_band_max_age_sec", "integer", "30s"),
        ("risk_max_sector_pct", "float", "-5"),
        ("risk_max_sector_pct", "float", "nan"),
        ("edis_mode", "string", "poa"),
    ):
        with pytest.raises(ValueError):
            norm(key, vtype, value)
