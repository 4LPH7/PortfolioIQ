"""
PortfolioIQ — Trading Safety Certification & Startup Health Check (Phase 8)
Enforces fail-closed safety gates before live order placement or service boot.

Live trading graduation requirements:
1. DRY_RUN_MODE=false requires explicit LIVE_TRADING_CONFIRMATION token.
2. Database schema must match all known migration versions on disk.
3. Market data price feeds must not be stale beyond threshold.
4. Broker credentials and authentication must be valid.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from src.config.settings import get_settings, is_dry_run_enabled


class StartupSafetyCheckError(RuntimeError):
    """Raised when a pre-flight safety condition fails."""

    pass


LIVE_CONFIRMATION_TOKEN = "I_UNDERSTAND_REAL_MONEY_IS_AT_RISK"


def check_live_mode_authorization() -> tuple[bool, str]:
    """Verify that live mode has explicit, logged human confirmation."""
    if is_dry_run_enabled():
        return True, "Dry-run mode active — safe simulated execution only."

    token = os.environ.get("LIVE_TRADING_CONFIRMATION", "").strip()
    if token != LIVE_CONFIRMATION_TOKEN:
        msg = (
            f"LIVE MODE REFUSED: DRY_RUN_MODE is false but LIVE_TRADING_CONFIRMATION "
            f"does not match expected token ('{LIVE_CONFIRMATION_TOKEN}')."
        )
        logger.critical(msg)
        return False, msg

    logger.warning("LIVE TRADING MODE AUTHORIZED. Real broker orders will be placed!")
    return True, "Live trading explicitly authorized."


def check_schema_integrity() -> tuple[bool, str]:
    """Verify that database schema matches all migrations on disk."""
    try:
        from db.run_migrations import (
            calculate_migration_checksum,
            get_applied_migrations,
            get_connection,
            get_migration_files,
        )

        files = get_migration_files()
        conn = get_connection(max_retries=2, retry_interval=1.0)
        try:
            applied = get_applied_migrations(conn)
        finally:
            conn.close()

        unapplied = [f.name for f in files if f.name not in applied]
        if unapplied:
            msg = f"Database schema mismatch: {len(unapplied)} unapplied migrations ({unapplied})"
            logger.error(msg)
            return False, msg

        # Verify checksums of applied migrations
        tampered = []
        for f in files:
            expected = applied.get(f.name)
            if expected:
                actual = calculate_migration_checksum(f)
                if expected != actual:
                    tampered.append(f.name)

        if tampered:
            msg = f"Database checksum mismatch in migrations: {tampered}"
            logger.error(msg)
            return False, msg

        return True, f"Schema verified: all {len(files)} migrations applied and intact."
    except Exception as exc:
        msg = f"Schema check failed to connect or execute: {exc}"
        logger.warning(msg)
        # In non-DB development/test environments, allow dry-run fallback
        if is_dry_run_enabled():
            return True, f"Schema check skipped in dry-run mode ({exc})"
        return False, msg


def check_price_feed_freshness(max_age_seconds: int = 120) -> tuple[bool, str]:
    """Verify that live prices in the database are not stale if live trading is enabled."""
    if is_dry_run_enabled():
        return True, "Price freshness check skipped in dry-run mode."

    try:
        import psycopg2

        dsn = get_settings().database_url
        conn = psycopg2.connect(dsn, connect_timeout=3)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*), MAX(last_updated) FROM live_prices WHERE is_stale = FALSE;"
            )
            count, latest = cur.fetchone()
            cur.close()

            if count == 0 or latest is None:
                return False, "Price feed empty: zero active prices in live_prices."

            now = datetime.now(UTC)
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=UTC)
            age = (now - latest).total_seconds()

            if age > max_age_seconds:
                return (
                    False,
                    f"Price feed stale: latest quote is {age:.1f}s old (limit {max_age_seconds}s).",
                )

            return True, f"Price feed fresh: {count} active quotes, newest {age:.1f}s old."
        finally:
            conn.close()
    except Exception as exc:
        return False, f"Price feed check failed: {exc}"


def check_broker_authentication() -> tuple[bool, str]:
    """Verify that Kite broker credentials are present."""
    settings = get_settings()
    if not settings.kite_api_key or settings.kite_api_key.startswith("test_"):
        if not is_dry_run_enabled():
            return False, "Live broker execution requires a non-mock KITE_API_KEY."
    if not settings.kite_api_secret or settings.kite_api_secret.startswith("test_"):
        if not is_dry_run_enabled():
            return False, "Live broker execution requires a non-mock KITE_API_SECRET."

    return True, "Broker configuration present."


def verify_startup_safety() -> dict[str, Any]:
    """
    Run all Phase 8 trading safety certification checks.
    Raises StartupSafetyCheckError if any gate fails in live mode.
    """
    checks = {
        "live_mode_auth": check_live_mode_authorization(),
        "schema_integrity": check_schema_integrity(),
        "price_feed": check_price_feed_freshness(),
        "broker_auth": check_broker_authentication(),
    }

    all_passed = all(status for status, _ in checks.values())

    report = {
        "all_passed": all_passed,
        "is_dry_run": is_dry_run_enabled(),
        "details": {k: {"passed": passed, "message": msg} for k, (passed, msg) in checks.items()},
    }

    if not is_dry_run_enabled() and not all_passed:
        failed = [f"{k}: {msg}" for k, (passed, msg) in checks.items() if not passed]
        raise StartupSafetyCheckError(
            f"Startup safety gate failed live mode boot: {'; '.join(failed)}"
        )

    return report
