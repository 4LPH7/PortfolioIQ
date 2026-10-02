#!/bin/sh
set -e

echo "=========================================================="
echo "  PortfolioIQ Container Initializing..."
echo "=========================================================="

# Wait for PostgreSQL
echo "[1/3] Checking database connectivity..."
python -c "
import time, os, sys
from sqlalchemy import create_engine, text

db_url = os.environ.get('DATABASE_URL')
if not db_url:
    print('WARNING: DATABASE_URL not set.')
    sys.exit(0)

max_retries = 30
for i in range(max_retries):
    try:
        engine = create_engine(db_url, connect_args={'connect_timeout': 3})
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        print('PostgreSQL is ready and accepting connections.')
        sys.exit(0)
    except Exception as exc:
        print(f'Waiting for PostgreSQL... ({i+1}/{max_retries})')
        time.sleep(1)

print('Error: Database connection timed out after 30 seconds.')
sys.exit(1)
"

# Run migrations
echo "[2/3] Applying schema migrations..."
python db/run_migrations.py || {
    echo "Warning: Migration runner exited with non-zero status. Proceeding with application startup..."
}

# Ensure instrument seed status
echo "[3/3] Checking instrument seeds..."
python -c "
from src.db.connection import execute_sql
try:
    rows = execute_sql('SELECT COUNT(*) as c FROM instrument_master')
    count = rows[0]['c'] if rows else 0
    print(f'Instrument master table contains {count} records.')
except Exception as e:
    print(f'Could not query instrument_master count: {e}')
" || true

echo "=========================================================="
echo "  Starting PortfolioIQ Backend Engine"
echo "=========================================================="
exec "$@"
