"""
PortfolioIQ — Test Configuration & PostgreSQL Fixture Harness
Provides path resolution, test environment configuration, migration bootstrapping,
nested transactional rollbacks, and offline DB auto-skipping.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Generator

import psycopg2
import pytest
from sqlalchemy.orm import Session, sessionmaker

# 1. Path resolution — ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 2. Test Environment Defaults
TEST_DB_URL = os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://portfolioiq_user:portfolioiq_pass_change_me@localhost:5432/portfolioiq",
)
os.environ.setdefault("KITE_API_KEY", "test_api_key")
os.environ.setdefault("KITE_API_SECRET", "test_api_secret")
os.environ.setdefault("KITE_CLIENT_ID", "TEST123")
os.environ.setdefault("DRY_RUN_MODE", "true")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("TESTING", "true")

IS_CI = os.environ.get("CI", "").strip().lower() in ("true", "1")


def is_postgres_reachable(dsn: str, timeout: int = 1) -> bool:
    """Quick connection probe to determine if PostgreSQL is responsive."""
    try:
        conn = psycopg2.connect(dsn, connect_timeout=timeout)
        conn.close()
        return True
    except Exception:
        return False


POSTGRES_AVAILABLE = is_postgres_reachable(TEST_DB_URL)


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "postgres: mark test as requiring a live PostgreSQL database instance",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Auto-skip or fail-fast based on PostgreSQL availability."""
    if item.get_closest_marker("postgres") is not None:
        if not POSTGRES_AVAILABLE:
            if IS_CI:
                pytest.fail(
                    f"PostgreSQL service container is unreachable in CI ({TEST_DB_URL})! "
                    "Failing fast per CI quality gate."
                )
            else:
                pytest.skip("PostgreSQL is not reachable locally. Skipping DB-dependent test.")


@pytest.fixture(scope="session", autouse=True)
def run_database_migrations_at_session_start() -> None:
    """Run migrations once per test session if PostgreSQL is available."""
    if not POSTGRES_AVAILABLE:
        if IS_CI:
            pytest.fail("Cannot execute session migrations in CI: PostgreSQL is unreachable.")
        return

    from db.run_migrations import run_migrations

    run_migrations()


@pytest.fixture
def db_connection() -> Generator:
    """
    Function-scoped database connection inside an outer transaction and savepoint.
    Rolls back automatically at test teardown so no test mutates state.
    """
    if not POSTGRES_AVAILABLE:
        if IS_CI:
            pytest.fail("PostgreSQL is required in CI.")
        pytest.skip("PostgreSQL is not reachable locally.")

    from src.db.connection import get_engine

    engine = get_engine()
    connection = engine.connect()
    trans = connection.begin()
    nested = connection.begin_nested()

    yield connection

    if nested.is_active:
        nested.rollback()
    if trans.is_active:
        trans.rollback()
    connection.close()


@pytest.fixture
def db_session(db_connection, monkeypatch: pytest.MonkeyPatch) -> Generator[Session, None, None]:
    """
    Function-scoped SQLAlchemy Session wired to db_connection with savepoint handling.
    Patches src.db.connection.get_session_factory so all repository calls and
    get_db_session() context managers operate inside the savepoint.
    """
    test_sessionmaker = sessionmaker(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    from src.db import connection as db_module

    monkeypatch.setattr(db_module, "get_session_factory", lambda: test_sessionmaker)

    session = test_sessionmaker()
    try:
        yield session
    finally:
        session.close()
