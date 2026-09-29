"""
PortfolioIQ — Ticker Mapping Engine
Centralizes mappings from Zerodha trading symbols to Yahoo Finance tickers.
"""

from __future__ import annotations

from typing import Final

# Overrides for symbols that don't follow the straightforward {symbol}.NS pattern
# or require specific exchange suffixes (e.g. BSE-only scrips, series suffixes)
SYMBOL_OVERRIDES: Final[dict[str, str]] = {
    "ITBEES": "ITBEES.NS",
    "JPPOWER": "JPPOWER.NS",
    "GOLDENTOBC-BZ": "GOLDENTOBC.NS",
    "GOLDENTOBC": "GOLDENTOBC.NS",
    "RPOWER": "RPOWER.NS",
    "TATAGOLD": "TATAGOLD.NS",
    "GOLDCASE": "GOLDCASE.BO",
    "NIFTY 50": "^NSEI",
    "NIFTY 50 TRI": "^NSEI",
    "NIFTY": "^NSEI",
    "NIFTYBEES": "NIFTYBEES.NS",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
}

DEFAULT_BENCHMARK_SYMBOL: Final[str] = "NIFTY 50 TRI"
DEFAULT_BENCHMARK_TICKER: Final[str] = "^NSEI"


def get_yf_ticker(tradingsymbol: str, exchange: str = "NSE") -> str:
    """
    Map a Zerodha trading symbol to its Yahoo Finance ticker string.

    Args:
        tradingsymbol: e.g. 'RELIANCE', 'ITBEES', 'GOLDCASE', 'NIFTY 50 TRI'
        exchange: Exchange code, default 'NSE' (used if symbol not in overrides)

    Returns:
        Yahoo Finance ticker string, e.g. 'RELIANCE.NS', '^NSEI'
    """
    clean_sym = tradingsymbol.strip().upper()
    if clean_sym in SYMBOL_OVERRIDES:
        return SYMBOL_OVERRIDES[clean_sym]

    suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
    return f"{clean_sym}{suffix}"


def get_benchmark_ticker(benchmark_symbol: str = DEFAULT_BENCHMARK_SYMBOL) -> str:
    """
    Get the Yahoo Finance ticker for the comparative benchmark index.

    Args:
        benchmark_symbol: Standard benchmark name (default 'NIFTY 50 TRI')

    Returns:
        Yahoo Finance ticker string (e.g. '^NSEI')
    """
    return get_yf_ticker(benchmark_symbol)
