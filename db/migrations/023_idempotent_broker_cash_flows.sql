-- Keep repeated imports of the same broker statement from duplicating flows.
CREATE UNIQUE INDEX IF NOT EXISTS uq_broker_ledger_cash_flow_reference
    ON portfolio_cash_flows (user_id, external_reference)
    WHERE source = 'BROKER_LEDGER'
      AND external_reference IS NOT NULL;
