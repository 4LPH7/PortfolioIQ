"""Scanner rank and verdict on rebalance orders: a plain note beside each order, nothing blocked."""

from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import patch

import pytest

from src.analytics import scanner
from src.analytics.drift_detector import DriftDirection, DriftSignal, DriftType
from src.analytics.rebalancer import (
    OrderSide,
    RebalanceOrder,
    annotate_scanner,
    generate_rebalance_plan,
    get_rebalance_summary,
)
from src.analytics.valuator import PortfolioValuation


def _order(side=OrderSide.BUY, symbol="INFY"):
    return RebalanceOrder(
        tradingsymbol=symbol,
        exchange="NSE",
        instrument_token=1,
        side=side,
        quantity=10,
        estimated_price=Decimal("1500"),
    )


def _lookup(rank, status="edge", of=476):
    return {"INFY": {"rank": rank, "of": of, "status": status}}


def test_buy_inside_the_top_100_of_a_ranking_with_an_edge():
    o = _order()
    annotate_scanner(o, _lookup(12))
    assert (o.scanner_rank, o.scanner_status) == (12, "edge")
    assert o.scanner_note == "Scanner rank 12 of 476, from a ranking with a tested edge."


def test_buy_outside_the_top_100_of_a_ranking_with_an_edge():
    o = _order()
    annotate_scanner(o, _lookup(300))
    assert o.scanner_note == (
        "Scanner rank 300 of 476: outside the top 100 that a ranking with a tested edge favours."
    )


def test_buy_when_the_ranking_has_no_tested_edge_is_context_only():
    o = _order()
    annotate_scanner(o, _lookup(40, status="no_edge"))
    assert o.scanner_note == (
        "Scanner rank 40 of 476, but the ranking has no tested edge, so treat it as context."
    )


def test_buy_with_no_rank_and_no_edge():
    o = _order()
    annotate_scanner(o, {"INFY": {"rank": None, "of": 476, "status": "no_edge"}})
    assert o.scanner_rank is None
    assert o.scanner_note == "Scanner has no tested edge, so there is no rank to weigh."


def test_buy_missing_from_the_scan_says_so():
    o = _order(symbol="SMALLCO")
    annotate_scanner(o, _lookup(5))
    assert o.scanner_rank is None
    assert o.scanner_note == (
        "Not ranked by the scanner: outside the NIFTY 500 or left out by a data check."
    )


def test_sell_gets_the_rank_but_no_note():
    o = _order(side=OrderSide.SELL)
    annotate_scanner(o, _lookup(12))
    assert o.scanner_rank == 12 and o.scanner_note is None


def test_no_scan_leaves_orders_untouched():
    for empty in (None, {}):
        o = _order()
        annotate_scanner(o, empty)
        assert (o.scanner_rank, o.scanner_status, o.scanner_note) == (None, None, None)


def _plan(scanner_lookup):
    signal = DriftSignal(
        drift_type=DriftType.HOLDING,
        name="INFY",
        current_weight_pct=Decimal("5.0"),
        target_weight_pct=Decimal("8.0"),
        drift_pct=Decimal("-3.0"),
        threshold_pct=Decimal("0.5"),
        direction=DriftDirection.UNDERWEIGHT,
        severity="MEDIUM",
        rebalance_amount=Decimal("15000.00"),
    )
    val = PortfolioValuation(
        user_id="default",
        holdings=[],
        total_aum=Decimal("500000"),
        available_cash=Decimal("50000"),
        net_worth=Decimal("550000"),
    )
    holding = {
        "instrument_token": 408065,
        "exchange": "NSE",
        "quantity": 10,
        "t1_quantity": 0,
        "average_price": Decimal("1500"),
        "current_price": Decimal("1500"),
        "lot_size": 1,
    }
    with (
        patch("src.analytics.rebalancer.compute_portfolio_valuation", return_value=val),
        patch("src.analytics.rebalancer.detect_drift", return_value=[signal]),
        patch("src.analytics.rebalancer._get_holding_details", return_value=holding),
    ):
        return get_rebalance_summary(scanner_lookup=scanner_lookup), generate_rebalance_plan(
            scanner_lookup=scanner_lookup
        )


def test_the_planner_annotates_orders_and_the_summary_carries_the_fields():
    summary, plan = _plan(_lookup(12))
    (order,) = plan.orders
    assert order.side is OrderSide.BUY and order.scanner_rank == 12
    row = summary["orders"][0]
    assert (row["scanner_rank"], row["scanner_status"]) == (12, "edge")
    assert row["scanner_note"].startswith("Scanner rank 12 of 476")
    assert len(summary["orders"]) == 1  # annotating never drops an order


def test_without_a_scan_the_summary_fields_are_null():
    summary, _ = _plan(None)
    row = summary["orders"][0]
    assert (row["scanner_rank"], row["scanner_status"], row["scanner_note"]) == (None, None, None)


# ── rank_lookup and the routes ──


def _scan(status, ranking, stocks_ranked=476):
    return {
        "latest": {"result": {"status": status, "ranking": ranking, "stocks_ranked": stocks_ranked}}
    }


def test_rank_lookup_reads_the_latest_scan():
    ranking = [{"symbol": "INFY", "rank": 3}, {"symbol": "TCS", "rank": None}]
    with patch.object(scanner, "latest_scan", return_value=_scan("edge", ranking)):
        got = scanner.rank_lookup()
    assert got == {
        "INFY": {"rank": 3, "of": 476, "status": "edge"},
        "TCS": {"rank": None, "of": 476, "status": "edge"},
    }


def test_rank_lookup_is_empty_without_a_completed_scan():
    with patch.object(scanner, "latest_scan", return_value={"latest": None}):
        assert scanner.rank_lookup() == {}


@pytest.fixture
def client():
    from flask_app import app

    os.environ["TESTING"] = "true"
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_both_rebalance_routes_pass_the_lookup(client):
    from src.config.settings import get_settings

    headers = {"X-API-Key": get_settings().portfolioiq_api_key}
    lookup = _lookup(7)
    with (
        patch.object(scanner, "rank_lookup", return_value=lookup),
        patch("src.analytics.rebalancer.get_rebalance_summary", return_value={}) as summary,
    ):
        assert client.get("/api/v1/rebalance/orders", headers=headers).status_code == 200
        assert client.post("/api/v1/rebalance/preview", headers=headers, json={}).status_code == 200
    assert [c.kwargs.get("scanner_lookup") for c in summary.call_args_list] == [lookup, lookup]


def test_scanner_rank_route_returns_one_stock_or_null(client):
    from src.config.settings import get_settings

    headers = {"X-API-Key": get_settings().portfolioiq_api_key}
    with patch.object(scanner, "rank_lookup", return_value=_lookup(7)):
        hit = client.get("/api/v1/scanner/rank/infy", headers=headers)
        miss = client.get("/api/v1/scanner/rank/NOPE", headers=headers)
    assert hit.get_json()["data"] == {"rank": 7, "of": 476, "status": "edge"}
    assert miss.status_code == 200 and miss.get_json()["data"] is None
    assert client.get("/api/v1/scanner/rank/INFY").status_code == 401
