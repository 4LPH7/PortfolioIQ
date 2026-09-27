"""
PortfolioIQ — Rate Limiter Extension
"""

import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from src.config.settings import get_settings

settings = get_settings()
is_testing = os.environ.get("TESTING", "").strip().lower() == "true"

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit_default],
    storage_uri="memory://",
    enabled=not is_testing,
)
