"""
PortfolioIQ — Application Settings
Loads from .env file via Pydantic Settings.
All configuration is strongly typed and validated at startup.
"""

from __future__ import annotations

import os
from decimal import Decimal
from functools import lru_cache
from typing import Literal

from loguru import logger
from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All runtime configuration for PortfolioIQ.
    Values are read from environment variables / .env file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Zerodha Kite Connect
    # ------------------------------------------------------------------ #
    kite_api_key: str = Field(..., description="Kite Connect API key")
    kite_api_secret: str = Field(..., description="Kite Connect API secret")
    kite_client_id: str = Field("WJU490", description="Zerodha client ID")
    kite_redirect_url: str = Field(
        "http://127.0.0.1:5000/api/v1/broker/callback",
        description="OAuth redirect URL (must match Kite app settings)",
    )

    # ------------------------------------------------------------------ #
    # Database
    # ------------------------------------------------------------------ #
    database_url: str = Field(
        ...,
        description="Full PostgreSQL DSN, e.g. postgresql://user:pass@host:5432/db",
    )
    db_pool_size: int = Field(5, description="SQLAlchemy connection pool size")
    db_pool_max_overflow: int = Field(10, description="Max overflow connections")

    # ------------------------------------------------------------------ #
    # Application
    # ------------------------------------------------------------------ #
    app_env: Literal["development", "production"] = Field("development")
    frontend_origin: str = Field(
        "http://localhost:8080",
        description="Public origin of the frontend used after broker sign-in",
    )
    dry_run_mode: bool = Field(
        True,
        description=(
            "CRITICAL: When True, orders are simulated but never sent to broker. "
            "Must remain True for at least 5 full market sessions."
        ),
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field("INFO")

    # ------------------------------------------------------------------ #
    # API Security & Rate Limiting
    # ------------------------------------------------------------------ #
    portfolioiq_api_key: str = Field(
        ..., description="Private API key required in X-API-Key header"
    )
    rate_limit_mutations: str = Field(
        "10 per minute",
        description="Rate limit on mutation endpoints (sync, rebalance, settings)",
    )
    rate_limit_default: str = Field(
        "120 per minute",
        description="Default rate limit across API endpoints",
    )

    # ------------------------------------------------------------------ #
    # Market timing
    # ------------------------------------------------------------------ #
    timezone: str = Field("Asia/Kolkata")
    market_open_time: str = Field("09:15", description="NSE open time HH:MM IST")
    market_close_time: str = Field("15:30", description="NSE close time HH:MM IST")
    eod_export_time: str = Field("15:45", description="EOD export trigger HH:MM IST")

    # ------------------------------------------------------------------ #
    # Polling
    # ------------------------------------------------------------------ #
    polling_interval_sec: int = Field(15, ge=10, le=300)

    # ------------------------------------------------------------------ #
    # Gatekeeper limits
    # ------------------------------------------------------------------ #
    slippage_bound_pct: float = Field(
        2.0,
        gt=0,
        le=10,
        description="Max price drift % since recommendation before blocking order",
    )
    concentration_limit_pct: float = Field(
        15.0,
        gt=0,
        le=100,
        description="Max single-stock portfolio weight % before blocking order",
    )
    duplicate_window_sec: int = Field(
        300,
        ge=60,
        description="Seconds to look back for duplicate orders",
    )
    max_rebalance_orders: int = Field(10, ge=1, le=50)
    rebalancer_min_trade_value: Decimal = Field(
        Decimal("2000.00"),
        ge=Decimal("0.00"),
        description="Minimum trade value in INR below which rebalance orders are suppressed (unless liquidating)",
    )
    rebalancer_cash_buffer_pct: Decimal = Field(
        Decimal("0.02"),
        ge=Decimal("0.00"),
        le=Decimal("0.50"),
        description="Target minimum cash reserve buffer as percentage of AUM (e.g. 0.02 = 2%)",
    )
    rebalancer_cash_buffer_floor: Decimal = Field(
        Decimal("5000.00"),
        ge=Decimal("0.00"),
        description="Absolute floor for cash reserve buffer in INR",
    )
    rebalancer_turnover_cap_pct: Decimal = Field(
        Decimal("0.15"),
        ge=Decimal("0.01"),
        le=Decimal("1.00"),
        description="Maximum daily portfolio turnover permitted as percentage of AUM (e.g. 0.15 = 15%)",
    )
    rebalancer_adv_limit_pct: Decimal = Field(
        Decimal("0.01"),
        ge=Decimal("0.001"),
        le=Decimal("0.10"),
        description="Maximum single-order size as fraction of 20-day Average Daily Volume (e.g. 0.01 = 1%)",
    )

    # ------------------------------------------------------------------ #
    # Exports
    # ------------------------------------------------------------------ #
    tableau_output_dir: str = Field("./exports/tableau")

    # ------------------------------------------------------------------ #
    # Validators
    # ------------------------------------------------------------------ #
    @field_validator("market_open_time", "market_close_time", "eod_export_time")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        """Ensure time strings are in HH:MM format."""
        parts = v.split(":")
        if len(parts) != 2 or not all(p.isdigit() and len(p) == 2 for p in parts):
            raise ValueError(f"Time must be in HH:MM format, got: {v!r}")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"Invalid time value: {v!r}")
        return v

    @computed_field  # type: ignore[misc]
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @computed_field  # type: ignore[misc]
    @property
    def is_dry_run(self) -> bool:
        """Convenience alias — always check this before placing orders."""
        return self.dry_run_mode


def is_dry_run_enabled() -> bool:
    """Read the execution kill switch directly from the deployment environment.

    Missing or malformed values fail closed. The database setting is deliberately
    not consulted here: a deployment-level dry-run flag must not be overridden by
    mutable application data.
    """
    raw_value = os.environ.get("DRY_RUN_MODE")
    if raw_value is None:
        logger.error("DRY_RUN_MODE is missing; forcing dry-run mode")
        return True

    normalized = raw_value.strip().lower()
    if normalized not in {"true", "false"}:
        logger.error("DRY_RUN_MODE is invalid; forcing dry-run mode")
        return True

    return normalized == "true"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return cached Settings instance.
    Called once at startup; all modules import this function.

    Usage:
        from src.config.settings import get_settings
        settings = get_settings()
    """
    return Settings()
