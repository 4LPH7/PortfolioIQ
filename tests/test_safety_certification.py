"""
Tests for Phase 8 Trading Safety Certification and Startup Health Check.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.execution.safety_certification import (
    LIVE_CONFIRMATION_TOKEN,
    StartupSafetyCheckError,
    check_broker_authentication,
    check_live_mode_authorization,
    check_price_feed_freshness,
    check_schema_integrity,
    verify_startup_safety,
)


def test_dry_run_mode_authorized_automatically():
    """In dry run mode, live authorization check passes safely."""
    with patch("src.execution.safety_certification.is_dry_run_enabled", return_value=True):
        ok, msg = check_live_mode_authorization()
        assert ok is True
        assert "Dry-run mode active" in msg


def test_live_mode_refused_without_token(monkeypatch):
    """In live mode without token, check fails."""
    monkeypatch.delenv("LIVE_TRADING_CONFIRMATION", raising=False)
    with patch("src.execution.safety_certification.is_dry_run_enabled", return_value=False):
        ok, msg = check_live_mode_authorization()
        assert ok is False
        assert "LIVE MODE REFUSED" in msg


def test_live_mode_authorized_with_exact_token(monkeypatch):
    """In live mode with exact token, check passes with warning."""
    monkeypatch.setenv("LIVE_TRADING_CONFIRMATION", LIVE_CONFIRMATION_TOKEN)
    with patch("src.execution.safety_certification.is_dry_run_enabled", return_value=False):
        ok, msg = check_live_mode_authorization()
        assert ok is True
        assert "authorized" in msg.lower()


def test_broker_authentication_checks():
    """Verify broker credential validation."""
    mock_settings = MagicMock()
    mock_settings.kite_api_key = "test_key"
    mock_settings.kite_api_secret = "test_secret"

    with patch("src.execution.safety_certification.get_settings", return_value=mock_settings):
        # In dry run mode, test keys are fine
        with patch("src.execution.safety_certification.is_dry_run_enabled", return_value=True):
            ok, _ = check_broker_authentication()
            assert ok is True

        # In live mode, test keys are rejected
        with patch("src.execution.safety_certification.is_dry_run_enabled", return_value=False):
            ok, msg = check_broker_authentication()
            assert ok is False
            assert "Live broker execution requires a non-mock" in msg


def test_price_freshness_in_dry_run():
    """Price freshness check is non-blocking in dry run mode."""
    with patch("src.execution.safety_certification.is_dry_run_enabled", return_value=True):
        ok, msg = check_price_feed_freshness()
        assert ok is True
        assert "skipped" in msg.lower()


def test_schema_integrity_in_dry_run_with_error():
    """Schema check handles database connection issues gracefully in dry run."""
    with patch("src.execution.safety_certification.is_dry_run_enabled", return_value=True):
        with patch("db.run_migrations.get_connection", side_effect=Exception("DB Down")):
            ok, msg = check_schema_integrity()
            assert ok is True
            assert "skipped in dry-run mode" in msg


def test_verify_startup_safety_dry_run_passes():
    """Startup safety verification passes completely in dry-run mode."""
    with patch("src.execution.safety_certification.is_dry_run_enabled", return_value=True):
        report = verify_startup_safety()
        assert report["is_dry_run"] is True
        assert "details" in report
        assert "live_mode_auth" in report["details"]


def test_verify_startup_safety_live_mode_failure_raises(monkeypatch):
    """Startup safety verification raises StartupSafetyCheckError if live gates fail."""
    monkeypatch.delenv("LIVE_TRADING_CONFIRMATION", raising=False)
    with patch("src.execution.safety_certification.is_dry_run_enabled", return_value=False):
        with pytest.raises(StartupSafetyCheckError) as exc_info:
            verify_startup_safety()
        assert "Startup safety gate failed live mode boot" in str(exc_info.value)
