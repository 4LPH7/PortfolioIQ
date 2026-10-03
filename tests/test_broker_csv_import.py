from decimal import Decimal

from src.ingestion.broker_csv_import import parse_broker_csv


def test_parse_holdings_and_positions_as_separate_resources():
    holdings = parse_broker_csv(
        "holdings.csv",
        b"Instrument,Qty.,Avg. cost,LTP\r\nABC,2,10.50,12.25\r\n",
    )
    positions = parse_broker_csv(
        "positions.csv",
        b"Product,Instrument,Qty.,Avg.,LTP,P&L,Chg.\r\nCNC,XYZ,3,20,19, -3, -1.5%\r\n",
    )

    assert holdings["kind"] == "holdings"
    assert holdings["rows"][0]["average_price"] == Decimal("10.50")
    assert positions["kind"] == "positions"
    assert positions["rows"][0]["quantity"] == 3
    assert positions["rows"][0]["pnl"] == Decimal("-3")


def test_parse_ledger_without_turning_trades_into_external_flows():
    parsed = parse_broker_csv(
        "ledger.csv",
        b"particulars,posting_date,cost_center,voucher_type,debit,credit,net_balance\r\n"
        b"Bank receipt,01-10-2026,,Receipt,0,1000,1000\r\n"
        b"DP charge,02-10-2026,,Journal Entry,15,0,985\r\n"
        b"Equity settlement,02-10-2026,,Book Voucher,500,0,485\r\n",
    )

    assert parsed["kind"] == "ledger"
    assert [row["entry_kind"] for row in parsed["rows"]] == [
        "DEPOSIT",
        "CHARGE",
        "TRADE_SETTLEMENT",
    ]
