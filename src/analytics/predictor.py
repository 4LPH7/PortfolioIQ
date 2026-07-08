"""
PortfolioIQ — Stock Prediction Engine
5 analysis methods applied to current holdings.

Methods:
  1. RSI (Relative Strength Index) — momentum oscillator
  2. MACD — trend-following crossover
  3. Bollinger Bands — volatility / mean-reversion
  4. Linear Regression Momentum — OLS slope over 30 days
  5. Monte Carlo Simulation — GBM price distribution (30-day)

Data: yfinance 1-year daily OHLCV. Cached per session to avoid
repeated network calls during Streamlit reruns.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf
from loguru import logger
from scipy import stats

warnings.filterwarnings("ignore")

# ──────────────────────────────────────────────────────────
# Ticker mapping: Zerodha symbol → Yahoo Finance ticker
# ──────────────────────────────────────────────────────────
TICKER_MAP: dict[str, str] = {
    "ITBEES": "ITBEES.NS",
    "JPPOWER": "JPPOWER.NS",
    "GOLDENTOBC-BZ": "GOLDENTOBC.NS",
    "RPOWER": "RPOWER.NS",
    "TATAGOLD": "TATAGOLD.NS",
    "GOLDCASE": "GOLDCASE.BO",
}

# ──────────────────────────────────────────────────────────
# Signal constants
# ──────────────────────────────────────────────────────────
STRONG_BUY = "STRONG BUY"
BUY = "BUY"
HOLD = "HOLD"
SELL = "SELL"
STRONG_SELL = "STRONG SELL"

SIGNAL_SCORES = {STRONG_BUY: 100, BUY: 75, HOLD: 50, SELL: 25, STRONG_SELL: 0}

# ──────────────────────────────────────────────────────────
# Data Classes
# ──────────────────────────────────────────────────────────

@dataclass
class RSIResult:
    current_rsi: float
    signal: str
    overbought: bool
    oversold: bool
    rsi_series: list[float] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)


@dataclass
class MACDResult:
    macd_line: float
    signal_line: float
    histogram: float
    signal: str
    is_crossover: bool        # recent bullish crossover
    is_crossunder: bool       # recent bearish crossunder
    macd_series: list[float] = field(default_factory=list)
    signal_series: list[float] = field(default_factory=list)
    hist_series: list[float] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)


@dataclass
class BollingerResult:
    upper_band: float
    middle_band: float         # 20-day SMA
    lower_band: float
    current_price: float
    bandwidth: float           # (upper - lower) / middle
    percent_b: float           # position within bands (0=lower, 1=upper)
    signal: str
    upper_series: list[float] = field(default_factory=list)
    middle_series: list[float] = field(default_factory=list)
    lower_series: list[float] = field(default_factory=list)
    price_series: list[float] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)


@dataclass
class LinearRegressionResult:
    slope: float               # price change per day (INR)
    slope_pct: float           # as % of current price per day
    r_squared: float           # goodness of fit
    predicted_30d: float       # OLS price forecast 30 days out
    signal: str
    confidence: float          # R² as confidence proxy
    regression_series: list[float] = field(default_factory=list)
    price_series: list[float] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)


@dataclass
class MonteCarloResult:
    current_price: float
    simulations: int = 1000
    horizon_days: int = 30
    # Price percentiles at end of simulation
    p10: float = 0.0           # pessimistic (10th percentile)
    p25: float = 0.0
    p50: float = 0.0           # median expectation
    p75: float = 0.0
    p90: float = 0.0           # optimistic (90th percentile)
    expected_return_pct: float = 0.0
    prob_profit: float = 0.0   # % of simulations showing a gain
    daily_volatility: float = 0.0
    annual_volatility: float = 0.0
    signal: str = HOLD
    # Fan chart data: list of [date, p10, p25, p50, p75, p90]
    fan_dates: list[str] = field(default_factory=list)
    fan_p10: list[float] = field(default_factory=list)
    fan_p25: list[float] = field(default_factory=list)
    fan_p50: list[float] = field(default_factory=list)
    fan_p75: list[float] = field(default_factory=list)
    fan_p90: list[float] = field(default_factory=list)


@dataclass
class CompositeResult:
    score: float               # 0–100
    signal: str
    rsi_score: float
    macd_score: float
    bollinger_score: float
    lr_score: float
    monte_carlo_score: float
    summary: str               # human-readable conclusion


@dataclass
class StockAnalysis:
    symbol: str
    yf_ticker: str
    current_price: float
    avg_buy_price: float
    data_start: str
    data_end: str
    data_points: int
    # Method results
    rsi: RSIResult | None = None
    macd: MACDResult | None = None
    bollinger: BollingerResult | None = None
    linear_regression: LinearRegressionResult | None = None
    monte_carlo: MonteCarloResult | None = None
    composite: CompositeResult | None = None
    error: str | None = None
    # Raw OHLCV for candlestick chart
    ohlcv: pd.DataFrame | None = None


# ──────────────────────────────────────────────────────────
# Data Fetching
# ──────────────────────────────────────────────────────────

_cache: dict[str, tuple[datetime, pd.DataFrame]] = {}
CACHE_TTL_MINUTES = 60  # refresh every hour


def _fetch_history(yf_ticker: str, period: str = "1y") -> pd.DataFrame | None:
    """Fetch daily OHLCV from yfinance with in-memory cache."""
    now = datetime.now()
    if yf_ticker in _cache:
        cached_at, df = _cache[yf_ticker]
        if (now - cached_at).total_seconds() < CACHE_TTL_MINUTES * 60:
            return df

    try:
        logger.debug("Fetching yfinance data for {}", yf_ticker)
        ticker = yf.Ticker(yf_ticker)
        df = ticker.history(period=period, auto_adjust=True)
        if df.empty or len(df) < 30:
            logger.warning("Insufficient data for {} ({} rows)", yf_ticker, len(df))
            return None
        df.index = pd.to_datetime(df.index)
        # Flatten MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        _cache[yf_ticker] = (now, df)
        logger.debug("Fetched {} rows for {}", len(df), yf_ticker)
        return df
    except Exception as exc:
        logger.error("yfinance fetch failed for {}: {}", yf_ticker, exc)
        return None


# ──────────────────────────────────────────────────────────
# Method 1: RSI
# ──────────────────────────────────────────────────────────

def compute_rsi(df: pd.DataFrame, period: int = 14) -> RSIResult:
    """Wilder's RSI on closing prices."""
    close = df["Close"].dropna()
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    # Wilder's smoothing
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    current_rsi = float(rsi.iloc[-1])
    rsi_last60 = rsi.iloc[-60:]

    if current_rsi >= 75:
        signal = STRONG_SELL
    elif current_rsi >= 60:
        signal = SELL
    elif current_rsi <= 25:
        signal = STRONG_BUY
    elif current_rsi <= 40:
        signal = BUY
    else:
        signal = HOLD

    return RSIResult(
        current_rsi=round(current_rsi, 2),
        signal=signal,
        overbought=current_rsi > 70,
        oversold=current_rsi < 30,
        rsi_series=[round(float(x), 2) for x in rsi_last60.fillna(50)],
        dates=[str(d.date()) for d in rsi_last60.index],
    )


