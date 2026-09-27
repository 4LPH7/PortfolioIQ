"""
Tests for src/db/repository.py and src/models/dtos.py
Verifies DTO validation, parameterized SQL execution, and append-only audit enforcement.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.db.repository import (
    get_app_config,
    get_current_holdings,
    record_broker_execution,
    record_order_attempt,
    update_app_config,
)
from src.models.dtos import (
    AppConfigDTO,
    BrokerExecutionDTO,
    HoldingDTO,
    OrderAttemptDTO,
)


def test_holding_dto_validation():
    """Verify HoldingDTO validates required and optional fields."""
    dto = HoldingDTO(
        instrument_token=256265,
        tradingsymbol="INFY",
        exchange="NSE",
        quantity=50,
        average_price=Decimal("1500.25"),
        last_price=Decimal("1520.00"),
        product="CNC",
    )
    assert dto.instrument_token == 256265
    assert dto.tradingsymbol == "INFY"
    assert dto.quantity == 50
    assert dto.average_price == Decimal("1500.25")
    assert dto.user_id == "default"


def test_order_attempt_dto_validation():
    """Verify OrderAttemptDTO validates order types and transaction types."""
    order_id = uuid4()
    dto = OrderAttemptDTO(
        internal_order_id=order_id,
        instrument_token=738561,
        tradingsymbol="RELIANCE",
        transaction_type="BUY",
        requested_quantity=10,
        price_at_signal=Decimal("2800.50"),
        validation_status="APPROVED",
    )
    assert dto.internal_order_id == order_id
    assert dto.transaction_type == "BUY"
    assert dto.validation_status == "APPROVED"
    assert dto.is_dry_run is True


def test_record_order_attempt_executes_insert():
    """Verify record_order_attempt runs parameterized SQL insert and returns DTO."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_session.execute.return_value = mock_result
    order_id = uuid4()

    mock_row = {
        "id": 101,
        "internal_order_id": order_id,
        "user_id": "default",
        "instrument_token": 738561,
        "tradingsymbol": "RELIANCE",
        "exchange": "NSE",
        "transaction_type": "BUY",
        "order_type": "MARKET",
        "variety": "regular",
        "product": "CNC",
        "validity": "DAY",
        "requested_quantity": 10,
        "executed_quantity": 0,
        "requested_price": None,
        "trigger_price": None,
        "execution_price": None,
        "price_at_signal": Decimal("2800.50"),
        "validation_status": "APPROVED",
        "broker_status": "NOT_SENT",
        "broker_status_message": None,
        "trigger_source": "REBALANCER",
        "rebalance_session_id": None,
        "target_weight_pct": None,
        "current_weight_pct": None,
        "drift_pct": None,
        "tax_type": None,
        "estimated_tax_impact": None,
        "is_dry_run": True,
        "created_at": None,
        "validated_at": None,
        "executed_at": None,
        "notes": None,
    }
    mock_result.mappings.return_value.one.return_value = mock_row

    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session

        attempt = record_order_attempt(
            internal_order_id=order_id,
            instrument_token=738561,
            tradingsymbol="RELIANCE",
            exchange="NSE",
            transaction_type="BUY",
            requested_quantity=10,
            price_at_signal=Decimal("2800.50"),
            validation_status="APPROVED",
            is_dry_run=True,
        )

        assert mock_session.execute.called
        query_text = str(mock_session.execute.call_args[0][0])
        assert "INSERT INTO order_audit_trail" in query_text
        assert "UPDATE" not in query_text

        assert isinstance(attempt, OrderAttemptDTO)
        assert attempt.id == 101
        assert attempt.tradingsymbol == "RELIANCE"


def test_record_broker_execution_is_append_only():
    """Verify record_broker_execution inserts a new outcome row without violating immutability."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_session.execute.return_value = mock_result
    order_id = uuid4()

    mock_row = {
        "id": 102,
        "internal_order_id": order_id,
        "kite_order_id": "240710001",
        "broker_status": "OPEN",
        "broker_status_message": None,
        "executed_quantity": 10,
        "execution_price": Decimal("2801.00"),
        "executed_at": None,
        "is_dry_run": False,
    }
    mock_result.mappings.return_value.one.return_value = mock_row

    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session

        outcome = record_broker_execution(
            internal_order_id=order_id,
            instrument_token=738561,
            tradingsymbol="RELIANCE",
            exchange="NSE",
            transaction_type="BUY",
            requested_quantity=10,
            price_at_signal=Decimal("2800.50"),
            status="PLACED",
            kite_order_id="240710001",
            is_dry_run=False,
            executed_quantity=10,
            execution_price=Decimal("2801.00"),
        )

        assert mock_session.execute.called
        query_text = str(mock_session.execute.call_args[0][0])
        # MUST BE INSERT, NEVER UPDATE
        assert "INSERT INTO order_audit_trail" in query_text
        assert "UPDATE" not in query_text

        assert isinstance(outcome, BrokerExecutionDTO)
        assert outcome.broker_status == "OPEN"
        assert outcome.kite_order_id == "240710001"


def test_get_current_holdings():
    """Verify get_current_holdings returns a list of HoldingDTO objects."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_session.execute.return_value = mock_result

    mock_rows = [
        {
            "id": 1,
            "user_id": "default",
            "instrument_token": 256265,
            "tradingsymbol": "INFY",
            "exchange": "NSE",
            "isin": "INE009A01021",
            "quantity": 25,
            "t1_quantity": 0,
            "opening_quantity": 25,
            "used_quantity": 0,
            "authorised_quantity": 25,
            "collateral_quantity": 0,
            "average_price": Decimal("1450.00"),
            "last_price": Decimal("1510.00"),
            "close_price": Decimal("1490.00"),
            "pnl": Decimal("1500.00"),
            "day_change": Decimal("20.00"),
            "day_change_pct": Decimal("1.34"),
            "product": "CNC",
            "first_buy_date": None,
            "has_discrepancy": False,
            "last_synced_at": None,
            "created_at": None,
            "updated_at": None,
        }
    ]
    mock_result.mappings.return_value.all.return_value = mock_rows

    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session

        holdings = get_current_holdings("default")
        assert len(holdings) == 1
        assert isinstance(holdings[0], HoldingDTO)
        assert holdings[0].tradingsymbol == "INFY"
        assert holdings[0].quantity == 25


def test_app_config_get_and_update():
    """Verify app config retrieval and updates."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_session.execute.return_value = mock_result

    mock_row = {
        "key": "dry_run_mode",
        "value": "true",
        "value_type": "boolean",
        "description": "Simulation mode",
        "updated_at": None,
    }
    mock_result.mappings.return_value.first.return_value = mock_row
    mock_result.rowcount = 1

    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session

        cfg = get_app_config("dry_run_mode")
        assert isinstance(cfg, AppConfigDTO)
        assert cfg.key == "dry_run_mode"
        assert cfg.value == "true"

        update_app_config("dry_run_mode", "false")
        assert mock_session.execute.called
        query_text = str(mock_session.execute.call_args[0][0])
        assert "UPDATE system_config SET value = :value" in query_text
