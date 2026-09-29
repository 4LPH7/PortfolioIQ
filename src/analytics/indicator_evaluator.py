"""
PortfolioIQ — Quantitative Indicator Evaluator & Spearman IC Engine
Phase 5: Make the Signal Engine Evidence-Based

Standardizes technical indicators into continuous factor scores bounded in [-1.0, +1.0],
computes Spearman rank Information Coefficients (IC) against forward returns,
enforces statistical pruning gates (IC <= 0 or p > 0.05), and dynamically weights
unpruned indicators proportional to their Information Ratio (IR = mean(IC) / std(IC)).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from scipy import stats

from src.models.dtos import IndicatorEvaluationDTO

# ──────────────────────────────────────────────────────────
# Standardized Factor Generators (Bounded in [-1.0, +1.0])
# ──────────────────────────────────────────────────────────


def compute_rsi_factor(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Computes Wilder's RSI and maps it into a mean-reverting factor bounded in [-1.0, +1.0].

    Mapping convention:
    - RSI < 30 (Oversold): Positive factor (+0.4 to +1.0, bullish rebound potential).
    - RSI = 50 (Neutral): Factor 0.0.
    - RSI > 70 (Overbought): Negative factor (-0.4 to -1.0, bearish pullback potential).

    Formula: (50.0 - RSI) / 50.0, clipped to [-1.0, +1.0].
    """
    if df.empty or "Close" not in df.columns:
        return pd.Series(dtype=float)

    close = df["Close"].astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    # Wilder's smoothing via exponential moving average (alpha = 1 / period)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rsi = pd.Series(50.0, index=close.index, dtype=float)
    has_loss = avg_loss > 0.0
    rsi.loc[has_loss] = 100.0 - (100.0 / (1.0 + avg_gain.loc[has_loss] / avg_loss.loc[has_loss]))
    rsi.loc[(avg_loss == 0.0) & (avg_gain > 0.0)] = 100.0

    factor = (50.0 - rsi) / 50.0
    return factor.fillna(0.0).clip(lower=-1.0, upper=1.0)


