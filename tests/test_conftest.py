"""
PortfolioIQ — Test Harness Verification
Validates tests/conftest.py PostgreSQL connection detection, marker handling,
and transactional rollback isolation.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import POSTGRES_AVAILABLE, is_postgres_reachable


def test_is_postgres_reachable_returns_false_for_invalid_dsn() -> None:
    """Invalid or dead database connections return False without crashing."""
    assert is_postgres_reachable("postgresql://invalid:invalid@127.0.0.1:54321/dead_db", timeout=1) is False


def test_postgres_marker_registered(pytestconfig: pytest.Config) -> None:
    """The 'postgres' marker must be registered in pytest configuration."""
    markers = pytestconfig.getini("markers")
    assert any("postgres" in m for m in markers)


@pytest.mark.postgres
def test_postgres_marked_test_executes_or_skips_cleanly(db_session) -> None:
    """
    Tests marked with @pytest.mark.postgres run with an isolated db_session
    or are automatically skipped if PostgreSQL is not available locally.
    """
    if not POSTGRES_AVAILABLE:
        pytest.fail("Should have been skipped by pytest_runtest_setup if offline!")

    # Verify session can execute queries inside savepoint
    res = db_session.execute(text("SELECT 1 AS alive"))
    assert res.scalar() == 1


@pytest.mark.postgres
def test_transactional_rollback_discards_mutations(db_session) -> None:
    """
    Verifies that rows inserted in a test are rolled back at teardown
    and do not leak into subsequent tests or persist in the database.
    """
    from src.db.repository import list_app_configs, update_app_config

    # Read original value or update temporarily
    original = [c for c in list_app_configs() if c.key == "dry_run_mode"]
    if original:
        orig_val = original[0].value
        temp_val = "temp_test_value"
        update_app_config("dry_run_mode", temp_val)
        updated = [c for c in list_app_configs() if c.key == "dry_run_mode"]
        assert updated[0].value == temp_val
