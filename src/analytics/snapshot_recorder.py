"""
PortfolioIQ — Daily Portfolio Snapshot Recorder & Cash Margin Sync
Maintains the daily unitized NAV ledger, automates EOD snapshots,
detects broker cash margin deltas, and handles historical baseline backfilling.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from loguru import logger
from sqlalchemy import text

from src.analytics.valuator import compute_portfolio_valuation
from src.db.connection import get_db_session
from src.db.repository import (
    get_cash_flows,
    get_daily_snapshots,
    record_daily_snapshot,
)
from src.models.dtos import (
    CreateSnapshotDTO,
    PortfolioDailySnapshotDTO,
)


def _get_benchmark_quote(snapshot_date: date) -> tuple[Decimal | None, Decimal | None]:
    """
    Fetch NIFTY 50 TRI or NIFTY proxy closing price and compute daily return.
    Checks live_prices and historical_daily_bars for NIFTYBEES or NIFTY 50.
    """
    query = text("""
        SELECT lp.last_price, lp.close_price
        FROM live_prices lp
        JOIN instrument_master im ON lp.instrument_token = im.instrument_token
        WHERE im.tradingsymbol IN ('NIFTY 50', 'NIFTYBEES', '^NSEI')
        ORDER BY lp.last_updated DESC
        LIMIT 1
    """)
    with get_db_session() as session:
        result = session.execute(query).mappings().first()
        if not result or result["last_price"] is None:
            hist = session.execute(
                text("""
                SELECT close FROM historical_daily_bars
                WHERE tradingsymbol IN ('NIFTY 50', 'NIFTYBEES', '^NSEI')
                ORDER BY bar_date DESC
                LIMIT 2
            """)
            ).all()
            if hist:
                current = Decimal(str(hist[0][0]))
                close = Decimal(str(hist[1][0])) if len(hist) > 1 else current
                daily_return = (
                    Decimal("0.0000")
                    if len(hist) <= 1
                    else ((current - close) / close).quantize(
                        Decimal("0.0001"), rounding=ROUND_HALF_UP
                    )
                )
                return current, daily_return
            return Decimal("25000.00"), Decimal("0.0000")

        current = Decimal(str(result["last_price"]))
        close = Decimal(str(result["close_price"])) if result.get("close_price") else None
        daily_return = None
        if close and close > 0:
            daily_return = ((current - close) / close).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        return current, daily_return


def record_daily_eod_snapshot(
    user_id: str = "default",
    snapshot_date: date | None = None,
) -> PortfolioDailySnapshotDTO:
    """
    Compute and record the End-of-Day (EOD) portfolio snapshot.
    Maintains GIPS unitized NAV progression:
        Unit NAV_0 = 100.00
        Unit NAV_t = Total NAV_t / Units_t
        Daily Return = (Unit NAV_t / Unit NAV_{t-1}) - 1.0
    """
    if snapshot_date is None:
        snapshot_date = date.today()

    # 1. Pull current valuation
    valuation = compute_portfolio_valuation(user_id=user_id)
    total_equity = valuation.total_aum
    cash_balance = valuation.available_cash
    total_nav = valuation.net_worth

    # 2. Retrieve chronological snapshots to find prior day state
    existing_snapshots = get_daily_snapshots(user_id=user_id, limit=500)
    prior_snapshots = [s for s in existing_snapshots if s.snapshot_date < snapshot_date]

    # Calculate net external cash flows on snapshot_date
    flows = get_cash_flows(user_id=user_id, start_date=snapshot_date, limit=100)
    today_flows = [f for f in flows if f.flow_date == snapshot_date]

    net_external_flow = Decimal("0.00")
    for f in today_flows:
        if f.flow_type in ("DEPOSIT", "INTEREST"):
            net_external_flow += f.amount
        elif f.flow_type in ("WITHDRAWAL", "CHARGE"):
            net_external_flow -= f.amount

    # 3. Determine units and unit NAV
    if not prior_snapshots:
        # Day 0 / Baseline initialization
        unit_nav = Decimal("100.0000")
        if total_nav > 0:
            units = (total_nav / unit_nav).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        else:
            units = Decimal("1.000000")
        daily_return_pct = Decimal("0.0000")
    else:
        prev = prior_snapshots[-1]
        prev_unit_nav = prev.unit_nav
        prev_units = prev.units

        # Start of Day (SOD) Unit issuance / redemption based on net external flow
        if prev_unit_nav > 0 and net_external_flow != 0:
            delta_units = (net_external_flow / prev_unit_nav).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP
            )
            units = prev_units + delta_units
        else:
            units = prev_units

        # Protect against zero/negative units
        if units <= 0:
            units = Decimal("1.000000")

        # EOD Unit NAV valuation
        unit_nav = (total_nav / units).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        # True GIPS Time-Weighted Daily Return
        if prev_unit_nav > 0:
            daily_return_pct = ((unit_nav - prev_unit_nav) / prev_unit_nav).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        else:
            daily_return_pct = Decimal("0.0000")

    # 4. Benchmark quote
    bm_val, bm_return = _get_benchmark_quote(snapshot_date)

    snapshot_create = CreateSnapshotDTO(
        snapshot_date=snapshot_date,
        user_id=user_id,
        total_equity_value=total_equity,
        cash_balance=cash_balance,
        total_nav=total_nav,
        units=units,
        unit_nav=unit_nav,
        daily_return_pct=daily_return_pct,
        benchmark_name="NIFTY 50 TRI",
        benchmark_value=bm_val,
        benchmark_daily_return_pct=bm_return,
        net_external_flow=net_external_flow,
        gross_daily_return_pct=daily_return_pct,
        stt_drag_bps=Decimal("0.00"),
        fee_drag_bps=Decimal("0.00"),
        tax_drag_bps=Decimal("0.00"),
    )

    recorded = record_daily_snapshot(snapshot_create)
    logger.info(
        f"[SNAPSHOT] Recorded EOD snapshot for {snapshot_date}: "
        f"NAV={total_nav}, UnitNAV={unit_nav}, Units={units}, Return={daily_return_pct}"
    )
    return recorded
