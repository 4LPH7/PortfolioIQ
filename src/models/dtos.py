"""
PortfolioIQ — Repository Data Transfer Objects (DTOs)
Strict, typed Pydantic representations for the persistence and domain boundary.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BaseDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class HoldingDTO(BaseDTO):
    id: int | None = None
    user_id: str = "default"
    instrument_token: int
    tradingsymbol: str
    exchange: str = "NSE"
    isin: str | None = None
    quantity: int = 0
    t1_quantity: int = 0
    opening_quantity: int = 0
    used_quantity: int = 0
    authorised_quantity: int = 0
    collateral_quantity: int = 0
    average_price: Decimal
    last_price: Decimal | None = None
    close_price: Decimal | None = None
    pnl: Decimal | None = None
    day_change: Decimal | None = None
    day_change_pct: Decimal | None = None
    product: Literal["CNC", "MIS", "NRML", "MTF"] = "CNC"
    first_buy_date: date | None = None
    has_discrepancy: bool = False
    last_synced_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OrderAttemptDTO(BaseDTO):
    id: int | None = None
    internal_order_id: UUID | str
    kite_order_id: str | None = None
    user_id: str = "default"
    instrument_token: int
    tradingsymbol: str
    exchange: str = "NSE"
    transaction_type: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT", "SL", "SL-M"] = "MARKET"
    variety: Literal["regular", "amo", "iceberg", "auction"] = "regular"
    product: Literal["CNC", "MIS", "NRML", "MTF"] = "CNC"
    validity: Literal["DAY", "IOC"] = "DAY"
    requested_quantity: int
    executed_quantity: int = 0
    requested_price: Decimal | None = None
    trigger_price: Decimal | None = None
    execution_price: Decimal | None = None
    price_at_signal: Decimal
    validation_status: Literal["PENDING", "APPROVED", "BLOCKED", "DRY_RUN"] = "PENDING"
    broker_status: (
        Literal[
            "NOT_SENT",
            "OPEN",
            "COMPLETE",
            "REJECTED",
            "CANCELLED",
            "TRIGGER PENDING",
            "MODIFY PENDING",
        ]
        | None
    ) = None
    broker_status_message: str | None = None
    trigger_source: Literal["MANUAL", "REBALANCER", "SCHEDULER"] = "MANUAL"
    rebalance_session_id: UUID | None = None
    target_weight_pct: Decimal | None = None
    current_weight_pct: Decimal | None = None
    drift_pct: Decimal | None = None
    tax_type: Literal["STCG", "LTCG", "NA"] | None = None
    estimated_tax_impact: Decimal | None = None
    is_dry_run: bool = True
    created_at: datetime | None = None
    validated_at: datetime | None = None
    executed_at: datetime | None = None
    notes: str | None = None


class ValidationCheckDTO(BaseDTO):
    id: int | None = None
    audit_id: int
    check_name: Literal[
        "MARGIN_CHECK",
        "SLIPPAGE_CHECK",
        "CONCENTRATION_CHECK",
        "DUPLICATE_CHECK",
        "MARKET_HOURS_CHECK",
        "QUANTITY_CHECK",
        "TAX_WARNING",
    ]
    passed: bool
    expected_value: str | None = None
    actual_value: str | None = None
    message: str
    checked_at: datetime | None = None


class BrokerExecutionDTO(BaseDTO):
    id: int | None = None
    internal_order_id: UUID | str
    kite_order_id: str | None = None
    broker_status: Literal["NOT_SENT", "OPEN", "COMPLETE", "REJECTED", "CANCELLED"]
    broker_status_message: str | None = None
    executed_quantity: int = 0
    execution_price: Decimal | None = None
    executed_at: datetime | None = None
    is_dry_run: bool = True


class AppConfigDTO(BaseDTO):
    key: str
    value: str
    value_type: Literal["string", "boolean", "integer", "float"] = "string"
    description: str | None = None
    updated_at: datetime | None = None


class UpdateConfigDTO(BaseModel):
    key: str
    value: str


class MarketCalendarDTO(BaseDTO):
    id: int | None = None
    holiday_date: date
    exchange: str = "NSE"
    segment: str = "equity"
    holiday_name: str
    session_type: str = "CLOSED"
    is_trading_holiday: bool = True
    special_session_open: str | None = None
    special_session_close: str | None = None
    description: str | None = None
    created_at: datetime | None = None


class CreateMarketCalendarDTO(BaseModel):
    holiday_date: date
    exchange: str = "NSE"
    segment: str = "equity"
    holiday_name: str
    session_type: Literal["CLOSED", "MUHURAT", "HALF_DAY"] = "CLOSED"
    is_trading_holiday: bool = True
    special_session_open: str | None = None
    special_session_close: str | None = None
    description: str | None = None


class HoldingsReconciliationDTO(BaseDTO):
    id: int | None = None
    user_id: str = "default"
    instrument_token: int
    tradingsymbol: str
    old_quantity: int
    new_quantity: int
    old_avg_price: Decimal | None = None
    new_avg_price: Decimal | None = None
    delta_quantity: int
    reconciliation_reason: Literal[
        "T1_SETTLEMENT",
        "TRADE_FILL",
        "CORPORATE_ACTION_SPLIT",
        "CORPORATE_ACTION_BONUS",
        "EXTERNAL_TRANSFER",
        "INITIAL_SYNC",
        "DISCREPANCY",
    ]
    detected_at: datetime | None = None


class PortfolioDailySnapshotDTO(BaseDTO):
    id: int | None = None
    snapshot_date: date
    user_id: str = "default"
    total_equity_value: Decimal
    cash_balance: Decimal
    total_nav: Decimal
    units: Decimal = Decimal("1.000000")
    unit_nav: Decimal = Decimal("100.0000")
    daily_return_pct: Decimal | None = None
    benchmark_name: str = "NIFTY 50 TRI"
    benchmark_value: Decimal | None = None
    benchmark_daily_return_pct: Decimal | None = None
    net_external_flow: Decimal = Decimal("0.00")
    gross_daily_return_pct: Decimal | None = None
    stt_drag_bps: Decimal | None = Decimal("0.00")
    fee_drag_bps: Decimal | None = Decimal("0.00")
    tax_drag_bps: Decimal | None = Decimal("0.00")
    created_at: datetime | None = None


class CreateSnapshotDTO(BaseDTO):
    snapshot_date: date
    user_id: str = "default"
    total_equity_value: Decimal
    cash_balance: Decimal
    total_nav: Decimal
    units: Decimal = Decimal("1.000000")
    unit_nav: Decimal = Decimal("100.0000")
    daily_return_pct: Decimal | None = None
    benchmark_name: str = "NIFTY 50 TRI"
    benchmark_value: Decimal | None = None
    benchmark_daily_return_pct: Decimal | None = None
    net_external_flow: Decimal = Decimal("0.00")
    gross_daily_return_pct: Decimal | None = None
    stt_drag_bps: Decimal | None = Decimal("0.00")
    fee_drag_bps: Decimal | None = Decimal("0.00")
    tax_drag_bps: Decimal | None = Decimal("0.00")


class PortfolioCashFlowDTO(BaseDTO):
    id: int | None = None
    user_id: str = "default"
    flow_date: date
    flow_type: Literal["DEPOSIT", "WITHDRAWAL", "DIVIDEND", "CHARGE", "INTEREST"]
    amount: Decimal
    units_affected: Decimal | None = None
    nav_per_unit: Decimal | None = None
    source: Literal["MANUAL", "AUTO_MARGIN_SYNC", "CORPORATE_ACTION", "BROKER_LEDGER"] = "MANUAL"
    external_reference: str | None = None
    notes: str | None = None
    created_at: datetime | None = None


class CreateCashFlowDTO(BaseDTO):
    user_id: str = "default"
    flow_date: date
    flow_type: Literal["DEPOSIT", "WITHDRAWAL", "DIVIDEND", "CHARGE", "INTEREST"]
    amount: Decimal
    units_affected: Decimal | None = None
    nav_per_unit: Decimal | None = None
    source: Literal["MANUAL", "AUTO_MARGIN_SYNC", "CORPORATE_ACTION", "BROKER_LEDGER"] = "MANUAL"
    external_reference: str | None = None
    notes: str | None = None


class PerformanceMetricsDTO(BaseDTO):
    user_id: str = "default"
    twr_pct: float
    cagr_pct: float | None = None
    xirr_pct: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    max_drawdown_pct: float
    current_drawdown_pct: float
    high_water_mark: float
    beta: float | None = None
    alpha_annual_pct: float | None = None
    r_squared: float | None = None
    tracking_error_pct: float | None = None
    history_days: int
    is_warmup_period: bool = False

    # Drag attribution
    gross_return_pct: float
    stt_drag_bps: float = 0.0
    fee_drag_bps: float = 0.0
    tax_drag_bps: float = 0.0
    net_realized_return_pct: float


class TaxHarvestingOpportunityDTO(BaseDTO):
    tradingsymbol: str
    lot_id: int
    buy_date: date
    quantity: int
    buy_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    tax_type: Literal["STCG", "LTCG"]
    potential_tax_savings: Decimal
    action_type: Literal["LOSS_HARVEST", "GAIN_HARVEST", "NEAR_LTCG_DEFER"]
    days_to_ltcg: int | None = None


class TaxHarvestingSummaryDTO(BaseDTO):
    fy_year: str
    ltcg_exemption_limit: Decimal = Decimal("125000.00")
    ltcg_realized_ytd: Decimal
    ltcg_exemption_remaining: Decimal
    is_q4: bool
    opportunities: list[TaxHarvestingOpportunityDTO] = []
