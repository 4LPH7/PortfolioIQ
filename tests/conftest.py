"""
pytest configuration for PortfolioIQ test suite.
Sets up path, env vars, and shared fixtures.
"""
import sys
import os
from pathlib import Path

# Add project root to Python path so 'src.*' imports resolve in tests
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Set test environment variables before any module imports
os.environ.setdefault("KITE_API_KEY",    "test_api_key")
os.environ.setdefault("KITE_API_SECRET", "test_api_secret")
os.environ.setdefault("KITE_CLIENT_ID",  "TEST123")
os.environ.setdefault("DATABASE_URL",    "postgresql://portfolioiq_user:portfolioiq_pass_change_me@localhost:5432/portfolioiq")
os.environ.setdefault("DRY_RUN_MODE",    "true")
os.environ.setdefault("APP_ENV",         "development")