# ──────────────────────────────────────────────────────────
# Method 2: MACD
# ──────────────────────────────────────────────────────────

def compute_macd(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal_period: int = 9
) -> MACDResult:
    """Standard 12/26/9 MACD."""
    close = df["Close"].dropna()
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram = macd_line - signal_line

    last60 = slice(-60, None)
    current_macd = float(macd_line.iloc[-1])
    current_signal = float(signal_line.iloc[-1])
    current_hist = float(histogram.iloc[-1])
    prev_hist = float(histogram.iloc[-2]) if len(histogram) > 1 else 0

    # Crossover detection (last 3 bars)
    recent_macd = macd_line.iloc[-3:]
    recent_sig = signal_line.iloc[-3:]
    is_crossover = bool(
        recent_macd.iloc[-1] > recent_sig.iloc[-1]
        and recent_macd.iloc[-2] <= recent_sig.iloc[-2]
    )
    is_crossunder = bool(
        recent_macd.iloc[-1] < recent_sig.iloc[-1]
        and recent_macd.iloc[-2] >= recent_sig.iloc[-2]
    )

    if is_crossover and current_macd > 0:
        signal = STRONG_BUY
    elif is_crossover:
        signal = BUY
    elif is_crossunder and current_macd < 0:
        signal = STRONG_SELL
    elif is_crossunder:
        signal = SELL
    elif current_macd > current_signal and current_hist > prev_hist:
        signal = BUY
    elif current_macd < current_signal and current_hist < prev_hist:
        signal = SELL
    else:
        signal = HOLD

    return MACDResult(
        macd_line=round(current_macd, 4),
        signal_line=round(current_signal, 4),
        histogram=round(current_hist, 4),
        signal=signal,
        is_crossover=is_crossover,
        is_crossunder=is_crossunder,
        macd_series=[round(float(x), 4) for x in macd_line.iloc[last60].fillna(0)],
        signal_series=[round(float(x), 4) for x in signal_line.iloc[last60].fillna(0)],
        hist_series=[round(float(x), 4) for x in histogram.iloc[last60].fillna(0)],
        dates=[str(d.date()) for d in macd_line.iloc[last60].index],
    )


