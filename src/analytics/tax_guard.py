"""
PortfolioIQ — Tax Guard
STCG/LTCG classification engine for Indian equity taxation.

Indian tax rules (equity, as of FY 2025-26):
    - LTCG: Holding period > 12 months. Tax @ 12.5% above Rs 1.25 lakh exemption.
    - STCG: Holding period <= 12 months. Tax @ 20%.
    - Grandfathering (pre-31-Jan-2018 purchases) is NOT handled here.

This module:
    1. Loads FIFO tax lots from holding_tax_lots
    2. Classifies each lot as STCG or LTCG based on buy_date vs today
    3. Computes potential tax liability if a holding were sold
    4. Issues warnings when a sell order would trigger STCG within N days of LTCG cutoff
    5. Reports lots nearing LTCG conversion (the "tax bomb" window)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from loguru import logger

from src.db.connection import execute_sql


# ============================================================
# Constants
# ============================================================

LTCG_HOLDING_DAYS = 365  # days for equity LTCG classification
STCG_TAX_RATE = Decimal("0.20")      # 20% STCG on equity
LTCG_TAX_RATE = Decimal("0.125")     # 12.5% LTCG on equity
LTCG_EXEMPTION = Decimal("125000")   # Rs 1.25 lakh LTCG exemption per FY
STCG_WARNING_WINDOW_DAYS = 30        # warn if selling within 30 days of LTCG cutoff


# ============================================================
# Data Classes
# ============================================================

@dataclass
class TaxLot:
    """A single FIFO tax lot."""
    lot_id: int
    holding_id: int
    buy_date: date
    buy_price: Decimal
    quantity: int
    remaining_quantity: int
    days_held: int = 0
    tax_type: str = "STCG"  # 'STCG' | 'LTCG'
    days_to_ltcg: int | None = None  # None if already LTCG

    def __post_init__(self):
        today = date.today()
        self.days_held = (today - self.buy_date).days
        if self.days_held > LTCG_HOLDING_DAYS:
            self.tax_type = "LTCG"
            self.days_to_ltcg = None
        else:
            self.tax_type = "STCG"
            self.days_to_ltcg = LTCG_HOLDING_DAYS - self.days_held

    @property
    def is_near_ltcg(self) -> bool:
        """True if this lot will convert to LTCG within the warning window."""
        return (
            self.tax_type == "STCG"
            and self.days_to_ltcg is not None
            and self.days_to_ltcg <= STCG_WARNING_WINDOW_DAYS
        )


@dataclass
class HoldingTaxProfile:
    """Tax profile for a single holding."""
    tradingsymbol: str
    exchange: str
    instrument_token: int
    current_price: Decimal
    lots: list[TaxLot] = field(default_factory=list)

    # Aggregated
    ltcg_quantity: int = 0
    ltcg_cost_basis: Decimal = Decimal("0")
    stcg_quantity: int = 0
    stcg_cost_basis: Decimal = Decimal("0")
    near_ltcg_quantity: int = 0
    next_ltcg_date: date | None = None

    # Estimated tax liability if fully sold
    estimated_stcg_tax: Decimal = Decimal("0")
    estimated_ltcg_tax: Decimal = Decimal("0")
    total_tax_liability: Decimal = Decimal("0")

    def compute_aggregates(self):
        """Compute aggregated tax metrics from lots."""
        for lot in self.lots:
            if lot.remaining_quantity <= 0:
                continue
            if lot.tax_type == "LTCG":
                self.ltcg_quantity += lot.remaining_quantity
                self.ltcg_cost_basis += lot.buy_price * lot.remaining_quantity
            else:
                self.stcg_quantity += lot.remaining_quantity
                self.stcg_cost_basis += lot.buy_price * lot.remaining_quantity
                if lot.is_near_ltcg:
                    self.near_ltcg_quantity += lot.remaining_quantity
                    lot_ltcg_date = lot.buy_date + timedelta(days=LTCG_HOLDING_DAYS)
                    if self.next_ltcg_date is None or lot_ltcg_date < self.next_ltcg_date:
                        self.next_ltcg_date = lot_ltcg_date

        # Estimate tax if everything were sold at current price
        if self.stcg_quantity > 0:
            stcg_gain = (self.current_price * self.stcg_quantity) - self.stcg_cost_basis
            if stcg_gain > 0:
                self.estimated_stcg_tax = (stcg_gain * STCG_TAX_RATE).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

        if self.ltcg_quantity > 0:
            ltcg_gain = (self.current_price * self.ltcg_quantity) - self.ltcg_cost_basis
            if ltcg_gain > 0:
                # Apply Rs 1.25 lakh exemption (simplified — per holding, not per FY)
                taxable_ltcg = max(ltcg_gain - LTCG_EXEMPTION, Decimal("0"))
                self.estimated_ltcg_tax = (taxable_ltcg * LTCG_TAX_RATE).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

        self.total_tax_liability = self.estimated_stcg_tax + self.estimated_ltcg_tax


@dataclass
class TaxWarning:
    """A warning about a potential tax-adverse action."""
    tradingsymbol: str
    warning_type: str  # 'NEAR_LTCG' | 'STCG_SELL' | 'HIGH_TAX'
    message: str
    severity: str  # 'LOW' | 'MEDIUM' | 'HIGH'
    details: dict[str, Any] = field(default_factory=dict)


# ============================================================
# Tax Guard Engine
# ============================================================

def load_tax_lots(user_id: str = "default") -> dict[str, HoldingTaxProfile]:
    """
    Load all tax lots for current holdings.
    Returns dict keyed by tradingsymbol → HoldingTaxProfile.
    """
    rows = execute_sql("""
        SELECT
            h.tradingsymbol,
            h.exchange,
            h.instrument_token,
            COALESCE(lp.last_price, h.last_price, h.close_price, 0) AS current_price,
            tl.id AS lot_id,
            tl.holding_id,
            tl.buy_date,
            tl.buy_price,
            tl.quantity,
            tl.remaining_quantity
        FROM user_holdings h
        JOIN holding_tax_lots tl ON h.id = tl.holding_id
        LEFT JOIN live_prices lp ON h.instrument_token = lp.instrument_token
        WHERE h.user_id = :uid
          AND tl.remaining_quantity > 0
        ORDER BY h.tradingsymbol, tl.buy_date ASC
    """, {"uid": user_id})

    profiles: dict[str, HoldingTaxProfile] = {}
    for row in rows:
        sym = row["tradingsymbol"]
        if sym not in profiles:
            profiles[sym] = HoldingTaxProfile(
                tradingsymbol=sym,
                exchange=row["exchange"],
                instrument_token=row["instrument_token"],
                current_price=Decimal(str(row["current_price"])),
            )

        lot = TaxLot(
            lot_id=row["lot_id"],
            holding_id=row["holding_id"],
            buy_date=row["buy_date"],
            buy_price=Decimal(str(row["buy_price"])),
            quantity=row["quantity"],
            remaining_quantity=row["remaining_quantity"],
        )
        profiles[sym].lots.append(lot)

    for p in profiles.values():
        p.compute_aggregates()

    return profiles


def check_sell_tax_impact(
    tradingsymbol: str,
    sell_quantity: int,
    user_id: str = "default",
) -> list[TaxWarning]:
    """
    Check the tax impact of selling a specific quantity of a holding.
    Uses FIFO ordering to determine which lots would be sold.

    Args:
        tradingsymbol: Stock to sell.
        sell_quantity: Number of shares to sell.
        user_id: Portfolio owner.

    Returns:
        List of TaxWarning objects.
    """
    profiles = load_tax_lots(user_id)
    profile = profiles.get(tradingsymbol)
    warnings: list[TaxWarning] = []

    if profile is None:
        return warnings

    remaining_to_sell = sell_quantity
    stcg_lots_sold = 0
    stcg_gain = Decimal("0")
    near_ltcg_lots_sold = 0

    for lot in profile.lots:  # already sorted by buy_date (FIFO)
        if remaining_to_sell <= 0:
            break

        sold_from_lot = min(remaining_to_sell, lot.remaining_quantity)
        remaining_to_sell -= sold_from_lot

        if lot.tax_type == "STCG":
            stcg_lots_sold += sold_from_lot
            gain = (profile.current_price - lot.buy_price) * sold_from_lot
            stcg_gain += gain

            if lot.is_near_ltcg:
                near_ltcg_lots_sold += sold_from_lot
                warnings.append(TaxWarning(
                    tradingsymbol=tradingsymbol,
                    warning_type="NEAR_LTCG",
                    message=(
                        f"Selling {sold_from_lot} shares bought on {lot.buy_date} "
                        f"which would convert to LTCG in {lot.days_to_ltcg} days. "
                        f"Consider waiting to save ~{float((gain * (STCG_TAX_RATE - LTCG_TAX_RATE)).quantize(Decimal('0.01')))} in tax."
                    ),
                    severity="HIGH" if lot.days_to_ltcg <= 7 else "MEDIUM",
                    details={
                        "buy_date": str(lot.buy_date),
                        "days_to_ltcg": lot.days_to_ltcg,
                        "quantity": sold_from_lot,
                        "potential_tax_saving": float(
                            (gain * (STCG_TAX_RATE - LTCG_TAX_RATE)).quantize(Decimal("0.01"))
                        ),
                    },
                ))

    # Summary warning for STCG
    if stcg_lots_sold > 0 and stcg_gain > 0:
        estimated_tax = (stcg_gain * STCG_TAX_RATE).quantize(Decimal("0.01"))
        warnings.append(TaxWarning(
            tradingsymbol=tradingsymbol,
            warning_type="STCG_SELL",
            message=(
                f"Selling {stcg_lots_sold} shares at STCG rate (20%). "
                f"Estimated STCG tax: Rs {float(estimated_tax):,.2f} "
                f"on gain of Rs {float(stcg_gain):,.2f}."
            ),
            severity="MEDIUM",
            details={
                "stcg_quantity": stcg_lots_sold,
                "stcg_gain": float(stcg_gain),
                "estimated_tax": float(estimated_tax),
            },
        ))

    return warnings


def get_tax_summary(user_id: str = "default") -> dict[str, Any]:
    """JSON-friendly tax summary for the dashboard."""
    profiles = load_tax_lots(user_id)

    total_stcg_quantity = 0
    total_ltcg_quantity = 0
    total_tax_liability = Decimal("0")
    near_ltcg_holdings = []

    holdings_tax = []
    for sym, p in profiles.items():
        total_stcg_quantity += p.stcg_quantity
        total_ltcg_quantity += p.ltcg_quantity
        total_tax_liability += p.total_tax_liability

        if p.near_ltcg_quantity > 0:
            near_ltcg_holdings.append({
                "symbol": sym,
                "quantity": p.near_ltcg_quantity,
                "next_ltcg_date": str(p.next_ltcg_date) if p.next_ltcg_date else None,
            })

        holdings_tax.append({
            "symbol": sym,
            "ltcg_qty": p.ltcg_quantity,
            "ltcg_cost_basis": float(p.ltcg_cost_basis),
            "stcg_qty": p.stcg_quantity,
            "stcg_cost_basis": float(p.stcg_cost_basis),
            "estimated_stcg_tax": float(p.estimated_stcg_tax),
            "estimated_ltcg_tax": float(p.estimated_ltcg_tax),
            "total_tax": float(p.total_tax_liability),
            "near_ltcg_qty": p.near_ltcg_quantity,
            "next_ltcg_date": str(p.next_ltcg_date) if p.next_ltcg_date else None,
        })

    return {
        "total_stcg_quantity": total_stcg_quantity,
        "total_ltcg_quantity": total_ltcg_quantity,
        "total_tax_liability": float(total_tax_liability),
        "near_ltcg_holdings": near_ltcg_holdings,
        "holdings": holdings_tax,
    }


if __name__ == "__main__":
    """Test: python -m src.analytics.tax_guard"""
    import json
    print(json.dumps(get_tax_summary(), indent=2))
