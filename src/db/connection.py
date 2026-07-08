"""
PortfolioIQ — Database Connection & Session Management
SQLAlchemy 2.0 engine with connection pooling.
All modules use get_db_session() as a context manager.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from loguru import logger
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.config.settings import get_settings


def _build_engine() -> Engine:
    """
    Create and configure the SQLAlchemy engine.
    Uses NullPool is NOT used here — we want persistent pooling for
    a long-running daemon, not a serverless environment.
    """
    settings = get_settings()

    engine = create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_max_overflow,
        pool_pre_ping=True,           # Test connections before use (handles stale conns)
        pool_recycle=3600,            # Recycle connections every 1 hour
        echo=(settings.log_level == "DEBUG"),  # Log SQL in DEBUG mode
        future=True,                  # SQLAlchemy 2.0 style
    )

    # Log successful connection on first use
    @event.listens_for(engine, "connect")
    def on_connect(dbapi_conn, connection_record):
        logger.debug("New DB connection established")

    return engine


# Module-level singletons — created once, reused across the process
_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    """Return the singleton SQLAlchemy engine."""
    global _engine
    if _engine is None:
        _engine = _build_engine()
        logger.info("Database engine initialized (pool_size={})",
                    get_settings().db_pool_size)
    return _engine


def get_session_factory() -> sessionmaker:
    """Return the singleton session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,   # Prevent lazy-load errors after commit
        )
    return _SessionLocal


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    Context manager that yields a database session.
    Automatically commits on success, rolls back on exception.

    Usage:
        with get_db_session() as session:
            results = session.execute(text("SELECT 1")).fetchall()
    """
    SessionLocal = get_session_factory()
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("DB session rolled back due to: {}", exc)
        raise
    finally:
        session.close()


def execute_sql(sql: str, params: dict | None = None) -> list[dict]:
    """
    Execute a raw SQL query and return results as a list of dicts.
    Convenience function for one-off queries that don't need ORM.

    Args:
        sql: SQL string (use :param_name placeholders for safety)
        params: Dictionary of parameter bindings

    Returns:
        List of row dictionaries

    Usage:
        rows = execute_sql(
            "SELECT * FROM live_prices WHERE instrument_token = :token",
            {"token": 408065}
        )
    """
    with get_db_session() as session:
        result = session.execute(text(sql), params or {})
        if result.returns_rows:
            keys = result.keys()
            return [dict(zip(keys, row)) for row in result.fetchall()]
        return []


def check_connection() -> bool:
    """
    Verify database connectivity. Returns True if connected.
    Used in startup health checks.
    """
    try:
        execute_sql("SELECT 1 AS health_check")
        logger.info("Database connection: OK")
        return True
    except Exception as exc:
        logger.error("Database connection FAILED: {}", exc)
        return False