# ──────────────────────────────────────────────────────────
# Method 3: Bollinger Bands
# ──────────────────────────────────────────────────────────

def compute_bollinger(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> BollingerResult:
    """Standard 20-day SMA ± 2σ Bollinger Bands."""
    close = df["Close"].dropna()
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = sma + std_dev * std
    lower = sma - std_dev * std

    cur_price = float(close.iloc[-1])
    cur_upper = float(upper.iloc[-1])
    cur_mid = float(sma.iloc[-1])
    cur_lower = float(lower.iloc[-1])
    bandwidth = (cur_upper - cur_lower) / cur_mid if cur_mid > 0 else 0
    band_range = cur_upper - cur_lower
    percent_b = (cur_price - cur_lower) / band_range if band_range > 0 else 0.5

    if percent_b < 0.05:
        signal = STRONG_BUY
    elif percent_b < 0.25:
        signal = BUY
    elif percent_b > 0.95:
        signal = STRONG_SELL
    elif percent_b > 0.75:
        signal = SELL
    else:
        signal = HOLD

    last60 = slice(-60, None)
    return BollingerResult(
        upper_band=round(cur_upper, 2),
        middle_band=round(cur_mid, 2),
        lower_band=round(cur_lower, 2),
        current_price=round(cur_price, 2),
        bandwidth=round(bandwidth, 4),
        percent_b=round(percent_b, 4),
        signal=signal,
        upper_series=[round(float(x), 2) for x in upper.iloc[last60].fillna(cur_upper)],
        middle_series=[round(float(x), 2) for x in sma.iloc[last60].fillna(cur_mid)],
        lower_series=[round(float(x), 2) for x in lower.iloc[last60].fillna(cur_lower)],
        price_series=[round(float(x), 2) for x in close.iloc[last60].fillna(cur_price)],
        dates=[str(d.date()) for d in close.iloc[last60].index],
    )


# ──────────────────────────────────────────────────────────
# Method 4: Linear Regression Momentum
# ──────────────────────────────────────────────────────────

def compute_linear_regression(df: pd.DataFrame, lookback: int = 30) -> LinearRegressionResult:
    """OLS regression on closing prices over last `lookback` days."""
    close = df["Close"].dropna().iloc[-lookback:]
    x = np.arange(len(close))
    y = close.values.astype(float)

    slope, intercept, r_value, p_value, std_err = stats.linregress(x, y)
    r_squared = r_value ** 2
    cur_price = float(close.iloc[-1])
    slope_pct = (slope / cur_price) * 100 if cur_price > 0 else 0
    predicted_30d = float(intercept + slope * (len(close) + 30))

    if slope_pct > 0.5 and r_squared > 0.7:
        signal = STRONG_BUY
    elif slope_pct > 0.15:
        signal = BUY
    elif slope_pct < -0.5 and r_squared > 0.7:
        signal = STRONG_SELL
    elif slope_pct < -0.15:
        signal = SELL
    else:
        signal = HOLD

    regression_line = [float(intercept + slope * xi) for xi in x]
    last60_close = df["Close"].dropna().iloc[-60:]
    return LinearRegressionResult(
        slope=round(float(slope), 4),
        slope_pct=round(slope_pct, 4),
        r_squared=round(r_squared, 4),
        predicted_30d=round(predicted_30d, 2),
        signal=signal,
        confidence=round(r_squared, 4),
        regression_series=[round(v, 2) for v in regression_line],
        price_series=[round(float(v), 2) for v in close.values],
        dates=[str(d.date()) for d in close.index],
    )


# ──────────────────────────────────────────────────────────
# Method 5: Monte Carlo Simulation (GBM)
# ──────────────────────────────────────────────────────────

def compute_monte_carlo(
    df: pd.DataFrame,
    horizon_days: int = 30,
    simulations: int = 1000,
    seed: int = 42,
) -> MonteCarloResult:
    """
    Geometric Brownian Motion Monte Carlo simulation.
    Uses historical daily log-returns for drift and volatility.
    """
    np.random.seed(seed)
    close = df["Close"].dropna()
    log_returns = np.log(close / close.shift(1)).dropna()

    daily_vol = float(log_returns.std())
    daily_drift = float(log_returns.mean())
    annual_vol = daily_vol * np.sqrt(252)
    current_price = float(close.iloc[-1])

    # Simulate paths: shape (simulations, horizon_days)
    rand_shocks = np.random.normal(
        loc=daily_drift - 0.5 * daily_vol**2,
        scale=daily_vol,
        size=(simulations, horizon_days),
    )
    paths = current_price * np.exp(np.cumsum(rand_shocks, axis=1))

    # Final prices at end of horizon
    final_prices = paths[:, -1]
    p10 = float(np.percentile(final_prices, 10))
    p25 = float(np.percentile(final_prices, 25))
    p50 = float(np.percentile(final_prices, 50))
    p75 = float(np.percentile(final_prices, 75))
    p90 = float(np.percentile(final_prices, 90))
    prob_profit = float(np.mean(final_prices > current_price) * 100)
    expected_return_pct = float((p50 - current_price) / current_price * 100)

    if prob_profit >= 70 and expected_return_pct >= 5:
        signal = STRONG_BUY
    elif prob_profit >= 55:
        signal = BUY
    elif prob_profit <= 30 and expected_return_pct <= -5:
        signal = STRONG_SELL
    elif prob_profit <= 45:
        signal = SELL
    else:
        signal = HOLD

    # Fan chart: daily percentiles over the horizon
    today = datetime.now()
    fan_dates = [(today + timedelta(days=i + 1)).strftime("%Y-%m-%d") for i in range(horizon_days)]
    fan_p10 = [round(float(np.percentile(paths[:, i], 10)), 2) for i in range(horizon_days)]
    fan_p25 = [round(float(np.percentile(paths[:, i], 25)), 2) for i in range(horizon_days)]
    fan_p50 = [round(float(np.percentile(paths[:, i], 50)), 2) for i in range(horizon_days)]
    fan_p75 = [round(float(np.percentile(paths[:, i], 75)), 2) for i in range(horizon_days)]
    fan_p90 = [round(float(np.percentile(paths[:, i], 90)), 2) for i in range(horizon_days)]

    return MonteCarloResult(
        current_price=round(current_price, 2),
        simulations=simulations,
        horizon_days=horizon_days,
        p10=round(p10, 2),
        p25=round(p25, 2),
        p50=round(p50, 2),
        p75=round(p75, 2),
        p90=round(p90, 2),
        expected_return_pct=round(expected_return_pct, 2),
        prob_profit=round(prob_profit, 1),
        daily_volatility=round(daily_vol, 4),
        annual_volatility=round(annual_vol, 4),
        signal=signal,
        fan_dates=fan_dates,
        fan_p10=fan_p10,
        fan_p25=fan_p25,
        fan_p50=fan_p50,
        fan_p75=fan_p75,
        fan_p90=fan_p90,
    )


# ──────────────────────────────────────────────────────────
# Composite Score
# ──────────────────────────────────────────────────────────

def compute_composite(
    rsi: RSIResult,
    macd: MACDResult,
    bollinger: BollingerResult,
    lr: LinearRegressionResult,
    mc: MonteCarloResult,
) -> CompositeResult:
    """Weighted composite of all 5 signals → 0-100 score."""
    weights = {"rsi": 0.20, "macd": 0.25, "bollinger": 0.20, "lr": 0.20, "mc": 0.15}

    rsi_s = float(SIGNAL_SCORES.get(rsi.signal, 50))
    macd_s = float(SIGNAL_SCORES.get(macd.signal, 50))
    bb_s = float(SIGNAL_SCORES.get(bollinger.signal, 50))
    lr_s = float(SIGNAL_SCORES.get(lr.signal, 50))
    mc_s = float(SIGNAL_SCORES.get(mc.signal, 50))

    score = (
        weights["rsi"] * rsi_s
        + weights["macd"] * macd_s
        + weights["bollinger"] * bb_s
        + weights["lr"] * lr_s
        + weights["mc"] * mc_s
    )

    if score >= 80:
        signal = STRONG_BUY
        summary = f"All indicators align bullish. Score {score:.0f}/100 — strong upside bias."
    elif score >= 62:
        signal = BUY
        summary = f"Majority of indicators are bullish. Score {score:.0f}/100 — cautious accumulation."
    elif score >= 38:
        signal = HOLD
        summary = f"Mixed signals. Score {score:.0f}/100 — no clear directional bias, hold position."
    elif score >= 20:
        signal = SELL
        summary = f"Majority of indicators are bearish. Score {score:.0f}/100 — consider reducing exposure."
    else:
        signal = STRONG_SELL
        summary = f"All indicators align bearish. Score {score:.0f}/100 — significant downside risk."

    return CompositeResult(
        score=round(score, 1),
        signal=signal,
        rsi_score=rsi_s,
        macd_score=macd_s,
        bollinger_score=bb_s,
        lr_score=lr_s,
        monte_carlo_score=mc_s,
        summary=summary,
    )


# ──────────────────────────────────────────────────────────
# Main Entry Point
# ──────────────────────────────────────────────────────────

def analyse_holding(
    symbol: str,
    avg_buy_price: float = 0.0,
) -> StockAnalysis:
    """
    Run all 5 prediction methods on a single holding.

    Args:
        symbol: Zerodha tradingsymbol (e.g. 'ITBEES')
        avg_buy_price: Broker average price for context

    Returns:
        StockAnalysis with all method results populated.
    """
    yf_ticker = TICKER_MAP.get(symbol, f"{symbol}.NS")
    logger.info("Analysing {} ({})", symbol, yf_ticker)

    df = _fetch_history(yf_ticker)

    if df is None or len(df) < 35:
        # Fallback: try .BO if .NS failed
        alt_ticker = yf_ticker.replace(".NS", ".BO").replace(".BO", ".NS")
        if alt_ticker != yf_ticker:
            logger.info("Trying fallback ticker {}", alt_ticker)
            df = _fetch_history(alt_ticker)
            if df is not None:
                yf_ticker = alt_ticker

    if df is None or len(df) < 35:
        return StockAnalysis(
            symbol=symbol,
            yf_ticker=yf_ticker,
            current_price=0.0,
            avg_buy_price=avg_buy_price,
            data_start="",
            data_end="",
            data_points=0,
            error=f"Insufficient market data for {symbol}. yfinance returned < 35 rows.",
        )

    cur_price = float(df["Close"].iloc[-1])
    result = StockAnalysis(
        symbol=symbol,
        yf_ticker=yf_ticker,
        current_price=round(cur_price, 2),
        avg_buy_price=round(avg_buy_price, 2),
        data_start=str(df.index[0].date()),
        data_end=str(df.index[-1].date()),
        data_points=len(df),
        ohlcv=df,
    )

    try:
        result.rsi = compute_rsi(df)
    except Exception as exc:
        logger.error("RSI failed for {}: {}", symbol, exc)

    try:
        result.macd = compute_macd(df)
    except Exception as exc:
        logger.error("MACD failed for {}: {}", symbol, exc)

    try:
        result.bollinger = compute_bollinger(df)
    except Exception as exc:
        logger.error("Bollinger failed for {}: {}", symbol, exc)

    try:
        result.linear_regression = compute_linear_regression(df)
    except Exception as exc:
        logger.error("LinearRegression failed for {}: {}", symbol, exc)

    try:
        result.monte_carlo = compute_monte_carlo(df)
    except Exception as exc:
        logger.error("MonteCarlo failed for {}: {}", symbol, exc)

    if all([result.rsi, result.macd, result.bollinger, result.linear_regression, result.monte_carlo]):
        try:
            result.composite = compute_composite(
                result.rsi, result.macd, result.bollinger,
                result.linear_regression, result.monte_carlo,
            )
        except Exception as exc:
            logger.error("Composite score failed for {}: {}", symbol, exc)

    logger.success("Analysis complete for {} — composite: {}",
                   symbol, result.composite.signal if result.composite else "N/A")
    return result


def analyse_all_holdings(holdings: list[dict]) -> dict[str, StockAnalysis]:
    """Analyse all holdings. holdings is list of {symbol, avg_price} dicts."""
    return {
        h["symbol"]: analyse_holding(h["symbol"], h.get("avg_price", 0.0))
        for h in holdings
    }