def compute_macd_factor(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.Series:
    """
    Computes standard 12/26/9 MACD histogram and standardizes it by rolling volatility,
    scaled via hyperbolic tangent tanh() into [-1.0, +1.0].

    Mapping convention:
    - MACD Histogram > 0 (Bullish momentum): Positive factor (0.0 to +1.0).
    - MACD Histogram < 0 (Bearish momentum): Negative factor (0.0 to -1.0).
    - Normalized by 60-day rolling standard deviation to maintain stability across regimes.
    """
    if df.empty or "Close" not in df.columns:
        return pd.Series(dtype=float)

    close = df["Close"].astype(float)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    macd_hist = macd_line - signal_line

    # Volatility normalization: 60-day rolling standard deviation
    rolling_std = macd_hist.rolling(window=60, min_periods=10).std()
    series_std = float(macd_hist.std()) if not np.isnan(macd_hist.std()) else 1.0
    if series_std <= 1e-6:
        series_std = 1.0

    norm_scale = rolling_std.fillna(series_std).replace(0.0, series_std)
    factor = np.tanh(macd_hist / (norm_scale + 1e-6))
    return pd.Series(factor, index=close.index, dtype=float).fillna(0.0).clip(-1.0, 1.0)


def compute_bollinger_factor(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.Series:
    """
    Computes Bollinger Bands %B and linearly maps it into a mean-reverting factor [-1.0, +1.0].

    Mapping convention:
    - %B = 0.0 (Price at Lower Band): Factor = +1.0 (Oversold, bullish bounce).
    - %B = 0.5 (Price at Middle SMA): Factor = 0.0 (Neutral equilibrium).
    - %B = 1.0 (Price at Upper Band): Factor = -1.0 (Overbought, bearish reversal).

    Formula: 1.0 - 2.0 * %B, clipped to [-1.0, +1.0].
    """
    if df.empty or "Close" not in df.columns:
        return pd.Series(dtype=float)

    close = df["Close"].astype(float)
    sma = close.rolling(window=period, min_periods=period).mean()
    rolling_std = close.rolling(window=period, min_periods=period).std()
    upper = sma + std_dev * rolling_std
    lower = sma - std_dev * rolling_std
    bandwidth = upper - lower

    percent_b = (close - lower) / bandwidth.replace(0.0, np.nan)
    percent_b = percent_b.fillna(0.5)

    factor = 1.0 - 2.0 * percent_b
    return factor.fillna(0.0).clip(lower=-1.0, upper=1.0)


def compute_momentum_factor(df: pd.DataFrame, lookback: int = 30) -> pd.Series:
    """
    Computes rolling OLS linear regression slope t-statistic over the lookback window,
    scaled via hyperbolic tangent tanh(t_stat / 2.0) into [-1.0, +1.0].

    Mapping convention:
    - Persistent upward trend (High positive t-statistic): Positive factor (+0.5 to +1.0).
    - Flat / zero trend (t-statistic near 0): Factor 0.0.
    - Persistent downward trend (High negative t-statistic): Negative factor (-0.5 to -1.0).
    """
    if df.empty or "Close" not in df.columns:
        return pd.Series(dtype=float)

    close = df["Close"].astype(float)
    n_bars = len(close)
    if n_bars < lookback:
        return pd.Series(0.0, index=close.index, dtype=float)

    N = lookback
    weights = np.arange(N, dtype=float)
    x_mean = (N - 1.0) / 2.0
    Sxx = N * (N**2 - 1.0) / 12.0

    prices = close.values
    conv = np.convolve(prices, weights[::-1], mode="valid")
    padded_conv = np.full(n_bars, np.nan)
    padded_conv[N - 1 :] = conv

    sum_y = close.rolling(N, min_periods=N).sum()
    Sxy = padded_conv - x_mean * sum_y
    slope = Sxy / Sxx

    roll_var = close.rolling(N, min_periods=N).var(ddof=1)
    Syy = (N - 1.0) * roll_var
    RSS = np.maximum(0.0, Syy - (slope**2) * Sxx)
    se = np.sqrt(RSS / ((N - 2.0) * Sxx))

    # Determine t-statistic, accounting for zero standard error in exact linear trends
    t_stat = np.where(
        se > 1e-9,
        slope / se,
        np.where(slope > 1e-9, 50.0, np.where(slope < -1e-9, -50.0, 0.0)),
    )

    factor = np.tanh(t_stat / 2.0)
    return pd.Series(factor, index=close.index, dtype=float).fillna(0.0).clip(-1.0, 1.0)


def compute_all_factors(df: pd.DataFrame) -> dict[str, pd.Series]:
    """
    Convenience orchestrator returning all four standardized continuous factor series
    bounded in [-1.0, +1.0].
    """
    return {
        "RSI": compute_rsi_factor(df),
        "MACD": compute_macd_factor(df),
        "BOLLINGER": compute_bollinger_factor(df),
        "LR_MOMENTUM": compute_momentum_factor(df),
    }


def compute_forward_returns(df_or_close: pd.DataFrame | pd.Series, horizon: int = 20) -> pd.Series:
    """
    Computes shifted forward returns: (Price_{t+h} - Price_t) / Price_t.
    Ensures strict zero-lookahead semantics: at index t, represents future realized return.
    """
    if isinstance(df_or_close, pd.DataFrame):
        close = df_or_close["Close"].astype(float)
    else:
        close = df_or_close.astype(float)

    return close.pct_change(horizon).shift(-horizon)


# ──────────────────────────────────────────────────────────
# Statistical Information Coefficient (IC) & Pruning Engine
# ──────────────────────────────────────────────────────────


def compute_spearman_ic(
    factor_scores: pd.Series,
    forward_returns: pd.Series,
) -> tuple[float, float]:
    """
    Computes Spearman rank Information Coefficient (IC) and two-tailed p-value
    between factor scores and forward returns.

    Guarantees:
    - Drops NaNs and infinite values.
    - Requires at least 15 valid paired observations (returns (0.0, 1.0) if fewer).
    - Detects constant / zero-variance series (returns (0.0, 1.0)).
    - Returns (float(ic), float(p_value)).
    """
    if factor_scores is None or forward_returns is None:
        return 0.0, 1.0

    paired = pd.concat([factor_scores, forward_returns], axis=1).dropna()
    paired = paired[~np.isinf(paired.iloc[:, 0]) & ~np.isinf(paired.iloc[:, 1])]

    if len(paired) < 15:
        return 0.0, 1.0

    s1 = paired.iloc[:, 0]
    s2 = paired.iloc[:, 1]

    if s1.nunique() <= 1 or s2.nunique() <= 1:
        return 0.0, 1.0

    res = stats.spearmanr(s1, s2)
    stat = float(res.statistic) if not np.isnan(res.statistic) else 0.0
    pval = float(res.pvalue) if not np.isnan(res.pvalue) else 1.0
    return stat, pval


def evaluate_indicators(
    factor_slices: dict[str, list[pd.Series] | pd.Series],
    forward_return_slices: list[pd.Series] | pd.Series,
    in_sample_factor_slices: dict[str, list[pd.Series] | pd.Series] | None = None,
    in_sample_return_slices: list[pd.Series] | pd.Series | None = None,
    current_factors: dict[str, float] | None = None,
) -> list[IndicatorEvaluationDTO]:
    """
    Evaluates independent indicators across out-of-sample cross-validation folds:
    1. Computes fold-by-fold Spearman rank IC and two-tailed p-value.
    2. Calculates out-of-sample mean IC, sample std(IC), and Information Ratio (IR = mean(IC) / std(IC)).
    3. Enforces the statistical pruning hurdle:
       Pruned if mean(IC) <= 0 or mean(p_value) > 0.05 or IR <= 0.
    4. Dynamically weights valid unpruned indicators proportional to IR:
       weight = IR_i / sum(IR_valid), normalized to sum to 1.0.
    5. Returns list of IndicatorEvaluationDTOs.
    """
    # Normalize out-of-sample slices to lists
    if isinstance(forward_return_slices, pd.Series):
        fwd_slices = [forward_return_slices]
    else:
        fwd_slices = list(forward_return_slices)

    norm_factor_slices: dict[str, list[pd.Series]] = {}
    for name, s in factor_slices.items():
        if isinstance(s, pd.Series):
            norm_factor_slices[name] = [s]
        else:
            norm_factor_slices[name] = list(s)

    # Normalize optional in-sample slices
    norm_in_factor_slices: dict[str, list[pd.Series]] = {}
    norm_in_return_slices: list[pd.Series] = []
    if in_sample_factor_slices is not None and in_sample_return_slices is not None:
        if isinstance(in_sample_return_slices, pd.Series):
            norm_in_return_slices = [in_sample_return_slices]
        else:
            norm_in_return_slices = list(in_sample_return_slices)
        for name, s in in_sample_factor_slices.items():
            if isinstance(s, pd.Series):
                norm_in_factor_slices[name] = [s]
            else:
                norm_in_factor_slices[name] = list(s)

    evaluations: list[IndicatorEvaluationDTO] = []
    valid_irs: dict[str, float] = {}

    for name, slices in norm_factor_slices.items():
        out_ics: list[float] = []
        out_pvals: list[float] = []

        n_folds = min(len(slices), len(fwd_slices))
        for i in range(n_folds):
            ic, p = compute_spearman_ic(slices[i], fwd_slices[i])
            out_ics.append(ic)
            out_pvals.append(p)

        # In-sample metrics if available
        in_ics: list[float] = []
        in_pvals: list[float] = []
        if name in norm_in_factor_slices and norm_in_return_slices:
            in_slices = norm_in_factor_slices[name]
            for i in range(min(len(in_slices), len(norm_in_return_slices))):
                ic, p = compute_spearman_ic(in_slices[i], norm_in_return_slices[i])
                in_ics.append(ic)
                in_pvals.append(p)

        mean_ic = float(np.mean(out_ics)) if out_ics else 0.0
        if len(out_ics) > 1:
            std_ic = float(np.std(out_ics, ddof=1))
        else:
            std_ic = 0.0

        mean_p = float(np.mean(out_pvals)) if out_pvals else 1.0

        # Information Ratio calculation
        if len(out_ics) == 1:
            ir = mean_ic if mean_ic > 0.0 else 0.0
        else:
            ir = mean_ic / std_ic if std_ic > 1e-6 else (mean_ic / 1e-4 if mean_ic > 0.0 else 0.0)

        # Statistical Pruning Hurdle (D-04)
        is_pruned = False
        prune_reason = None
        if mean_ic <= 0.0:
            is_pruned = True
            prune_reason = f"Non-positive out-of-sample IC ({mean_ic:.4f} <= 0)"
        elif mean_p > 0.05:
            is_pruned = True
            prune_reason = f"Statistical insignificance (p={mean_p:.4f} > 0.05)"
        elif ir <= 0.0:
            is_pruned = True
            prune_reason = f"Non-positive Information Ratio ({ir:.4f} <= 0)"

        if not is_pruned and ir > 0.0:
            valid_irs[name] = ir

        # Map current factor to [0.0, 100.0] score if provided
        if current_factors is not None and name in current_factors:
            raw_factor = float(current_factors[name])
            score = float(np.clip(round((raw_factor + 1.0) * 50.0, 2), 0.0, 100.0))
        else:
            score = 50.0

        evaluations.append(
            IndicatorEvaluationDTO(
                name=name,
                in_sample_ic=round(float(np.mean(in_ics)), 4) if in_ics else None,
                in_sample_p_value=round(float(np.mean(in_pvals)), 4) if in_pvals else None,
                out_sample_ic=round(out_ics[-1], 4) if out_ics else None,
                out_sample_p_value=round(out_pvals[-1], 4) if out_pvals else None,
                mean_ic=round(mean_ic, 4),
                std_ic=round(std_ic, 4),
                information_ratio=round(ir, 4),
                p_value=round(mean_p, 4),
                weight=0.0,
                is_pruned=is_pruned,
                prune_reason=prune_reason,
                score=score,
            )
        )

    # Dynamic Weighting normalized to sum to 1.0
    sum_ir = sum(valid_irs.values())
    if sum_ir > 0.0:
        unpruned_evals = [ev for ev in evaluations if not ev.is_pruned]
        raw_weights = [valid_irs[ev.name] / sum_ir for ev in unpruned_evals]
        # Round and adjust rounding discrepancy on the maximum weight
        rounded_weights = [round(w, 4) for w in raw_weights]
        discrepancy = round(1.0 - sum(rounded_weights), 4)
        if unpruned_evals:
            max_idx = int(np.argmax(rounded_weights))
            rounded_weights[max_idx] = round(rounded_weights[max_idx] + discrepancy, 4)

        for ev, w in zip(unpruned_evals, rounded_weights, strict=True):
            ev.weight = w
    else:
        logger.info("All indicators pruned by statistical hurdle; assigning zero weights")
        for ev in evaluations:
            ev.weight = 0.0

    return evaluations


def compute_composite_score(
    evaluations: list[IndicatorEvaluationDTO],
    current_factors: dict[str, float] | None = None,
) -> tuple[float, str]:
    """
    Computes evidence-weighted composite score from evaluated indicators.

    Returns:
    (composite_score, signal_status_or_label)

    Rules:
    - If all indicators are pruned (sum of valid weights == 0):
      Returns (50.0, "UNPROVEN_NOISE").
    - If valid indicators exist:
      Score = sum(weight_i * score_i) where score_i is mapped to [0, 100].
      If Score >= 60.0: (Score, "BUY")
      If Score <= 40.0: (Score, "SELL")
      Else: (Score, "HOLD")
    """
    valid_evals = [ev for ev in evaluations if not ev.is_pruned and ev.weight > 0.0]
    if not valid_evals:
        return 50.0, "UNPROVEN_NOISE"

    total_weight = sum(ev.weight for ev in valid_evals)
    if total_weight <= 0.0:
        return 50.0, "UNPROVEN_NOISE"

    score_sum = 0.0
    for ev in valid_evals:
        if current_factors is not None and ev.name in current_factors:
            factor_val = current_factors[ev.name]
            ind_score = (factor_val + 1.0) * 50.0
        else:
            ind_score = ev.score
        score_sum += ev.weight * ind_score

    composite_score = float(np.clip(round(score_sum / total_weight, 2), 0.0, 100.0))

    if composite_score >= 60.0:
        signal_label = "BUY"
    elif composite_score <= 40.0:
        signal_label = "SELL"
    else:
        signal_label = "HOLD"

    return composite_score, signal_label
