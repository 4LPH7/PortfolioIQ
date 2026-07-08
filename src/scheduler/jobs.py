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

from datetime import datetime

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
    from src.db.connection import get_db_session
    from sqlalchemy import text

    try:
        with get_db_session() as session:
            session.execute(text("SELECT create_next_partition()"))
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
