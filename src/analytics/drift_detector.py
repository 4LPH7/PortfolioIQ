"""
PortfolioIQ — Drift Detector
Compares current portfolio allocation against target weights
and flags holdings/sectors that have drifted beyond thresholds.

Drift types:
    1. SECTOR DRIFT — sector weight vs target sector allocation
    2. HOLDING DRIFT — single holding weight vs concentration limit
    3. CASH DRIFT — cash as % of net worth vs target cash buffer

Output:
    List of DriftSignal objects that the Rebalancer consumes to
    generate corrective orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any

from loguru import logger

from src.analytics.valuator import compute_portfolio_valuation, PortfolioValuation
from src.db.connection import execute_sql


class DriftType(str, Enum):
    SECTOR = "SECTOR"
    HOLDING = "HOLDING"
    CASH = "CASH"


class DriftDirection(str, Enum):
    OVERWEIGHT = "OVERWEIGHT"
    UNDERWEIGHT = "UNDERWEIGHT"


@dataclass
class DriftSignal:
    """A single drift detection result."""
    drift_type: DriftType
    name: str  # sector name or tradingsymbol
    current_weight_pct: Decimal
    target_weight_pct: Decimal
    drift_pct: Decimal  # current - target (positive = overweight)
    threshold_pct: Decimal
    direction: DriftDirection
    severity: str  # 'LOW' | 'MEDIUM' | 'HIGH'
    current_value: Decimal = Decimal("0")
    target_value: Decimal = Decimal("0")
    rebalance_amount: Decimal = Decimal("0")  # + = buy, - = sell

    @property
    def is_actionable(self) -> bool:
        """True if drift exceeds threshold and warrants a rebalance order."""
        return abs(self.drift_pct) > self.threshold_pct


def _classify_severity(drift_pct: Decimal, threshold_pct: Decimal) -> str:
    """Classify drift severity based on how far past the threshold it is."""
    abs_drift = abs(drift_pct)
    if abs_drift <= threshold_pct:
        return "LOW"  # within tolerance
    elif abs_drift <= threshold_pct * 2:
        return "MEDIUM"
    else:
        return "HIGH"


def _get_active_targets(user_id: str = "default") -> dict[str, dict]:
    """
    Load active allocation targets from the database.
    Returns dict keyed by (allocation_type, name) → target info.
    """
    rows = execute_sql("""
        SELECT
            ta.allocation_type,
            ta.sector,
            ta.tradingsymbol,
            ta.target_weight_pct,
            ta.drift_threshold_pct,
            ta.min_weight_pct,
            ta.max_weight_pct
        FROM target_allocations ta
        JOIN allocation_profiles ap ON ta.profile_id = ap.id
        WHERE ap.user_id = :uid AND ap.is_active = TRUE
    """, {"uid": user_id})

    targets = {}
    for row in rows:
        alloc_type = row["allocation_type"]
        name = row.get("sector") or row.get("tradingsymbol") or "UNKNOWN"
        key = (alloc_type, name)
        targets[key] = {
            "target_weight_pct": Decimal(str(row["target_weight_pct"])),
            "drift_threshold_pct": Decimal(str(row.get("drift_threshold_pct") or 5)),
            "min_weight_pct": Decimal(str(row.get("min_weight_pct") or 0)),
            "max_weight_pct": Decimal(str(row.get("max_weight_pct") or 100)),
        }

    logger.debug("Loaded {} allocation targets", len(targets))
    return targets


def _get_system_config_value(key: str, default: str = "0") -> Decimal:
    """Get a config value from system_config as Decimal."""
    rows = execute_sql(
        "SELECT value FROM system_config WHERE key = :key",
        {"key": key}
    )
    return Decimal(rows[0]["value"]) if rows else Decimal(default)


def detect_drift(
    user_id: str = "default",
    valuation: PortfolioValuation | None = None,
) -> list[DriftSignal]:
    """
    Run full drift detection across sectors and individual holdings.

    Args:
        user_id: Portfolio owner.
        valuation: Pre-computed valuation (optional, computed if not provided).

    Returns:
        List of DriftSignal objects sorted by severity (HIGH first).
    """
    if valuation is None:
        valuation = compute_portfolio_valuation(user_id)

    targets = _get_active_targets(user_id)
    concentration_limit = _get_system_config_value("concentration_limit_pct", "15.0")

    signals: list[DriftSignal] = []

    # ─── 1. SECTOR DRIFT ─────────────────────────────────────
    if valuation.total_aum > 0:
        for sector, weight_pct in valuation.sector_weights.items():
            target_key = ("SECTOR", sector)
            if target_key in targets:
                t = targets[target_key]
                target_wt = t["target_weight_pct"]
                threshold = t["drift_threshold_pct"]
                drift = weight_pct - target_wt
                direction = DriftDirection.OVERWEIGHT if drift > 0 else DriftDirection.UNDERWEIGHT

                sector_value = (weight_pct / 100) * valuation.total_aum
                target_value = (target_wt / 100) * valuation.total_aum

                signal = DriftSignal(
                    drift_type=DriftType.SECTOR,
                    name=sector,
                    current_weight_pct=weight_pct,
                    target_weight_pct=target_wt,
                    drift_pct=drift,
                    threshold_pct=threshold,
                    direction=direction,
                    severity=_classify_severity(drift, threshold),
                    current_value=sector_value.quantize(Decimal("0.01")),
                    target_value=target_value.quantize(Decimal("0.01")),
                    rebalance_amount=(target_value - sector_value).quantize(Decimal("0.01")),
                )
                signals.append(signal)

    # ─── 2. HOLDING DRIFT (concentration limit) ──────────────
    for h in valuation.holdings:
        if h.weight_pct > concentration_limit:
            drift = h.weight_pct - concentration_limit
            target_value = (concentration_limit / 100) * valuation.total_aum

            signal = DriftSignal(
                drift_type=DriftType.HOLDING,
                name=h.tradingsymbol,
                current_weight_pct=h.weight_pct,
                target_weight_pct=concentration_limit,
                drift_pct=drift,
                threshold_pct=Decimal("0"),  # any breach is a signal
                direction=DriftDirection.OVERWEIGHT,
                severity=_classify_severity(drift, Decimal("5")),
                current_value=h.current_value,
                target_value=target_value.quantize(Decimal("0.01")),
                rebalance_amount=(target_value - h.current_value).quantize(Decimal("0.01")),
            )
            signals.append(signal)

        # Also check per-holding targets
        target_key = ("HOLDING", h.tradingsymbol)
        if target_key in targets:
            t = targets[target_key]
            target_wt = t["target_weight_pct"]
            threshold = t["drift_threshold_pct"]
            drift = h.weight_pct - target_wt
            direction = DriftDirection.OVERWEIGHT if drift > 0 else DriftDirection.UNDERWEIGHT

            target_value = (target_wt / 100) * valuation.total_aum

            signal = DriftSignal(
                drift_type=DriftType.HOLDING,
                name=h.tradingsymbol,
                current_weight_pct=h.weight_pct,
                target_weight_pct=target_wt,
                drift_pct=drift,
                threshold_pct=threshold,
                direction=direction,
                severity=_classify_severity(drift, threshold),
                current_value=h.current_value,
                target_value=target_value.quantize(Decimal("0.01")),
                rebalance_amount=(target_value - h.current_value).quantize(Decimal("0.01")),
            )
            signals.append(signal)

    # ─── 3. CASH DRIFT ───────────────────────────────────────
    if valuation.net_worth > 0:
        cash_weight = (
            (valuation.available_cash / valuation.net_worth) * 100
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        cash_target_key = ("CASH", "CASH")
        if cash_target_key in targets:
            t = targets[cash_target_key]
            target_cash_pct = t["target_weight_pct"]
            threshold = t["drift_threshold_pct"]
            drift = cash_weight - target_cash_pct
            direction = DriftDirection.OVERWEIGHT if drift > 0 else DriftDirection.UNDERWEIGHT

            signal = DriftSignal(
                drift_type=DriftType.CASH,
                name="CASH",
                current_weight_pct=cash_weight,
                target_weight_pct=target_cash_pct,
                drift_pct=drift,
                threshold_pct=threshold,
                direction=direction,
                severity=_classify_severity(drift, threshold),
                current_value=valuation.available_cash,
                target_value=(target_cash_pct / 100 * valuation.net_worth).quantize(Decimal("0.01")),
                rebalance_amount=Decimal("0"),  # cash drift is informational
            )
            signals.append(signal)

    # Sort by severity: HIGH → MEDIUM → LOW
    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    signals.sort(key=lambda s: (severity_order.get(s.severity, 3), -abs(s.drift_pct)))

    actionable = sum(1 for s in signals if s.is_actionable)
    logger.info(
        "Drift detection complete: {} signals ({} actionable)",
        len(signals), actionable
    )
    return signals


def get_drift_summary(user_id: str = "default") -> dict[str, Any]:
    """JSON-friendly drift summary for the dashboard."""
    signals = detect_drift(user_id)
    return {
        "total_signals": len(signals),
        "actionable_signals": sum(1 for s in signals if s.is_actionable),
        "signals": [
            {
                "type": s.drift_type.value,
                "name": s.name,
                "current_weight": float(s.current_weight_pct),
                "target_weight": float(s.target_weight_pct),
                "drift_pct": float(s.drift_pct),
                "threshold_pct": float(s.threshold_pct),
                "direction": s.direction.value,
                "severity": s.severity,
                "rebalance_amount": float(s.rebalance_amount),
                "actionable": s.is_actionable,
            }
            for s in signals
        ],
    }


if __name__ == "__main__":
    """Test: python -m src.analytics.drift_detector"""
    import json
    print(json.dumps(get_drift_summary(), indent=2))
