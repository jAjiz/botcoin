"""Tests for POST /backtest."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.routes.backtest as backtest_route_mod
from api.routes.backtest import router
from trading.backtest import BacktestResult


def _make_app():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


_RESULT = BacktestResult(
    pair="XBTEUR",
    fee_pct=0.0,
    total_ops=3,
    pnl_samples=2,
    win_rate_pct=100.0,
    total_pnl_abs=200.0,
    avg_pnl_abs=100.0,
    median_pnl_abs=100.0,
    best_pnl_abs=150.0,
    worst_pnl_abs=50.0,
    total_fees_abs=0.0,
    cum_pnl_pct=5.0,
    operations=[],
)


def test_post_backtest_returns_200_with_result(monkeypatch):
    monkeypatch.setattr(backtest_route_mod, "run_backtest", lambda req: _RESULT)

    client = _make_app()
    resp = client.post("/backtest", json={"pair": "XBTEUR"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["pair"] == "XBTEUR"
    assert body["total_ops"] == 3
    assert body["win_rate_pct"] == pytest.approx(100.0)


def test_post_backtest_forwards_optional_fields(monkeypatch):
    captured = {}

    def _fake_run(req):
        captured["req"] = req
        return _RESULT

    monkeypatch.setattr(backtest_route_mod, "run_backtest", _fake_run)

    client = _make_app()
    client.post(
        "/backtest",
        json={"pair": "XBTEUR", "fee_pct": 0.26, "start": "2026-01-01", "end": "2026-06-01", "max_ops": 10},
    )

    req = captured["req"]
    assert req.fee_pct == pytest.approx(0.26)
    assert req.start == "2026-01-01"
    assert req.end == "2026-06-01"
    assert req.max_ops == 10


def test_post_backtest_returns_404_for_unknown_pair(monkeypatch):
    monkeypatch.setattr(backtest_route_mod, "run_backtest", lambda req: (_ for _ in ()).throw(KeyError("UNKNOWN")))

    client = _make_app()
    resp = client.post("/backtest", json={"pair": "UNKNOWN"})

    assert resp.status_code == 404


def test_post_backtest_returns_500_on_unexpected_error(monkeypatch):
    monkeypatch.setattr(backtest_route_mod, "run_backtest", lambda req: (_ for _ in ()).throw(RuntimeError("db error")))

    client = _make_app()
    resp = client.post("/backtest", json={"pair": "XBTEUR"})

    assert resp.status_code == 500
