"""
PortfolioIQ — Database Migration Runner
Executes SQL migration files in numbered order with idempotent tracking.
Tracks applied versions and SHA-256 checksums in schema_migrations table.

Usage:
    python db/run_migrations.py
    python db/run_migrations.py --dry-run   # Preview pending migrations
    python db/run_migrations.py --status    # Show migration status table
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

# Load .env from project root
ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class MigrationTamperedError(Exception):
    """Raised when an already applied migration's content or checksum differs from database record."""

    pass


def get_connection(max_retries: int = 5, retry_interval: float = 2.0):
    """Create a raw psycopg2 connection with retry backoff."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set in environment or .env", file=sys.stderr)
        sys.exit(1)

    import time

    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return psycopg2.connect(dsn, connect_timeout=5)
        except psycopg2.OperationalError as exc:
            last_exc = exc
            if attempt < max_retries:
                print(
                    f"Waiting for PostgreSQL to accept connections (attempt {attempt}/{max_retries})...",
                    file=sys.stderr,
                )
                time.sleep(retry_interval)
            else:
                print(
                    f"ERROR: Could not connect to PostgreSQL after {max_retries} attempts: {exc}",
                    file=sys.stderr,
                )
                raise last_exc from exc


def get_migration_files() -> list[Path]:
    """Return all .sql files sorted numerically."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print(f"WARNING: No migration files found in {MIGRATIONS_DIR}")
    return files


def calculate_migration_checksum(file_path: Path) -> str:
    """Calculate SHA-256 hash of migration file with normalized line endings (CRLF -> LF)."""
    content = file_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def ensure_schema_migrations_table(conn) -> None:
    """Ensure the schema_migrations tracking table exists."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(255) PRIMARY KEY,
            checksum_sha256 VARCHAR(64) NOT NULL,
            executed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_schema_migrations_executed_at
            ON schema_migrations (executed_at ASC);
    """)
    conn.commit()
    cursor.close()


def get_applied_migrations(conn) -> dict[str, str]:
    """Return dictionary of {version: checksum_sha256} for all applied migrations."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT version, checksum_sha256 FROM schema_migrations ORDER BY executed_at ASC"
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.commit()
    return {row[0]: row[1] for row in rows}


def show_migration_status(conn) -> None:
    """Display status of all migrations."""
    ensure_schema_migrations_table(conn)
    applied = get_applied_migrations(conn)
    files = get_migration_files()

    print("\n" + "=" * 80)
    print(f"{'Migration Version':<35} | {'Status':<10} | {'Checksum':<16} | {'Details'}")
    print("-" * 80)

    for f in files:
        checksum = calculate_migration_checksum(f)
        short_hash = checksum[:12] + "..."
        if f.name in applied:
            stored_hash = applied[f.name]
            if stored_hash == checksum:
                status = "APPLIED"
                details = "OK (Matches DB)"
            else:
                status = "TAMPERED"
                details = f"HASH MISMATCH (DB: {stored_hash[:8]}... Local: {checksum[:8]}...)"
        else:
            status = "PENDING"
            details = "Unapplied"

        print(f"{f.name:<35} | {status:<10} | {short_hash:<16} | {details}")

    print("=" * 80 + "\n")


def run_migrations(dry_run: bool = False, baseline: bool = False) -> None:
    """Execute all pending migrations in order with verification."""
    files = get_migration_files()
    conn = get_connection()

    try:
        ensure_schema_migrations_table(conn)
        applied = get_applied_migrations(conn)

        print(
            f"\n{'DRY RUN — ' if dry_run else ''}Discovered {len(files)} migrations ({len(applied)} recorded in DB)\n"
        )
        print("=" * 70)

        unapplied = []
        for f in files:
            current_checksum = calculate_migration_checksum(f)
            if f.name in applied:
                stored_checksum = applied[f.name]
                if stored_checksum != current_checksum:
                    raise MigrationTamperedError(
                        f"Checksum mismatch for already applied migration '{f.name}'!\n"
                        f"  DB Checksum:    {stored_checksum}\n"
                        f"  Local Checksum: {current_checksum}\n"
                        "Historical migrations must not be modified. Revert changes or create a new migration."
                    )
                print(f"  [SKIPPED] {f.name} (already applied)")
            else:
                unapplied.append((f, current_checksum))

        if not unapplied:
            print("\nDatabase is fully up to date. Zero migrations pending.")
            return

        print(f"\nFound {len(unapplied)} pending migration(s):")
        for f, _ in unapplied:
            print(f"  -> {f.name}")

        if dry_run:
            print("\n[DRY RUN] No changes made. Remove --dry-run to apply.")
            return

        cursor = conn.cursor()
        for f, checksum in unapplied:
            print(f"\nExecuting {f.name} ... ", end="", flush=True)
            if baseline:
                # Baseline mode: record as applied without executing DDL
                cursor.execute(
                    "INSERT INTO schema_migrations (version, checksum_sha256, executed_at) VALUES (%s, %s, NOW())",
                    (f.name, checksum),
                )
                conn.commit()
                print("RECORDED (baseline mode)")
                continue

            try:
                sql = f.read_text(encoding="utf-8")
                cursor.execute(sql)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, checksum_sha256, executed_at) VALUES (%s, %s, NOW())",
                    (f.name, checksum),
                )
                conn.commit()
                print("OK")
            except Exception as exc:
                conn.rollback()
                print(f"FAILED\n\nERROR in {f.name}:\n{exc}\n")
                print("Migration halted and rolled back. Fix the issue and re-run.")
                raise

        cursor.close()

    finally:
        conn.close()

    print("\n" + "=" * 70)
    print("[OK] All migrations applied and recorded successfully.")


def main():
    parser = argparse.ArgumentParser(description="PortfolioIQ Database Migration Runner")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview pending migrations without executing"
    )
    parser.add_argument("--status", action="store_true", help="Show migration status table")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Mark pending migrations as applied without executing DDL",
    )
    args = parser.parse_args()

    if args.status:
        conn = get_connection()
        try:
            show_migration_status(conn)
        finally:
            conn.close()
    else:
        try:
            run_migrations(dry_run=args.dry_run, baseline=args.baseline)
        except Exception as exc:
            print(f"\nCRITICAL: Migration run failed: {exc}", file=sys.stderr)
            import traceback

            traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    main()
