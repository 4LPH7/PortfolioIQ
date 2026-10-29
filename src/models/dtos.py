"""
PortfolioIQ — Repository Data Transfer Objects (DTOs)
Strict, typed Pydantic representations for the persistence and domain boundary.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        "SELLABLE_CHECK",  # migration 025
        "PRICE_BAND_CHECK",
        "PORTFOLIO_LIMITS_CHECK",
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


class OptimizeRequestDTO(BaseModel):
    method: Literal["risk_parity", "min_variance", "hrp", "equal_weight"] = "risk_parity"
    user_id: str = "default"
    cap_pct: float | None = Field(None, gt=0, le=100)  # default: concentration_limit_pct
    cash_floor_pct: float | None = Field(None, ge=0, lt=50)  # default: rebalancer cash buffer
    walk_forward: bool = True


class ProfileTargetDTO(BaseModel):
    instrument_token: int
    target_weight_pct: float = Field(ge=0, le=100)


class CreateProfileDTO(BaseModel):
    """A saved suggestion. It is created inactive; activation is a separate request."""

    user_id: str = "default"
    profile_name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(None, max_length=500)
    targets: list[ProfileTargetDTO] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _check_targets(self) -> CreateProfileDTO:
        tokens = [t.instrument_token for t in self.targets]
        if len(set(tokens)) != len(tokens):
            raise ValueError("Each instrument may appear only once")
        if sum(t.target_weight_pct for t in self.targets) > 100.001:
            raise ValueError("Stock targets add up to more than 100%")
        return self


class OddsRequestDTO(BaseModel):
    """A delivery buy with a stop and a target. Entry defaults to the last traded close."""

    user_id: str = "default"
    symbol: str = Field(pattern=r"^[A-Z0-9&_-]{1,30}$")
    entry: float | None = Field(None, gt=0)
    stop: float = Field(gt=0)
    target: float = Field(gt=0)
    horizon_days: int = Field(20, ge=5, le=250)
    lookback_years: int = Field(2, ge=1, le=5)
    replays: int = Field(10_000, ge=1_000, le=20_000)
    seed: int = Field(19, ge=0)
    max_loss_inr: float | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _check_levels(self) -> OddsRequestDTO:
        if self.stop >= self.target:
            raise ValueError("The stop must be below the target")
        if self.entry is not None and not self.stop < self.entry < self.target:
            raise ValueError("The entry must sit between the stop and the target")
        return self


class PriceAlertDTO(BaseModel):
    user_id: str = "default"
    symbol: str = Field(pattern=r"^[A-Z0-9&_-]{1,30}$")
    kind: Literal["above", "below", "pct_move"]
    price: float | None = Field(None, gt=0)
    pct: float | None = Field(None, gt=0, le=50)
    direction: Literal["up", "down", "either"] = "either"
    note: str | None = Field(None, max_length=120)
    rearm: bool = False

    @model_validator(mode="after")
    def _check_value(self) -> PriceAlertDTO:
        if self.kind == "pct_move" and self.pct is None:
            raise ValueError("A percent-move alert needs pct")
        if self.kind != "pct_move" and self.price is None:
            raise ValueError("A price alert needs a price")
        return self


class PriceAlertUpdateDTO(BaseModel):
    user_id: str = "default"
    active: bool


class ProjectionRequestDTO(BaseModel):
    user_id: str = "default"
    years: int = Field(5, ge=1, le=10)
    simulations: int = Field(5_000, ge=1_000, le=20_000)
    lookback_years: int = Field(5, ge=2, le=10)
    seed: int = Field(19, ge=0)


class ResearchRunRequestDTO(BaseModel):
    """One committee run. A repeated idempotency_key returns the run it already created."""

    user_id: str = "default"
    symbol: str = Field(pattern=r"^[A-Z0-9&_-]{1,30}$")
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


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


class MetricValueDTO(BaseDTO):
    """A statistic with an honest status: a value only when the estimate can be trusted."""

    value: float | None = None
    status: Literal["ok", "insufficient_data", "zero_variance", "non_finite", "unstable"]
    n_obs: int = 0


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
    benchmark_twr_pct: float | None = None  # NIFTY 50 TRI compounded over the same days
    history_days: int
    is_warmup_period: bool = False

    # Risk statistics after QuantConnect Lean's PortfolioStatistics (migration-free, computed)
    probabilistic_sharpe: MetricValueDTO | None = None  # P(true Sharpe > 0), 0..1
    var_95_pct: MetricValueDTO | None = None  # one-day historical VaR, a positive loss %
    cvar_95_pct: MetricValueDTO | None = None
    var_99_pct: MetricValueDTO | None = None
    cvar_99_pct: MetricValueDTO | None = None
    information_ratio: MetricValueDTO | None = None

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


# ============================================================
# Phase 5: Quantitative Signal Engine DTOs
# ============================================================


class IndicatorEvaluationDTO(BaseDTO):
    name: str
    in_sample_ic: float | None = None
    in_sample_p_value: float | None = None
    out_sample_ic: float | None = None
    out_sample_p_value: float | None = None
    mean_ic: float = 0.0
    std_ic: float = 0.0
    information_ratio: float = 0.0
    p_value: float = 1.0
    weight: float = 0.0
    is_pruned: bool = True
    prune_reason: str | None = None
    score: float = 50.0


class CalibratedMonteCarloDTO(BaseDTO):
    horizon_days: int = 30
    current_price: float = 0.0
    degrees_of_freedom: float = 5.0
    scale_multiplier: float = 1.0
    empirical_coverage_80: float = 80.0
    p10: float = 0.0
    p25: float = 0.0
    p50: float = 0.0
    p75: float = 0.0
    p90: float = 0.0
    prob_profit: float = 50.0
    fan_dates: list[str] = []
    fan_p10: list[float] = []
    fan_p50: list[float] = []
    fan_p90: list[float] = []


class BacktestRunDTO(BaseDTO):
    id: int | None = None
    run_id: UUID | str
    tradingsymbol: str
    model_version: str = "v1.0.0"
    train_start_date: date
    train_end_date: date
    test_start_date: date
    test_end_date: date
    train_window_days: int = 252
    test_window_days: int = 63
    total_folds: int = 1
    strategy_cagr: float | None = None
    strategy_sharpe: float | None = None
    strategy_sortino: float | None = None
    strategy_max_drawdown: float | None = None
    strategy_win_rate: float | None = None
    strategy_profit_factor: float | None = None
    total_trades: int = 0
    stock_cagr: float | None = None
    stock_sharpe: float | None = None
    stock_max_drawdown: float | None = None
    benchmark_cagr: float | None = None
    benchmark_sharpe: float | None = None
    benchmark_max_drawdown: float | None = None
    excess_cagr_vs_stock: float | None = None
    excess_cagr_vs_benchmark: float | None = None
    total_cost_drag_bps: float = 0.0
    slippage_drag_inr: float = 0.0  # market impact paid on fills (statutory charges excluded)
    slippage_drag_bps: float = 0.0  # same, annualised bps of initial capital
    inputs_hash: str | None = None  # sha256 of parameters, constants and price bars
    status: Literal["PROVEN_EDGE", "UNPROVEN_NOISE", "PENDING"] = "UNPROVEN_NOISE"
    passed_hurdle: bool = False
    hurdle_details: dict[str, Any] = {}
    indicators: list[IndicatorEvaluationDTO] = []
    created_at: datetime | None = None


class SignalSnapshotDTO(BaseDTO):
    id: int | None = None
    snapshot_date: date
    user_id: str = "default"
    tradingsymbol: str
    model_version: str = "v1.0.0"
    current_price: Decimal
    benchmark_price: Decimal | None = None
    composite_score: Decimal
    signal_label: Literal["STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"]
    status: Literal["PROVEN_EDGE", "UNPROVEN_NOISE", "PENDING"] = "PENDING"
    indicators: dict[str, Any] = {}
    monte_carlo: dict[str, Any] = {}
    return_5d_stock: Decimal | None = None
    return_5d_benchmark: Decimal | None = None
    excess_return_5d: Decimal | None = None
    realized_5d_at: date | None = None
    return_20d_stock: Decimal | None = None
    return_20d_benchmark: Decimal | None = None
    excess_return_20d: Decimal | None = None
    realized_20d_at: date | None = None
    return_60d_stock: Decimal | None = None
    return_60d_benchmark: Decimal | None = None
    excess_return_60d: Decimal | None = None
    realized_60d_at: date | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CreateSignalSnapshotDTO(BaseDTO):
    snapshot_date: date
    user_id: str = "default"
    tradingsymbol: str
    model_version: str = "v1.0.0"
    current_price: Decimal
    benchmark_price: Decimal | None = None
    composite_score: Decimal
    signal_label: Literal["STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"]
    status: Literal["PROVEN_EDGE", "UNPROVEN_NOISE", "PENDING"] = "PENDING"
    indicators: dict[str, Any] = {}
    monte_carlo: dict[str, Any] = {}


class HoldingSignalDTO(BaseDTO):
    symbol: str
    tradingsymbol: str
    current_price: float
    composite_score: float
    signal_label: str
    status: Literal["PROVEN_EDGE", "UNPROVEN_NOISE", "PENDING"] = "PENDING"
    evidence_badge: str = ""
    indicators: list[IndicatorEvaluationDTO] = []
    monte_carlo: CalibratedMonteCarloDTO | None = None
    backtest_summary: dict[str, Any] | None = None
    avg_buy_price: float = 0.0
    data_start: str = ""
    data_end: str = ""
    data_points: int = 0


class HistoricalBarDTO(BaseDTO):
    tradingsymbol: str
    bar_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = 0
    adj_close: Decimal | None = None  # split- and dividend-adjusted close (migration 024)
    price_basis: str | None = None
    created_at: datetime | None = None


# ─────────────────────────────────────────────────────────────
# Phase 6 Product & UX Polish DTOs
# ─────────────────────────────────────────────────────────────
class CSVImportHoldingDTO(BaseDTO):
    tradingsymbol: str
    quantity: int
    average_price: Decimal
    exchange: str = "NSE"
    instrument_token: int | None = None
    invested_value: Decimal | None = None


class CSVImportResultDTO(BaseDTO):
    imported_count: int
    skipped_count: int
    holdings: list[CSVImportHoldingDTO]
    errors: list[str] = Field(default_factory=list)
    message: str
