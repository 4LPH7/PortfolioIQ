"""
PortfolioIQ — Tests for Kite Holdings Sync & Reconciliation
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.ingestion.kite_sync import sync_holdings


@patch("src.ingestion.kite_sync.get_authenticated_kite")
@patch("src.ingestion.kite_sync.get_db_session")
@patch("src.ingestion.kite_sync._ensure_instrument_exists")
class TestKiteSyncReconciliation:
    def test_sync_holdings_initial_sync(self, mock_ensure, mock_session, mock_get_kite):
        mock_kite = MagicMock()
        mock_kite.holdings.return_value = [
            {
                "instrument_token": 123,
                "tradingsymbol": "NEWSTOCK",
                "quantity": 10,
                "t1_quantity": 0,
                "average_price": 100.0,
            }
        ]
        mock_get_kite.return_value = mock_kite

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        # No local holdings
        mock_db.execute.return_value.fetchall.return_value = []

        upserted = sync_holdings()
        assert upserted == 1

        # Check that INITIAL_SYNC was recorded
        calls = mock_db.execute.call_args_list
        reconciliation_call = [
            c for c in calls if "INSERT INTO holdings_reconciliation_log" in c[0][0].text
        ][0]
        params = reconciliation_call[0][1]

        assert params["reason"] == "INITIAL_SYNC"
        assert params["delta"] == 10

    def test_sync_holdings_t1_settlement(self, mock_ensure, mock_session, mock_get_kite):
        mock_kite = MagicMock()
        # Broker has 10 settled, 0 t1
        mock_kite.holdings.return_value = [
            {
                "instrument_token": 123,
                "tradingsymbol": "SETTLEDSTOCK",
                "quantity": 10,
                "t1_quantity": 0,
                "average_price": 100.0,
            }
        ]
        mock_get_kite.return_value = mock_kite

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        # Local has 0 settled, 10 t1
        local_row = MagicMock()
        local_row.instrument_token = 123
        local_row.tradingsymbol = "SETTLEDSTOCK"
        local_row.quantity = 0
        local_row.t1_quantity = 10
        local_row.average_price = 100.0
        local_row.last_synced_at = "2026-09-27 10:00:00"
        mock_db.execute.return_value.fetchall.return_value = [local_row]

        upserted = sync_holdings()
        assert upserted == 1

        calls = mock_db.execute.call_args_list
        reconciliation_call = [
            c for c in calls if "INSERT INTO holdings_reconciliation_log" in c[0][0].text
        ][0]
        params = reconciliation_call[0][1]

        assert params["reason"] == "T1_SETTLEMENT"
        assert params["delta"] == 0

    def test_sync_holdings_trade_fill(self, mock_ensure, mock_session, mock_get_kite):
        mock_kite = MagicMock()
        # Broker has 20 qty (up from 10)
        mock_kite.holdings.return_value = [
            {
                "instrument_token": 123,
                "tradingsymbol": "TRADESTOCK",
                "quantity": 20,
                "t1_quantity": 0,
                "average_price": 100.0,
            }
        ]
        mock_get_kite.return_value = mock_kite

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        local_row = MagicMock()
        local_row.instrument_token = 123
        local_row.tradingsymbol = "TRADESTOCK"
        local_row.quantity = 10
        local_row.t1_quantity = 0
        local_row.average_price = 100.0
        local_row.last_synced_at = "2026-09-27 10:00:00"

        def execute_side_effect(query, params=None):
            if "SELECT instrument_token, tradingsymbol, quantity" in query.text:
                m = MagicMock()
                m.fetchall.return_value = [local_row]
                return m
            if "SELECT SUM(CASE WHEN transaction_type" in query.text:
                m = MagicMock()
                r = MagicMock()
                r.net_fills = 10
                m.fetchone.return_value = r
                return m
            if "SELECT action_type FROM corporate_actions" in query.text:
                m = MagicMock()
                m.fetchone.return_value = None
                return m
            return MagicMock()

        mock_db.execute.side_effect = execute_side_effect

        upserted = sync_holdings()
        assert upserted == 1

        # We need to manually check the log inserts from the mock calls
        # because side_effect intercepts it all
        calls = mock_db.execute.call_args_list
        reconciliation_call = [
            c for c in calls if "INSERT INTO holdings_reconciliation_log" in c[0][0].text
        ][0]
        params = reconciliation_call[0][1]

        assert params["reason"] == "TRADE_FILL"
        assert params["delta"] == 10

    def test_sync_holdings_corporate_action_split(self, mock_ensure, mock_session, mock_get_kite):
        mock_kite = MagicMock()
        # Broker has 20 qty (up from 10)
        mock_kite.holdings.return_value = [
            {
                "instrument_token": 123,
                "tradingsymbol": "SPLITSTOCK",
                "quantity": 20,
                "t1_quantity": 0,
                "average_price": 50.0,
            }
        ]
        mock_get_kite.return_value = mock_kite

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        local_row = MagicMock()
        local_row.instrument_token = 123
        local_row.tradingsymbol = "SPLITSTOCK"
        local_row.quantity = 10
        local_row.t1_quantity = 0
        local_row.average_price = 100.0
        local_row.last_synced_at = "2026-09-27 10:00:00"

        def execute_side_effect(query, params=None):
            if "SELECT instrument_token, tradingsymbol, quantity" in query.text:
                m = MagicMock()
                m.fetchall.return_value = [local_row]
                return m
            if "SELECT SUM(CASE WHEN transaction_type" in query.text:
                m = MagicMock()
                r = MagicMock()
                r.net_fills = 0
                m.fetchone.return_value = r
                return m
            if "SELECT action_type FROM corporate_actions" in query.text:
                m = MagicMock()
                r = MagicMock()
                r.action_type = "SPLIT"
                m.fetchone.return_value = r
                return m
            return MagicMock()

        mock_db.execute.side_effect = execute_side_effect

        upserted = sync_holdings()
        assert upserted == 1

        calls = mock_db.execute.call_args_list
        reconciliation_call = [
            c for c in calls if "INSERT INTO holdings_reconciliation_log" in c[0][0].text
        ][0]
        params = reconciliation_call[0][1]

        assert params["reason"] == "CORPORATE_ACTION_SPLIT"
        assert params["delta"] == 10

    def test_sync_holdings_unexplained_jump_discrepancy(
        self, mock_ensure, mock_session, mock_get_kite
    ):
        mock_kite = MagicMock()
        mock_kite.holdings.return_value = [
            {
                "instrument_token": 123,
                "tradingsymbol": "MYSTERYSTOCK",
                "quantity": 20,
                "t1_quantity": 0,
                "average_price": 100.0,
            }
        ]
        mock_get_kite.return_value = mock_kite

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        local_row = MagicMock()
        local_row.instrument_token = 123
        local_row.tradingsymbol = "MYSTERYSTOCK"
        local_row.quantity = 10
        local_row.t1_quantity = 0
        local_row.average_price = 100.0
        local_row.last_synced_at = "2026-09-27 10:00:00"

        def execute_side_effect(query, params=None):
            if "SELECT instrument_token, tradingsymbol, quantity" in query.text:
                m = MagicMock()
                m.fetchall.return_value = [local_row]
                return m
            if "SELECT SUM(CASE WHEN transaction_type" in query.text:
                m = MagicMock()
                m.fetchone.return_value = None
                return m
            if "SELECT action_type FROM corporate_actions" in query.text:
                m = MagicMock()
                m.fetchone.return_value = None
                return m
            return MagicMock()

        mock_db.execute.side_effect = execute_side_effect

        with patch("src.ingestion.kite_sync.logger.warning") as mock_warning:
            upserted = sync_holdings()

            assert upserted == 1
            assert any(
                "UNEXPLAINED QUANTITY JUMP for" in call[0][0]
                for call in mock_warning.call_args_list
            )

        calls = mock_db.execute.call_args_list
        reconciliation_call = [
            c for c in calls if "INSERT INTO holdings_reconciliation_log" in c[0][0].text
        ][0]
        params = reconciliation_call[0][1]

        assert params["reason"] == "DISCREPANCY"
        assert params["delta"] == 10

    def test_sync_holdings_liquidated_position(self, mock_ensure, mock_session, mock_get_kite):
        mock_kite = MagicMock()
        mock_kite.holdings.return_value = []
        mock_get_kite.return_value = mock_kite

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        local_row = MagicMock()
        local_row.instrument_token = 123
        local_row.tradingsymbol = "GONE"
        local_row.quantity = 10
        local_row.t1_quantity = 0
        local_row.average_price = 100.0
        local_row.last_synced_at = "2026-09-27 10:00:00"

        def execute_side_effect(query, params=None):
            if "SELECT instrument_token, tradingsymbol, quantity" in query.text:
                m = MagicMock()
                m.fetchall.return_value = [local_row]
                return m
            if "SELECT SUM(CASE WHEN transaction_type" in query.text:
                m = MagicMock()
                m.fetchone.return_value = None
                return m
            if "SELECT action_type FROM corporate_actions" in query.text:
                m = MagicMock()
                m.fetchone.return_value = None
                return m
            return MagicMock()

        mock_db.execute.side_effect = execute_side_effect

        upserted = sync_holdings()
        assert upserted == 0

        calls = mock_db.execute.call_args_list
        reconciliation_call = [
            c for c in calls if "INSERT INTO holdings_reconciliation_log" in c[0][0].text
        ][0]
        params = reconciliation_call[0][1]

        assert params["reason"] == "DISCREPANCY"
        assert params["delta"] == -10

        zero_out_call = [c for c in calls if "UPDATE user_holdings" in c[0][0].text][0]
        assert zero_out_call[0][1]["token"] == 123
