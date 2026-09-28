"""
Tests for db/run_migrations.py idempotent tracking and tamper detection.
"""

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from db.run_migrations import (
    MigrationTamperedError,
    calculate_migration_checksum,
    ensure_schema_migrations_table,
    get_applied_migrations,
    run_migrations,
)


def test_calculate_migration_checksum_crlf_lf_parity(tmp_path: Path):
    """Verify that Windows CRLF and Linux LF line endings produce identical SHA-256 hashes."""
    crlf_file = tmp_path / "test_crlf.sql"
    lf_file = tmp_path / "test_lf.sql"

    crlf_file.write_bytes(b"CREATE TABLE foo (\r\n    id SERIAL PRIMARY KEY\r\n);\r\n")
    lf_file.write_bytes(b"CREATE TABLE foo (\n    id SERIAL PRIMARY KEY\n);\n")

    crlf_hash = calculate_migration_checksum(crlf_file)
    lf_hash = calculate_migration_checksum(lf_file)

    assert crlf_hash == lf_hash
    # Expected SHA-256 of "CREATE TABLE foo (\n    id SERIAL PRIMARY KEY\n);\n"
    expected = hashlib.sha256(b"CREATE TABLE foo (\n    id SERIAL PRIMARY KEY\n);\n").hexdigest()
    assert crlf_hash == expected


def test_ensure_schema_migrations_table():
    """Verify schema_migrations table creation SQL is executed."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    ensure_schema_migrations_table(mock_conn)

    assert mock_cursor.execute.called
    executed_sql = mock_cursor.execute.call_args[0][0]
    assert "CREATE TABLE IF NOT EXISTS schema_migrations" in executed_sql
    assert mock_conn.commit.called
    assert mock_cursor.close.called


def test_get_applied_migrations():
    """Verify get_applied_migrations parses query results into dict."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        ("001_first.sql", "hash1"),
        ("002_second.sql", "hash2"),
    ]

    applied = get_applied_migrations(mock_conn)

    assert applied == {"001_first.sql": "hash1", "002_second.sql": "hash2"}


def test_run_migrations_skips_applied(tmp_path: Path):
    """Verify already applied migrations are skipped idempotently."""
    mock_file = tmp_path / "001_sample.sql"
    mock_file.write_text("SELECT 1;\n", encoding="utf-8")
    file_hash = calculate_migration_checksum(mock_file)

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with (
        patch("db.run_migrations.get_connection", return_value=mock_conn),
        patch("db.run_migrations.get_migration_files", return_value=[mock_file]),
        patch("db.run_migrations.ensure_schema_migrations_table"),
        patch(
            "db.run_migrations.get_applied_migrations", return_value={"001_sample.sql": file_hash}
        ),
    ):
        run_migrations()

        # cursor.execute should NOT have executed the file's SQL
        assert not any("SELECT 1" in str(call) for call in mock_cursor.execute.call_args_list)


def test_run_migrations_detects_tampering(tmp_path: Path):
    """Verify tampering with an already applied migration raises MigrationTamperedError."""
    mock_file = tmp_path / "001_sample.sql"
    mock_file.write_text("ALTER TABLE altered;\n", encoding="utf-8")

    mock_conn = MagicMock()

    with (
        patch("db.run_migrations.get_connection", return_value=mock_conn),
        patch("db.run_migrations.get_migration_files", return_value=[mock_file]),
        patch("db.run_migrations.ensure_schema_migrations_table"),
        patch(
            "db.run_migrations.get_applied_migrations",
            return_value={"001_sample.sql": "different_historical_hash"},
        ),
    ):
        with pytest.raises(MigrationTamperedError) as exc_info:
            run_migrations()

        assert "Checksum mismatch for already applied migration '001_sample.sql'" in str(
            exc_info.value
        )


def test_run_migrations_executes_unapplied(tmp_path: Path):
    """Verify unapplied migrations are executed and recorded in schema_migrations."""
    mock_file = tmp_path / "002_new.sql"
    mock_file.write_text("CREATE TABLE test_tab (id INT);\n", encoding="utf-8")
    calculate_migration_checksum(mock_file)

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with (
        patch("db.run_migrations.get_connection", return_value=mock_conn),
        patch("db.run_migrations.get_migration_files", return_value=[mock_file]),
        patch("db.run_migrations.ensure_schema_migrations_table"),
        patch("db.run_migrations.get_applied_migrations", return_value={}),
    ):
        run_migrations()

        # Check that file SQL was executed
        assert any(
            "CREATE TABLE test_tab" in str(call) for call in mock_cursor.execute.call_args_list
        )
        # Check that record was inserted
        assert any(
            "INSERT INTO schema_migrations" in str(call)
            for call in mock_cursor.execute.call_args_list
        )
        assert mock_conn.commit.called


