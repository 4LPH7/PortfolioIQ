"""
PortfolioIQ — Repository Data Transfer Objects (DTOs)
Strict, typed Pydantic representations for the persistence and domain boundary.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
    broker_status: Literal[
        "NOT_SENT", "OPEN", "COMPLETE", "REJECTED", "CANCELLED",
        "TRIGGER PENDING", "MODIFY PENDING"
    ] | None = None
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
        "MARGIN_CHECK", "SLIPPAGE_CHECK", "CONCENTRATION_CHECK",
        "DUPLICATE_CHECK", "MARKET_HOURS_CHECK", "QUANTITY_CHECK", "TAX_WARNING"
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
