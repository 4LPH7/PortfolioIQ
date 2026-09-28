"""
Tests for src/config/settings.py
Verifies time format validation, fail-closed dry-run kill switch, and Settings defaults.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.config.settings import Settings, get_settings, is_dry_run_enabled


class TestTimeFormatValidation:
    """Tests for Settings.validate_time_format validator."""

    @pytest.mark.parametrize("valid_time", ["00:00", "09:15", "15:30", "23:59", "12:00"])
    def test_valid_time_formats(self, valid_time: str) -> None:
        assert Settings.validate_time_format(valid_time) == valid_time

    @pytest.mark.parametrize(
        "invalid_time",
        [
            "9:15",  # single digit hour
            "0915",  # missing colon
            "09:15:00",  # seconds included
            "24:00",  # hour 24 invalid
            "25:00",  # hour > 24
            "-1:30",  # negative hour
            "12:60",  # minute 60 invalid
            "12:99",  # minute > 59
            "ab:cd",  # letters
            "12:xx",  # mixed
            "",  # empty
            "invalid",  # arbitrary string
        ],
    )
    def test_invalid_time_formats(self, invalid_time: str) -> None:
        with pytest.raises(ValueError):
            Settings.validate_time_format(invalid_time)


class TestDryRunFailClosed:
    """Tests for fail-closed behavior of is_dry_run_enabled()."""

    def test_missing_env_var_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DRY_RUN_MODE", raising=False)
        assert is_dry_run_enabled() is True

    @pytest.mark.parametrize(
        "invalid_val",
        ["", " ", "yes", "no", "1", "0", "enabled", "disabled", "random_string"],
    )
    def test_invalid_env_var_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch, invalid_val: str
    ) -> None:
        monkeypatch.setenv("DRY_RUN_MODE", invalid_val)
        assert is_dry_run_enabled() is True

    @pytest.mark.parametrize("true_val", ["true", "True", "TRUE", " true ", "\tTrue\n"])
    def test_explicit_true_returns_true(
        self, monkeypatch: pytest.MonkeyPatch, true_val: str
    ) -> None:
        monkeypatch.setenv("DRY_RUN_MODE", true_val)
        assert is_dry_run_enabled() is True

    @pytest.mark.parametrize("false_val", ["false", "False", "FALSE", " false ", "\tFalse\n"])
    def test_explicit_false_returns_false(
        self, monkeypatch: pytest.MonkeyPatch, false_val: str
    ) -> None:
        monkeypatch.setenv("DRY_RUN_MODE", false_val)
        assert is_dry_run_enabled() is False


class TestSettingsDefaultsAndProperties:
    """Tests for Settings initialization and computed properties."""

    def test_settings_properties_and_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KITE_API_KEY", "dummy_key")
        monkeypatch.setenv("KITE_API_SECRET", "dummy_secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("DRY_RUN_MODE", "true")

        settings = Settings()
        assert settings.kite_api_key == "dummy_key"
        assert settings.is_production is False
        assert settings.is_dry_run is True
        assert settings.market_open_time == "09:15"
        assert settings.market_close_time == "15:30"
        assert settings.eod_export_time == "15:45"
        assert settings.slippage_bound_pct == 2.0
        assert settings.concentration_limit_pct == 15.0
        assert settings.duplicate_window_sec == 300
        assert settings.rate_limit_mutations == "10 per minute"
        assert settings.rate_limit_default == "120 per minute"
        assert settings.rebalancer_min_trade_value == Decimal("2000.00")
        assert settings.rebalancer_cash_buffer_pct == Decimal("0.02")
        assert settings.rebalancer_cash_buffer_floor == Decimal("5000.00")
        assert settings.rebalancer_turnover_cap_pct == Decimal("0.15")
        assert settings.rebalancer_adv_limit_pct == Decimal("0.01")

    def test_production_environment_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KITE_API_KEY", "dummy_key")
        monkeypatch.setenv("KITE_API_SECRET", "dummy_secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
        monkeypatch.setenv("APP_ENV", "production")

        settings = Settings()
        assert settings.is_production is True

    def test_get_settings_cached(self) -> None:
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2