def test_phase_3_table_structures():
    """Verify table structures for holdings_reconciliation_log and market_calendar enhancements."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from src.db.connection import get_db_session

    try:
        with get_db_session() as session:
            # Check holdings_reconciliation_log exists
            result = session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'holdings_reconciliation_log'"
                )
            )
            columns = {r[0] for r in result.fetchall()}
            assert "reconciliation_reason" in columns
            assert "delta_quantity" in columns
            assert "instrument_token" in columns

            # Check market_calendar enhancements
            result = session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'market_calendar'"
                )
            )
            columns = {r[0] for r in result.fetchall()}
            assert "segment" in columns
            assert "is_trading_holiday" in columns
            assert "special_session_open" in columns
            assert "special_session_close" in columns
            assert "description" in columns
    except OperationalError:
        pytest.skip("Database not available")


def test_phase_4_table_structures():
    """Verify table structures for portfolio_daily_snapshots and portfolio_cash_flows."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from src.db.connection import get_db_session

    try:
        with get_db_session() as session:
            # Check portfolio_daily_snapshots exists
            result = session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'portfolio_daily_snapshots'"
                )
            )
            columns = {r[0] for r in result.fetchall()}
            assert "snapshot_date" in columns
            assert "unit_nav" in columns
            assert "units" in columns
            assert "daily_return_pct" in columns
            assert "benchmark_name" in columns

            # Check portfolio_cash_flows exists
            result = session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'portfolio_cash_flows'"
                )
            )
            columns = {r[0] for r in result.fetchall()}
            assert "flow_date" in columns
            assert "flow_type" in columns
            assert "amount" in columns
            assert "source" in columns
    except OperationalError:
        pytest.skip("Database not available")


def test_phase_5_table_structures():
    """Verify table structures for signal_snapshots, backtest_runs, indicator_evaluations, and historical_daily_bars."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from src.db.connection import get_db_session

    try:
        with get_db_session() as session:
            # 1. signal_snapshots
            result = session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'signal_snapshots'"
                )
            )
            sig_cols = {r[0] for r in result.fetchall()}
            assert "snapshot_date" in sig_cols
            assert "tradingsymbol" in sig_cols
            assert "composite_score" in sig_cols
            assert "signal_label" in sig_cols
            assert "status" in sig_cols
            assert "indicators" in sig_cols
            assert "monte_carlo" in sig_cols
            assert "return_5d_stock" in sig_cols
            assert "return_20d_stock" in sig_cols
            assert "return_60d_stock" in sig_cols
            assert "excess_return_20d" in sig_cols

            # 2. backtest_runs
            result = session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'backtest_runs'"
                )
            )
            bt_cols = {r[0] for r in result.fetchall()}
            assert "run_id" in bt_cols
            assert "strategy_cagr" in bt_cols
            assert "stock_cagr" in bt_cols
            assert "benchmark_cagr" in bt_cols
            assert "excess_cagr_vs_stock" in bt_cols
            assert "passed_hurdle" in bt_cols
            assert "status" in bt_cols

            # 3. indicator_evaluations
            result = session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'indicator_evaluations'"
                )
            )
            eval_cols = {r[0] for r in result.fetchall()}
            assert "backtest_run_id" in eval_cols
            assert "indicator_name" in eval_cols
            assert "mean_ic" in eval_cols
            assert "information_ratio" in eval_cols
            assert "weight" in eval_cols
            assert "is_pruned" in eval_cols

            # 4. historical_daily_bars
            result = session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'historical_daily_bars'"
                )
            )
            bar_cols = {r[0] for r in result.fetchall()}
            assert "tradingsymbol" in bar_cols
            assert "bar_date" in bar_cols
            assert "open" in bar_cols
            assert "high" in bar_cols
            assert "low" in bar_cols
            assert "close" in bar_cols
            assert "volume" in bar_cols
    except OperationalError:
        pytest.skip("Database not available")
