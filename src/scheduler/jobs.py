"""
PortfolioIQ — APScheduler Job Definitions
Manages all background jobs for the system:
    - SOD Sync (9:15 AM): Kite holdings + margins
    - Instrument Refresh (8:30 AM): Kite instruments API
    - EOD Export (3:45 PM): Tableau CSV export
    - Partition Maintenance (midnight): Create next day's partition
    - Token Expiry Warning (5:30 AM): Alert if Kite token expired
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

IST = pytz.timezone("Asia/Kolkata")


def _sod_sync_job():
    """Start-of-day sync: pull holdings + margins from Kite."""
    from src.ingestion.kite_sync import run_start_of_day_sync
    from src.ingestion.market_hours import is_market_day

    if not is_market_day():
        logger.info("[SOD] Skipping — not a market day.")
        return

    try:
        result = run_start_of_day_sync()
        logger.info("[SOD] Sync complete: {}", result)
    except Exception as exc:
        logger.exception("[SOD] Sync failed: {}", exc)


def _instrument_refresh_job():
    """Refresh instrument master from Kite API."""
    from src.ingestion.instrument_mapper import sync_instrument_master_from_kite
    from src.ingestion.market_hours import is_market_day

    if not is_market_day():
        logger.info("[INSTRUMENTS] Skipping — not a market day.")
        return

    try:
        count = sync_instrument_master_from_kite()
        logger.info("[INSTRUMENTS] Synced {} instruments.", count)
    except Exception as exc:
        logger.exception("[INSTRUMENTS] Sync failed: {}", exc)


def _eod_export_job():
    """End-of-day Tableau CSV export."""
    from src.export.tableau_export import run_eod_export
    from src.ingestion.market_hours import is_market_day

    if not is_market_day():
        logger.info("[EOD] Skipping — not a market day.")
        return

    try:
        path = run_eod_export()
        logger.info("[EOD] Export saved to: {}", path)
    except Exception as exc:
        logger.exception("[EOD] Export failed: {}", exc)


def _partition_maintenance_job():
    """Create price_history partition for tomorrow."""
    from sqlalchemy import text

    from src.db.connection import get_db_session

    try:
        with get_db_session() as session:
            session.execute(text("SELECT create_price_partition(CURRENT_DATE + 1)"))
        logger.info("[PARTITION] Created next day's partition.")
    except Exception as exc:
        logger.exception("[PARTITION] Failed: {}", exc)


def _token_expiry_check_job():
    """Check if the Kite access token is still valid."""
    from src.ingestion.kite_auth import get_stored_token

    token = get_stored_token()
    if token is None:
        logger.warning(
            "[TOKEN] Kite access token has expired or is missing! "
            "Run: python -m src.ingestion.kite_auth"
        )
    else:
        logger.debug("[TOKEN] Kite token is valid.")


def _eod_snapshot_job():
    """End-of-day portfolio snapshot: records unitized NAV and benchmark comparison."""
    from src.analytics.snapshot_recorder import record_daily_eod_snapshot
    from src.ingestion.market_hours import is_market_day

    if not is_market_day():
        logger.info("[SNAPSHOT] Skipping — not a market day.")
        return

    try:
        snapshot = record_daily_eod_snapshot()
        logger.info(
            "[SNAPSHOT] EOD Snapshot recorded: NAV={}, UnitNAV={}",
            snapshot.total_nav,
            snapshot.unit_nav,
        )
    except Exception as exc:
        logger.exception("[SNAPSHOT] EOD Snapshot failed: {}", exc)


def record_daily_signals_job():
    """End-of-day quantitative signal snapshot recording for active holdings."""
    from src.analytics.signal_recorder import record_eod_signals
    from src.ingestion.market_hours import is_market_day

    if not is_market_day():
        logger.info("[SIGNALS] Skipping — not a market day.")
        return []

    try:
        snapshots = record_eod_signals()
        logger.info("[SIGNALS] Recorded {} EOD signal snapshots.", len(snapshots))
        return snapshots
    except Exception as exc:
        logger.exception("[SIGNALS] EOD signal recording failed: {}", exc)
        return []


def mature_forward_returns_job():
    """Daily evaluation and maturation of realized forward returns for signals."""
    from src.analytics.signal_recorder import mature_forward_returns
    from src.ingestion.market_hours import is_market_day

    if not is_market_day():
        logger.info("[MATURATION] Skipping — not a market day.")
        return {}

    try:
        matured = mature_forward_returns()
        logger.info("[MATURATION] Forward return maturation complete: {}", matured)
        return matured
    except Exception as exc:
        logger.exception("[MATURATION] Forward return maturation failed: {}", exc)
        return {}


def adjusted_price_backfill_job():
    """Refetch symbols whose cached bars predate adjusted closes (migration 024)."""
    from src.analytics.historical_cache import backfill_adjusted_prices

    try:
        return backfill_adjusted_prices()
    except Exception as exc:
        logger.exception("[BACKFILL] Adjusted-price backfill failed: {}", exc)
        return {}


def research_fundamentals_job():
    """Refresh fundamentals for current holdings, off the request path (research committee)."""
    from src.analytics.valuator import compute_portfolio_valuation
    from src.research.fundamentals import refresh_fundamentals

    try:
        symbols = sorted({h.tradingsymbol for h in compute_portfolio_valuation().holdings})
        return refresh_fundamentals(symbols)
    except Exception as exc:
        logger.exception("[RESEARCH] Fundamentals refresh failed: {}", exc)
        return {}


def price_alerts_job():
    """Check the price alerts the user set (market hours only; information, never orders)."""
    from src.analytics.price_alerts import check_price_alerts

    try:
        return check_price_alerts()
    except Exception as exc:
        logger.exception("[ALERTS] Price alert check failed: {}", exc)
        return {}


def news_refresh_job():
    """Store recent headlines for holdings and the scanner's top 20 (Yahoo Finance RSS, unofficial)."""
    from src.analytics.scanner import top_symbols
    from src.analytics.valuator import compute_portfolio_valuation
    from src.ingestion.news import refresh_news

    try:
        held = {h.tradingsymbol for h in compute_portfolio_valuation().holdings}
        return refresh_news(sorted(held | set(top_symbols(20))))
    except Exception as exc:
        logger.exception("[NEWS] Headline refresh failed: {}", exc)
        return {}


