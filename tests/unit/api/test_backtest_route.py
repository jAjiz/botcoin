"""Unit tests for POST /backtest route."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import backtest as backtest_route
from trading.backtest import BacktestResult
from trading.engine import Operation

_PAIR = "XBTEUR"
_PAIRS = {_PAIR: {}}

_OPERATIONS = [
    Operation(
        idx=0,
        time="2026-01-01 00:00:00",
        side="buy",
        price=80000.0,
        vol="MV",
        k_stop=1.2,
        fee_abs=0.0,
        pnl_abs=None,
        pnl_pct=None,
        cum_pnl=None,
    ),
    Operation(
        idx=1,
        time="2026-01-02 00:00:00",
        side="sell",
        price=82000.0,
        vol="MV",
        k_stop=1.2,
        fee_abs=20.8,
        pnl_abs=2000.0,
        pnl_pct=2.5,
        cum_pnl=2000.0,
    ),
]

_SUMMARY = {
    "ops_count": 2,
    "pnl_samples": 1,
    "win_rate_pct": 100.0,
    "total_pnl_eur": 2000.0,
    "total_fees_eur": 20.8,
    "best_op_pnl_eur": 2000.0,
    "worst_op_pnl_eur": 2000.0,
    "avg_op_pnl_eur": 2000.0,
    "median_op_pnl_eur": 2000.0,
    "row_count": 100,
    "source": "recompute",
}


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setattr(backtest_route, "PAIRS", _PAIRS)
    app = FastAPI()
    app.include_router(backtest_route.router)
    return TestClient(app)


def test_post_backtest_unknown_pair_returns_400(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    resp = client.post("/backtest", json={"pair": "UNKNOWN"})
    assert resp.status_code == 400
    assert "Unknown pair" in resp.json()["detail"]


def test_post_backtest_returns_summary_and_operations(monkeypatch) -> None:
    fixed_result = BacktestResult(pair=_PAIR, fee_pct=0.26, summary=_SUMMARY, operations=_OPERATIONS)
    monkeypatch.setattr(backtest_route, "run_backtest", lambda req: fixed_result)
    client = _make_client(monkeypatch)
    resp = client.post("/backtest", json={"pair": _PAIR, "fee_pct": 0.26})
    assert resp.status_code == 200
    body = resp.json()
    assert body["pair"] == _PAIR
    assert body["fee_pct"] == 0.26
    assert body["summary"]["ops_count"] == 2
    assert body["summary"]["source"] == "recompute"
    assert len(body["operations"]) == 2
    assert body["operations"][0]["side"] == "buy"
    assert body["operations"][1]["pnl_abs"] == 2000.0
