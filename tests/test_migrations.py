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