def scanner_job():
    """Rank the NIFTY 500 and refresh its evidence (skipped if a scan is already running)."""
    from src.analytics.scanner import run_scan, start_scan

    try:
        run_id = start_scan("scheduled")
        if run_id is None:
            logger.info("[SCANNER] A scan is already running; skipping.")
            return None
        run_scan(run_id)
        return run_id
    except Exception as exc:
        logger.exception("[SCANNER] Scan failed to start: {}", exc)
        return None


def scanner_fundamentals_job():
    """Weekly fundamentals for the scanner's index members (shown beside the ranking, not scored)."""
    from src.db.connection import execute_sql
    from src.research.fundamentals import refresh_fundamentals

    try:
        rows = execute_sql("SELECT tradingsymbol FROM scanner_universe ORDER BY tradingsymbol")
        return refresh_fundamentals([r["tradingsymbol"] for r in rows])
    except Exception as exc:
        logger.exception("[SCANNER] Fundamentals refresh failed: {}", exc)
        return {}


def research_settlement_job():
    """Settle forward returns and alpha for completed research committee ratings."""
    from src.research.runs import settle_research_runs

    try:
        return settle_research_runs()
    except Exception as exc:
        logger.exception("[RESEARCH] Settlement failed: {}", exc)
        return {}


_daily_signals_job = record_daily_signals_job
_mature_forward_returns_job = mature_forward_returns_job


