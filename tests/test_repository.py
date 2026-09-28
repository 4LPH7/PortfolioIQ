"""
Tests for src/db/repository.py and src/models/dtos.py
Verifies DTO validation, parameterized SQL execution, and append-only audit enforcement.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.db.repository import (
    get_app_config,
    get_cash_flows,
    get_current_holdings,
    get_daily_snapshots,
    get_historical_bars,
    get_holdings_reconciliation_logs,
    get_latest_backtest_run,
    get_market_calendar_entries,
    get_pending_forward_return_snapshots,
    get_realized_ltcg_ytd,
    get_signal_snapshots,
    record_backtest_run,
    record_broker_execution,
    record_cash_flow,
    record_daily_snapshot,
    record_holdings_reconciliation,
    record_order_attempt,
    record_signal_snapshot,
    update_app_config,
    update_signal_forward_returns,
    upsert_historical_bars,
    upsert_market_calendar_entry,
)
from src.models.dtos import (
    AppConfigDTO,
    BacktestRunDTO,
    BrokerExecutionDTO,
    CreateCashFlowDTO,
    CreateMarketCalendarDTO,
    CreateSignalSnapshotDTO,
    CreateSnapshotDTO,
    HistoricalBarDTO,
    HoldingDTO,
    HoldingsReconciliationDTO,
    IndicatorEvaluationDTO,
    MarketCalendarDTO,
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


def test_market_calendar_repository_crud():
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_session.execute.return_value = mock_result

    # Test upsert
    mock_row = {
        "id": 1,
        "holiday_date": date(2026, 11, 8),
        "exchange": "NSE",
        "segment": "equity",
        "holiday_name": "Diwali Muhurat",
        "session_type": "MUHURAT",
        "is_trading_holiday": False,
        "special_session_open": "18:15:00",
        "special_session_close": "19:15:00",
        "description": "Diwali",
        "created_at": None,
    }
    mock_result.mappings.return_value.one.return_value = mock_row

    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        entry = CreateMarketCalendarDTO(
            holiday_date=date(2026, 11, 8),
            holiday_name="Diwali Muhurat",
            session_type="MUHURAT",
            is_trading_holiday=False,
            special_session_open="18:15:00",
            special_session_close="19:15:00",
        )
        res = upsert_market_calendar_entry(entry)
        assert isinstance(res, MarketCalendarDTO)
        assert res.session_type == "MUHURAT"

    # Test get
    mock_result.mappings.return_value.all.return_value = [mock_row]
    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        entries = get_market_calendar_entries(year=2026)
        assert len(entries) == 1
        assert entries[0].holiday_name == "Diwali Muhurat"


def test_holdings_reconciliation_repository():
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_session.execute.return_value = mock_result

    mock_row = {
        "id": 1,
        "user_id": "default",
        "instrument_token": 123456,
        "tradingsymbol": "RELIANCE",
        "old_quantity": 10,
        "new_quantity": 15,
        "old_avg_price": Decimal("2500"),
        "new_avg_price": Decimal("2500"),
        "delta_quantity": 5,
        "reconciliation_reason": "T1_SETTLEMENT",
        "detected_at": None,
    }
    mock_result.mappings.return_value.one.return_value = mock_row

    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        entry = HoldingsReconciliationDTO(
            instrument_token=123456,
            tradingsymbol="RELIANCE",
            old_quantity=10,
            new_quantity=15,
            delta_quantity=5,
            reconciliation_reason="T1_SETTLEMENT",
        )
        res = record_holdings_reconciliation(entry)
        assert res.id == 1
        assert res.reconciliation_reason == "T1_SETTLEMENT"

    mock_result.mappings.return_value.all.return_value = [mock_row]
    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        logs = get_holdings_reconciliation_logs(reason="T1_SETTLEMENT")
        assert len(logs) == 1
        assert logs[0].tradingsymbol == "RELIANCE"


def test_portfolio_daily_snapshots_repository():
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_session.execute.return_value = mock_result

    mock_row = {
        "id": 1,
        "snapshot_date": date(2026, 9, 28),
        "user_id": "default",
        "total_equity_value": Decimal("100000.00"),
        "cash_balance": Decimal("5000.00"),
        "total_nav": Decimal("105000.00"),
        "units": Decimal("1050.000000"),
        "unit_nav": Decimal("100.0000"),
        "daily_return_pct": Decimal("0.0000"),
        "benchmark_name": "NIFTY 50 TRI",
        "benchmark_value": Decimal("25000.00"),
        "benchmark_daily_return_pct": Decimal("0.0050"),
        "net_external_flow": Decimal("0.00"),
        "gross_daily_return_pct": Decimal("0.0000"),
        "stt_drag_bps": Decimal("0.00"),
        "fee_drag_bps": Decimal("0.00"),
        "tax_drag_bps": Decimal("0.00"),
        "created_at": None,
    }
    mock_result.mappings.return_value.one.return_value = mock_row

    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        snapshot = CreateSnapshotDTO(
            snapshot_date=date(2026, 9, 28),
            total_equity_value=Decimal("100000.00"),
            cash_balance=Decimal("5000.00"),
            total_nav=Decimal("105000.00"),
            units=Decimal("1050.000000"),
            unit_nav=Decimal("100.0000"),
        )
        res = record_daily_snapshot(snapshot)
        assert res.id == 1
        assert res.total_nav == Decimal("105000.00")
        assert res.unit_nav == Decimal("100.0000")

    mock_result.mappings.return_value.all.return_value = [mock_row]
    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        snapshots = get_daily_snapshots(user_id="default", start_date=date(2026, 9, 1))
        assert len(snapshots) == 1
        assert snapshots[0].benchmark_name == "NIFTY 50 TRI"


def test_portfolio_cash_flows_repository():
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_session.execute.return_value = mock_result

    mock_row = {
        "id": 10,
        "user_id": "default",
        "flow_date": date(2026, 9, 28),
        "flow_type": "DEPOSIT",
        "amount": Decimal("50000.00"),
        "units_affected": Decimal("500.000000"),
        "nav_per_unit": Decimal("100.0000"),
        "source": "MANUAL",
        "external_reference": "TXN12345",
        "notes": "Bank transfer",
        "created_at": None,
    }
    mock_result.mappings.return_value.one.return_value = mock_row

    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        flow = CreateCashFlowDTO(
            flow_date=date(2026, 9, 28),
            flow_type="DEPOSIT",
            amount=Decimal("50000.00"),
            units_affected=Decimal("500.000000"),
            nav_per_unit=Decimal("100.0000"),
            notes="Bank transfer",
        )
        res = record_cash_flow(flow)
        assert res.id == 10
        assert res.amount == Decimal("50000.00")
        assert res.flow_type == "DEPOSIT"

    mock_result.mappings.return_value.all.return_value = [mock_row]
    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        flows = get_cash_flows(user_id="default")
        assert len(flows) == 1
        assert flows[0].source == "MANUAL"


def test_get_realized_ltcg_ytd_repository():
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_session.execute.return_value = mock_result
    mock_result.mappings.return_value.first.return_value = {"total_ltcg": Decimal("45000.00")}

    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        ltcg = get_realized_ltcg_ytd(user_id="default", fy_start_date=date(2026, 4, 1))
        assert ltcg == Decimal("45000.00")


def test_signal_snapshots_repository():
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_session.execute.return_value = mock_result

    mock_row = {
        "id": 1,
        "snapshot_date": date(2026, 9, 28),
        "user_id": "default",
        "tradingsymbol": "INFY",
        "model_version": "v1.0.0",
        "current_price": Decimal("1500.00"),
        "benchmark_price": Decimal("24500.00"),
        "composite_score": Decimal("72.50"),
        "signal_label": "BUY",
        "status": "PROVEN_EDGE",
        "indicators": {"rsi": {"score": 75.0}},
        "monte_carlo": {"p50": 1550.0},
        "return_5d_stock": None,
        "return_5d_benchmark": None,
        "excess_return_5d": None,
        "realized_5d_at": None,
        "return_20d_stock": None,
        "return_20d_benchmark": None,
        "excess_return_20d": None,
        "realized_20d_at": None,
        "return_60d_stock": None,
        "return_60d_benchmark": None,
        "excess_return_60d": None,
        "realized_60d_at": None,
        "created_at": None,
        "updated_at": None,
    }
    mock_result.mappings.return_value.one.return_value = mock_row

    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        snap = CreateSignalSnapshotDTO(
            snapshot_date=date(2026, 9, 28),
            tradingsymbol="INFY",
            current_price=Decimal("1500.00"),
            composite_score=Decimal("72.50"),
            signal_label="BUY",
            status="PROVEN_EDGE",
            indicators={"rsi": {"score": 75.0}},
        )
        res = record_signal_snapshot(snap)
        assert res.id == 1
        assert res.tradingsymbol == "INFY"
        assert res.status == "PROVEN_EDGE"
        assert res.composite_score == Decimal("72.50")

    mock_result.mappings.return_value.all.return_value = [mock_row]
    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        snapshots = get_signal_snapshots(tradingsymbol="INFY")
        assert len(snapshots) == 1
        assert snapshots[0].tradingsymbol == "INFY"

    # Pending forward return query
    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        pending = get_pending_forward_return_snapshots(horizon_days=20)
        assert len(pending) == 1

    # Invalid horizon raises ValueError
    import pytest

    with pytest.raises(ValueError, match="Invalid horizon_days"):
        get_pending_forward_return_snapshots(horizon_days=15)

    # Update forward returns
    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        update_signal_forward_returns(
            snapshot_id=1,
            horizon="20d",
            stock_return=Decimal("0.0520"),
            bench_return=Decimal("0.0210"),
            excess_return=Decimal("0.0310"),
        )
        assert mock_session.execute.called

    with pytest.raises(ValueError, match="Invalid horizon"):
        update_signal_forward_returns(
            snapshot_id=1,
            horizon="10d",
            stock_return=Decimal("0.05"),
            bench_return=Decimal("0.02"),
            excess_return=Decimal("0.03"),
        )


def test_backtest_runs_repository():
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_session.execute.return_value = mock_result
    run_uuid = uuid4()

    mock_run_row = {
        "id": 5,
        "run_id": run_uuid,
        "tradingsymbol": "RELIANCE",
        "model_version": "v1.0.0",
        "train_start_date": date(2025, 1, 1),
        "train_end_date": date(2025, 12, 31),
        "test_start_date": date(2026, 1, 1),
        "test_end_date": date(2026, 3, 31),
        "train_window_days": 252,
        "test_window_days": 63,
        "total_folds": 4,
        "strategy_cagr": 0.2450,
        "strategy_sharpe": 1.45,
        "strategy_sortino": 1.95,
        "strategy_max_drawdown": -0.1250,
        "strategy_win_rate": 0.6200,
        "strategy_profit_factor": 1.85,
        "total_trades": 18,
        "stock_cagr": 0.1520,
        "stock_sharpe": 0.95,
        "stock_max_drawdown": -0.1850,
        "benchmark_cagr": 0.1280,
        "benchmark_sharpe": 0.85,
        "benchmark_max_drawdown": -0.1600,
        "excess_cagr_vs_stock": 0.0930,
        "excess_cagr_vs_benchmark": 0.1170,
        "total_cost_drag_bps": 45.0,
        "status": "PROVEN_EDGE",
        "passed_hurdle": True,
        "hurdle_details": {"h1": True, "h2": True, "h3": True, "h4": True},
        "created_at": None,
    }
    mock_result.mappings.return_value.one.return_value = mock_run_row

    eval_dto = IndicatorEvaluationDTO(
        name="RSI",
        mean_ic=0.0820,
        std_ic=0.0410,
        information_ratio=2.0,
        p_value=0.0120,
        weight=0.60,
        is_pruned=False,
    )

    run_dto = BacktestRunDTO(
        run_id=run_uuid,
        tradingsymbol="RELIANCE",
        train_start_date=date(2025, 1, 1),
        train_end_date=date(2025, 12, 31),
        test_start_date=date(2026, 1, 1),
        test_end_date=date(2026, 3, 31),
        strategy_cagr=0.2450,
        status="PROVEN_EDGE",
        passed_hurdle=True,
        indicators=[eval_dto],
    )

    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        saved = record_backtest_run(run_dto)
        assert saved.id == 5
        assert saved.tradingsymbol == "RELIANCE"
        assert saved.status == "PROVEN_EDGE"
        assert len(saved.indicators) == 1
        assert saved.indicators[0].name == "RSI"

    # get_latest_backtest_run
    mock_eval_row = {
        "indicator_name": "RSI",
        "in_sample_ic": 0.09,
        "in_sample_p_value": 0.01,
        "out_sample_ic": 0.08,
        "out_sample_p_value": 0.02,
        "mean_ic": 0.0820,
        "std_ic": 0.0410,
        "information_ratio": 2.0,
        "weight": 0.60,
        "is_pruned": False,
        "prune_reason": None,
    }
    mock_result.mappings.return_value.first.return_value = mock_run_row
    mock_result.mappings.return_value.all.return_value = [mock_eval_row]

    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        latest = get_latest_backtest_run("RELIANCE")
        assert latest is not None
        assert latest.tradingsymbol == "RELIANCE"
        assert len(latest.indicators) == 1
        assert latest.indicators[0].information_ratio == 2.0

    # Test non-existent backtest run returns None
    mock_result.mappings.return_value.first.return_value = None
    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        assert get_latest_backtest_run("NONEXISTENT") is None


def test_historical_bars_repository():
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_session.execute.return_value = mock_result

    # Empty list returns 0
    assert upsert_historical_bars([]) == 0

    bars = [
        HistoricalBarDTO(
            tradingsymbol="INFY",
            bar_date=date(2026, 9, 25),
            open=Decimal("1480.00"),
            high=Decimal("1510.00"),
            low=Decimal("1475.00"),
            close=Decimal("1500.00"),
            volume=2500000,
        ),
        HistoricalBarDTO(
            tradingsymbol="INFY",
            bar_date=date(2026, 9, 28),
            open=Decimal("1500.00"),
            high=Decimal("1525.00"),
            low=Decimal("1495.00"),
            close=Decimal("1520.00"),
            volume=3100000,
        ),
    ]

    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        count = upsert_historical_bars(bars)
        assert count == 2

    mock_rows = [
        {
            "tradingsymbol": "INFY",
            "bar_date": date(2026, 9, 25),
            "open": Decimal("1480.00"),
            "high": Decimal("1510.00"),
            "low": Decimal("1475.00"),
            "close": Decimal("1500.00"),
            "volume": 2500000,
            "created_at": None,
        },
        {
            "tradingsymbol": "INFY",
            "bar_date": date(2026, 9, 28),
            "open": Decimal("1500.00"),
            "high": Decimal("1525.00"),
            "low": Decimal("1495.00"),
            "close": Decimal("1520.00"),
            "volume": 3100000,
            "created_at": None,
        },
    ]
    mock_result.mappings.return_value.all.return_value = mock_rows

    with patch("src.db.repository.get_db_session") as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        res = get_historical_bars("INFY", start_date=date(2026, 9, 1), end_date=date(2026, 9, 28))
        assert len(res) == 2
        assert res[0].close == Decimal("1500.00")
        assert res[1].close == Decimal("1520.00")
