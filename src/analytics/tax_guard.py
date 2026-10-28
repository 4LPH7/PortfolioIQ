"""
PortfolioIQ — Tax Guard
STCG/LTCG classification, tax-loss harvesting, near-LTCG conversion protection,
and annual FY Rs 1.25L exemption tracking for Indian equity taxation.

Indian tax rules (equity, as of Budget 2024 / FY 2025-26):
    - LTCG: Holding period > 12 months (> 365 days). Tax @ 12.5% above Rs 1.25 lakh exemption.
    - STCG: Holding period <= 12 months (<= 365 days). Tax @ 20%.
    - Near-LTCG threshold (335-365 days): 30-day lock to prevent 7.5% tax penalty (20% - 12.5%).
    - Indian Financial Year runs from April 1 to March 31.
    - Q4 (Jan 1 - March 31): Proactive tax-gain harvesting up to remaining Rs 1.25L exemption.

This module:
    1. Loads FIFO tax lots from holding_tax_lots
    2. Implements tax-optimized lot selection (STCL -> LTCL -> LTCG -> STCG -> Near-LTCG deferred)
    3. Computes potential tax liability and issues warnings for sells in the 30-day conversion window
    4. Tracks FY realized LTCG toward the Rs 1.25 lakh tax-free ceiling
    5. Produces proactive loss and gain harvesting recommendations
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

from src.db.connection import execute_sql
from src.db.repository import get_realized_ltcg_ytd
from src.models.dtos import TaxHarvestingOpportunityDTO, TaxHarvestingSummaryDTO

# ============================================================
# Constants
# ============================================================

LTCG_HOLDING_DAYS = 365  # days for equity LTCG classification
STCG_TAX_RATE = Decimal("0.20")  # 20% STCG on equity
LTCG_TAX_RATE = Decimal("0.125")  # 12.5% LTCG on equity
LTCG_EXEMPTION = Decimal("125000.00")  # Rs 1.25 lakh LTCG exemption per FY
STCG_WARNING_WINDOW_DAYS = 30  # warn/defer if selling within 30 days of LTCG cutoff


# ============================================================
# Data Classes
# ============================================================


@dataclass
class TaxLot:
    """A single tax lot for a holding."""

    lot_id: int
    holding_id: int
    buy_date: date
    buy_price: Decimal
    quantity: int
    remaining_quantity: int
    days_held: int = 0
    tax_type: str = "STCG"  # 'STCG' | 'LTCG'
    days_to_ltcg: int | None = None  # None if already LTCG
    ref_date: date | None = None

    def __post_init__(self):
        today = self.ref_date or date.today()
        self.days_held = (today - self.buy_date).days
        if self.days_held > LTCG_HOLDING_DAYS:
            self.tax_type = "LTCG"
            self.days_to_ltcg = None
        else:
            self.tax_type = "STCG"
            self.days_to_ltcg = max(0, LTCG_HOLDING_DAYS - self.days_held)

    @property
    def is_near_ltcg(self) -> bool:
        """True if this lot will convert to LTCG within the 30-day warning window."""
        return (
            self.tax_type == "STCG"
            and self.days_to_ltcg is not None
            and 0 < self.days_to_ltcg <= STCG_WARNING_WINDOW_DAYS
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
                    lot_ltcg_date = lot.buy_date + timedelta(days=LTCG_HOLDING_DAYS + 1)
                    if self.next_ltcg_date is None or lot_ltcg_date < self.next_ltcg_date:
                        self.next_ltcg_date = lot_ltcg_date

        if self.stcg_quantity > 0:
            stcg_gain = (self.current_price * self.stcg_quantity) - self.stcg_cost_basis
            if stcg_gain > 0:
                self.estimated_stcg_tax = (stcg_gain * STCG_TAX_RATE).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

        if self.ltcg_quantity > 0:
            ltcg_gain = (self.current_price * self.ltcg_quantity) - self.ltcg_cost_basis
            if ltcg_gain > 0:
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
# Financial Year Helpers
# ============================================================


def get_financial_year_bounds(ref_date: date | None = None) -> tuple[date, date, str]:
    """
    Returns (fy_start_date, fy_end_date, fy_label) for Indian taxation.
    The Indian financial year runs from April 1 to March 31.
    """
    ref = ref_date or date.today()
    if ref.month >= 4:
        fy_start = date(ref.year, 4, 1)
        fy_end = date(ref.year + 1, 3, 31)
        fy_label = f"FY {ref.year}-{str(ref.year + 1)[-2:]}"
    else:
        fy_start = date(ref.year - 1, 4, 1)
        fy_end = date(ref.year, 3, 31)
        fy_label = f"FY {ref.year - 1}-{str(ref.year)[-2:]}"
    return fy_start, fy_end, fy_label


# ============================================================
# Tax Lot Selection Engine
# ============================================================


# ============================================================
# Annual Tax Harvesting & Exemption Tracker
# ============================================================


def get_annual_tax_harvesting_summary(
    user_id: str = "default",
    ref_date: date | None = None,
) -> TaxHarvestingSummaryDTO:
    """
    Computes annual capital gains status and proactive tax harvesting advisory:
    1. Financial Year (April 1 to March 31) realized LTCG tracked against Rs 1.25L exemption.
    2. Identifies all unrealized short-term and long-term loss lots (LOSS_HARVEST).
    3. Identifies lots near the 365-day boundary that should be protected (NEAR_LTCG_DEFER).
    4. In Q4 (Jan 1 - Mar 31), if remaining LTCG exemption > 0, proposes harvesting LTCG gains
       tax-free up to the ceiling (GAIN_HARVEST).

    Args:
        user_id: Portfolio owner.
        ref_date: Optional reference date (defaults to today).

    Returns:
        TaxHarvestingSummaryDTO with itemized opportunities.
    """
    today = ref_date or date.today()
    fy_start, fy_end, fy_label = get_financial_year_bounds(today)

    # 1. Realized LTCG YTD
    realized_ltcg = get_realized_ltcg_ytd(user_id=user_id, fy_start_date=fy_start)
    exemption_limit = LTCG_EXEMPTION
    exemption_remaining = max(Decimal("0.00"), exemption_limit - realized_ltcg)

    is_q4 = today.month in (1, 2, 3)

    opportunities: list[TaxHarvestingOpportunityDTO] = []
    profiles = load_tax_lots(user_id)

    # 2. Loss harvesting & near-LTCG deferral
    ltcg_gain_candidates: list[
        tuple[str, TaxLot, Decimal, Decimal]
    ] = []  # sym, lot, cur_price, gain

    for sym, profile in profiles.items():
        for lot in profile.lots:
            if lot.remaining_quantity <= 0:
                continue

            # Update lot with ref_date
            lot_ref = TaxLot(
                lot_id=lot.lot_id,
                holding_id=lot.holding_id,
                buy_date=lot.buy_date,
                buy_price=lot.buy_price,
                quantity=lot.quantity,
                remaining_quantity=lot.remaining_quantity,
                ref_date=today,
            )

            pnl_per_share = profile.current_price - lot_ref.buy_price
            unrealized_pnl = (pnl_per_share * lot_ref.remaining_quantity).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            if unrealized_pnl < 0:
                # Loss harvest opportunity
                is_st = lot_ref.tax_type == "STCG"
                tax_rate = STCG_TAX_RATE if is_st else LTCG_TAX_RATE
                tax_savings = (abs(unrealized_pnl) * tax_rate).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

                opportunities.append(
                    TaxHarvestingOpportunityDTO(
                        tradingsymbol=sym,
                        lot_id=lot_ref.lot_id,
                        buy_date=lot_ref.buy_date,
                        quantity=lot_ref.remaining_quantity,
                        buy_price=lot_ref.buy_price,
                        current_price=profile.current_price,
                        unrealized_pnl=unrealized_pnl,
                        tax_type="STCG" if is_st else "LTCG",
                        potential_tax_savings=tax_savings,
                        action_type="LOSS_HARVEST",
                        days_to_ltcg=lot_ref.days_to_ltcg,
                    )
                )
            else:
                # Profitable lot
                if lot_ref.is_near_ltcg:
                    # Near-LTCG warning/deferral
                    tax_diff = STCG_TAX_RATE - LTCG_TAX_RATE  # 7.5%
                    tax_savings = (unrealized_pnl * tax_diff).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    opportunities.append(
                        TaxHarvestingOpportunityDTO(
                            tradingsymbol=sym,
                            lot_id=lot_ref.lot_id,
                            buy_date=lot_ref.buy_date,
                            quantity=lot_ref.remaining_quantity,
                            buy_price=lot_ref.buy_price,
                            current_price=profile.current_price,
                            unrealized_pnl=unrealized_pnl,
                            tax_type="STCG",
                            potential_tax_savings=tax_savings,
                            action_type="NEAR_LTCG_DEFER",
                            days_to_ltcg=lot_ref.days_to_ltcg,
                        )
                    )
                elif lot_ref.tax_type == "LTCG" and is_q4 and exemption_remaining > 0:
                    ltcg_gain_candidates.append(
                        (sym, lot_ref, profile.current_price, unrealized_pnl)
                    )

    # 3. Q4 Gain Harvesting (budgeted up to remaining exemption)
    if is_q4 and exemption_remaining > 0 and ltcg_gain_candidates:
        # Sort by gain ascending to harvest cleanly
        ltcg_gain_candidates.sort(key=lambda x: x[3])
        budget = exemption_remaining

        for sym, lot, cur_price, lot_gain in ltcg_gain_candidates:
            if budget <= 0:
                break
            gain_per_share = cur_price - lot.buy_price
            if gain_per_share <= 0:
                continue

            max_harvestable_gain = min(lot_gain, budget)
            harvest_qty = int(
                (max_harvestable_gain / gain_per_share).to_integral_value(rounding=ROUND_DOWN)
            )
            if harvest_qty <= 0:
                continue

            harvest_gain = (gain_per_share * harvest_qty).quantize(Decimal("0.01"))
            budget -= harvest_gain

            # Tax saved by exploiting 0% bracket now rather than paying 12.5% in the future
            tax_savings = (harvest_gain * LTCG_TAX_RATE).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            opportunities.append(
                TaxHarvestingOpportunityDTO(
                    tradingsymbol=sym,
                    lot_id=lot.lot_id,
                    buy_date=lot.buy_date,
                    quantity=harvest_qty,
                    buy_price=lot.buy_price,
                    current_price=cur_price,
                    unrealized_pnl=harvest_gain,
                    tax_type="LTCG",
                    potential_tax_savings=tax_savings,
                    action_type="GAIN_HARVEST",
                    days_to_ltcg=None,
                )
            )

    return TaxHarvestingSummaryDTO(
        fy_year=fy_label,
        ltcg_exemption_limit=exemption_limit,
        ltcg_realized_ytd=realized_ltcg,
        ltcg_exemption_remaining=exemption_remaining,
        is_q4=is_q4,
        opportunities=opportunities,
    )


# ============================================================
# Existing Tax Guard Functions (Preserved & Enhanced)
# ============================================================


def load_tax_lots(user_id: str = "default") -> dict[str, HoldingTaxProfile]:
    """
    Load all tax lots for current holdings.
    Returns dict keyed by tradingsymbol -> HoldingTaxProfile.
    """
    rows = execute_sql(
        """
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
    """,
        {"uid": user_id},
    )

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

    for lot in profile.lots:
        if remaining_to_sell <= 0:
            break

        sold_from_lot = min(remaining_to_sell, lot.remaining_quantity)
        remaining_to_sell -= sold_from_lot

        if lot.tax_type == "STCG":
            stcg_lots_sold += sold_from_lot
            gain = (profile.current_price - lot.buy_price) * sold_from_lot
            stcg_gain += gain

            if lot.is_near_ltcg:
                warnings.append(
                    TaxWarning(
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
                    )
                )

    if stcg_lots_sold > 0 and stcg_gain > 0:
        estimated_tax = (stcg_gain * STCG_TAX_RATE).quantize(Decimal("0.01"))
        warnings.append(
            TaxWarning(
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
            )
        )

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
            near_ltcg_holdings.append(
                {
                    "symbol": sym,
                    "quantity": p.near_ltcg_quantity,
                    "next_ltcg_date": str(p.next_ltcg_date) if p.next_ltcg_date else None,
                }
            )

        holdings_tax.append(
            {
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
            }
        )

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
