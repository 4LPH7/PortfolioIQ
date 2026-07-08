"""
PortfolioIQ — Application Settings
Loads from .env file via Pydantic Settings.
All configuration is strongly typed and validated at startup.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

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
        "http://127.0.0.1:5000/callback",
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
    dry_run_mode: bool = Field(
        True,
        description=(
            "CRITICAL: When True, orders are simulated but never sent to broker. "
            "Must remain True for at least 5 full market sessions."
        ),
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field("INFO")

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
    polling_interval_sec: int = Field(50, ge=10, le=300)

    # ------------------------------------------------------------------ #
    # Gatekeeper limits
    # ------------------------------------------------------------------ #
    slippage_bound_pct: float = Field(
        2.0, gt=0, le=10,
        description="Max price drift % since recommendation before blocking order",
    )
    concentration_limit_pct: float = Field(
        15.0, gt=0, le=100,
        description="Max single-stock portfolio weight % before blocking order",
    )
    duplicate_window_sec: int = Field(
        300, ge=60,
        description="Seconds to look back for duplicate orders",
    )
    max_rebalance_orders: int = Field(10, ge=1, le=50)

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
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
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