def create_scheduler() -> BackgroundScheduler:
    """
    Create and configure the APScheduler with all PortfolioIQ jobs.
    Does NOT start the scheduler — caller must call scheduler.start().
    """
    scheduler = BackgroundScheduler(timezone=IST)

    # SOD Sync — 9:15 AM IST on weekdays
    scheduler.add_job(
        _sod_sync_job,
        CronTrigger(hour=9, minute=15, day_of_week="mon-fri", timezone=IST),
        id="sod_sync",
        name="Start of Day Kite Sync",
        replace_existing=True,
    )

    # Instrument Refresh — 8:30 AM IST on weekdays
    scheduler.add_job(
        _instrument_refresh_job,
        CronTrigger(hour=8, minute=30, day_of_week="mon-fri", timezone=IST),
        id="instrument_refresh",
        name="Instrument Master Refresh",
        replace_existing=True,
    )

    # EOD Export — 3:45 PM IST on weekdays
    scheduler.add_job(
        _eod_export_job,
        CronTrigger(hour=15, minute=45, day_of_week="mon-fri", timezone=IST),
        id="eod_export",
        name="End of Day Tableau Export",
        replace_existing=True,
    )

    # EOD Portfolio Snapshot — 4:00 PM IST on weekdays
    scheduler.add_job(
        _eod_snapshot_job,
        CronTrigger(hour=16, minute=0, day_of_week="mon-fri", timezone=IST),
        id="eod_snapshot",
        name="End of Day Portfolio Snapshot",
        replace_existing=True,
    )

    # Adjusted-price backfill — 4:10 PM IST on weekdays (before signals), and once shortly after
    # start so an existing install converts its cache without waiting for the evening run
    scheduler.add_job(
        adjusted_price_backfill_job,
        CronTrigger(hour=16, minute=10, day_of_week="mon-fri", timezone=IST),
        id="adjusted_price_backfill",
        name="Adjusted Price Backfill",
        next_run_time=datetime.now(IST) + timedelta(minutes=2),
        replace_existing=True,
    )

    # Daily EOD Signal Snapshots — 4:15 PM IST on weekdays
    scheduler.add_job(
        _daily_signals_job,
        CronTrigger(hour=16, minute=15, day_of_week="mon-fri", timezone=IST),
        id="daily_signals",
        name="Daily EOD Signal Snapshots",
        replace_existing=True,
    )

    # Mature Forward Returns — 4:30 PM IST on weekdays
    scheduler.add_job(
        _mature_forward_returns_job,
        CronTrigger(hour=16, minute=30, day_of_week="mon-fri", timezone=IST),
        id="mature_forward_returns",
        name="Mature Signal Forward Returns",
        replace_existing=True,
    )

    # Price alerts — every minute; the job itself does nothing outside market hours
    scheduler.add_job(
        price_alerts_job,
        "interval",
        seconds=60,
        id="price_alerts",
        name="Price Alert Check",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Headlines for holdings — every 30 minutes from 7 AM to 8 PM IST, and once soon after start
    scheduler.add_job(
        news_refresh_job,
        CronTrigger(minute="*/30", hour="7-20", timezone=IST),
        id="news_refresh",
        name="Holdings News Refresh",
        next_run_time=datetime.now(IST) + timedelta(minutes=1),
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Market scanner — 6:45 PM IST on weekdays; fundamentals for its 500 stocks on Saturday morning
    scheduler.add_job(
        scanner_job,
        CronTrigger(hour=18, minute=45, day_of_week="mon-fri", timezone=IST),
        id="market_scanner",
        name="Market Scanner (NIFTY 500)",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        scanner_fundamentals_job,
        CronTrigger(hour=8, minute=0, day_of_week="sat", timezone=IST),
        id="scanner_fundamentals",
        name="Scanner Fundamentals Refresh",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Research committee: settle ratings after the bars are in, refresh fundamentals overnight
    scheduler.add_job(
        research_settlement_job,
        CronTrigger(hour=16, minute=40, day_of_week="mon-fri", timezone=IST),
        id="research_settlement",
        name="Research Rating Settlement",
        replace_existing=True,
    )
    scheduler.add_job(
        research_fundamentals_job,
        CronTrigger(hour=19, minute=0, day_of_week="mon-fri", timezone=IST),
        id="research_fundamentals",
        name="Research Fundamentals Refresh",
        replace_existing=True,
    )

    # Partition Maintenance — midnight IST daily
    scheduler.add_job(
        _partition_maintenance_job,
        CronTrigger(hour=0, minute=5, timezone=IST),
        id="partition_maintenance",
        name="Price History Partition Maintenance",
        replace_existing=True,
    )

    # Token Expiry Check — 5:30 AM IST daily
    scheduler.add_job(
        _token_expiry_check_job,
        CronTrigger(hour=5, minute=30, timezone=IST),
        id="token_expiry_check",
        name="Kite Token Expiry Check",
        replace_existing=True,
    )

    logger.info("Scheduler configured with {} jobs.", len(scheduler.get_jobs()))
    return scheduler
