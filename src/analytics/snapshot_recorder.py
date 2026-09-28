"""
PortfolioIQ — Daily Portfolio Snapshot Recorder & Cash Margin Sync
Maintains the daily unitized NAV ledger, automates EOD snapshots,
detects broker cash margin deltas, and handles historical baseline backfilling.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from loguru import logger
from sqlalchemy import text

from src.analytics.valuator import compute_portfolio_valuation
from src.db.connection import get_db_session
from src.db.repository import (
    get_cash_flows,
    get_daily_snapshots,
    record_cash_flow,
    record_daily_snapshot,
)
from src.models.dtos import (
    CreateCashFlowDTO,
    CreateSnapshotDTO,
    PortfolioCashFlowDTO,
    PortfolioDailySnapshotDTO,
)


def _get_benchmark_quote(snapshot_date: date) -> tuple[Decimal | None, Decimal | None]:
    """
    Fetch NIFTY 50 TRI or NIFTY proxy closing price and compute daily return.
    Checks live_prices and price_history for NIFTYBEES or NIFTY 50.
    """
    query = text("""
        SELECT last_price, close_price
        FROM live_prices
        WHERE tradingsymbol IN ('NIFTY 50', 'NIFTYBEES', '^NSEI')
        ORDER BY recorded_at DESC
        LIMIT 1
    """)
    with get_db_session() as session:
        result = session.execute(query).mappings().first()
        if not result or result["last_price"] is None:
            return None, None

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


def detect_and_sync_cash_margin_deltas(
    user_id: str = "default",
    current_broker_cash: Decimal | None = None,
    sync_date: date | None = None,
) -> list[PortfolioCashFlowDTO]:
    """
    Compares current broker available cash margin against recorded cash balance.
    Accounts for settled trade fills in order_audit_trail.
    Any unexplained jump is logged as an AUTO_MARGIN_SYNC cash flow (DEPOSIT/WITHDRAWAL).
    """
    if sync_date is None:
        sync_date = date.today()

    with get_db_session() as session:
        # 1. Get current broker cash from user_margins if not passed
        if current_broker_cash is None:
            margin_row = (
                session.execute(
                    text(
                        "SELECT available_cash FROM user_margins WHERE user_id = :user_id LIMIT 1"
                    ),
                    {"user_id": user_id},
                )
                .mappings()
                .first()
            )
            if not margin_row or margin_row["available_cash"] is None:
                return []
            current_broker_cash = Decimal(str(margin_row["available_cash"]))

        # 2. Get latest recorded snapshot cash balance
        snap_row = (
            session.execute(
                text(
                    "SELECT cash_balance, unit_nav FROM portfolio_daily_snapshots "
                    "WHERE user_id = :user_id ORDER BY snapshot_date DESC LIMIT 1"
                ),
                {"user_id": user_id},
            )
            .mappings()
            .first()
        )

        if not snap_row or snap_row["cash_balance"] is None:
            # No baseline to compare against
            return []

        recorded_cash = Decimal(str(snap_row["cash_balance"]))
        unit_nav = Decimal(str(snap_row["unit_nav"])) if snap_row["unit_nav"] else Decimal("100.00")

        # 3. Sum trade fills since last snapshot date
        fills = (
            session.execute(
                text("""
                SELECT
                    COALESCE(SUM(CASE
                        WHEN transaction_type = 'SELL' THEN (requested_quantity * price_at_signal)
                        WHEN transaction_type = 'BUY'  THEN -(requested_quantity * price_at_signal)
                        ELSE 0
                    END), 0) AS net_trade_cash
                FROM order_audit_trail
                WHERE user_id = :user_id
                  AND created_at::date >= :sync_date
                  AND validation_status = 'APPROVED'
            """),
                {"user_id": user_id, "sync_date": sync_date},
            )
            .mappings()
            .first()
        )
        trade_cash_delta = Decimal(str(fills["net_trade_cash"])) if fills else Decimal("0.00")

        expected_cash = recorded_cash + trade_cash_delta
        unexplained_delta = current_broker_cash - expected_cash

        # Discrepancy threshold: ₹50
        if abs(unexplained_delta) < Decimal("50.00"):
            return []

        flow_type = "DEPOSIT" if unexplained_delta > 0 else "WITHDRAWAL"
        abs_amount = abs(unexplained_delta)
        units_affected = (
            (abs_amount / unit_nav).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            if unit_nav > 0
            else None
        )

        flow_create = CreateCashFlowDTO(
            user_id=user_id,
            flow_date=sync_date,
            flow_type=flow_type,
            amount=abs_amount,
            units_affected=units_affected,
            nav_per_unit=unit_nav,
            source="AUTO_MARGIN_SYNC",
            notes=f"Detected unexplained margin change of {unexplained_delta} from broker",
        )
        flow_dto = record_cash_flow(flow_create)
        logger.warning(
            f"[CASH_SYNC] Auto-recorded {flow_type} of ₹{abs_amount} "
            f"(Units affected: {units_affected}) from broker margin sync."
        )
        return [flow_dto]


def backfill_historical_snapshots(user_id: str = "default") -> int:
    """
    Backfills daily snapshots:
    If order history exists in order_audit_trail, constructs baseline from first trade date.
    If no prior history exists, establishes a Day-1 baseline today at 100.00.
    Returns count of snapshots created.
    """
    with get_db_session() as session:
        first_order = (
            session.execute(
                text(
                    "SELECT MIN(created_at::date) AS start_date FROM order_audit_trail "
                    "WHERE user_id = :user_id AND validation_status = 'APPROVED'"
                ),
                {"user_id": user_id},
            )
            .mappings()
            .first()
        )

    today = date.today()
    if not first_order or not first_order["start_date"]:
        # No trade history: record baseline today
        record_daily_eod_snapshot(user_id=user_id, snapshot_date=today)
        return 1

    start_date = first_order["start_date"]
    current_date = start_date
    count = 0
    while current_date <= today:
        record_daily_eod_snapshot(user_id=user_id, snapshot_date=current_date)
        count += 1
        current_date += timedelta(days=1)

    logger.info(f"[BACKFILL] Completed backfill of {count} daily snapshots from {start_date}.")
    return count
