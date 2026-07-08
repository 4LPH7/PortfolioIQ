"""
PortfolioIQ — Database Migration Runner
Executes all SQL migration files in numbered order.
Safe to re-run: migrations use IF NOT EXISTS and ON CONFLICT DO NOTHING.

Usage:
    python db/run_migrations.py
    python db/run_migrations.py --dry-run   # Show files only, don't execute
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

# Load .env from project root
ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_connection():
    """Create a raw psycopg2 connection (no SQLAlchemy, simpler for migrations)."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set in .env")
        sys.exit(1)
    return psycopg2.connect(dsn)


def get_migration_files() -> list[Path]:
    """Return all .sql files sorted numerically."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print(f"WARNING: No migration files found in {MIGRATIONS_DIR}")
    return files


def run_migrations(dry_run: bool = False) -> None:
    """Execute all migrations in order."""
    files = get_migration_files()

    print(f"\n{'DRY RUN — ' if dry_run else ''}Running {len(files)} migrations\n")
    print("=" * 60)

    if dry_run:
        for f in files:
            print(f"  [DRY RUN] Would execute: {f.name}")
        print("\nNo changes made. Remove --dry-run to execute.")
        return

    conn = get_connection()
    conn.autocommit = False

    try:
        cursor = conn.cursor()

        for migration_file in files:
            print(f"  -> Executing: {migration_file.name} ...", end="", flush=True)
            try:
                sql = migration_file.read_text(encoding="utf-8")
                cursor.execute(sql)
                conn.commit()
                print(" OK")
            except Exception as exc:
                conn.rollback()
                print(f" FAILED\n\nERROR in {migration_file.name}:\n{exc}\n")
                print("Migration stopped. Fix the error and re-run.")
                sys.exit(1)

        cursor.close()

    finally:
        conn.close()

    print("\n" + "=" * 60)
    print("[OK] All migrations completed successfully.")
    print("\nNext steps:")
    print("  1. python -m src.ingestion.kite_auth    (authenticate with Zerodha)")
    print("  2. python -m src.ingestion.instrument_mapper  (sync instrument master)")
    print("  3. python -m src.ingestion.kite_sync    (pull holdings + margins)")
    print("  4. streamlit run ui/app.py               (start the dashboard)")


def main():
    parser = argparse.ArgumentParser(description="PortfolioIQ Database Migration Runner")
    parser.add_argument("--dry-run", action="store_true", help="Show files without executing")
    args = parser.parse_args()
    run_migrations(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
