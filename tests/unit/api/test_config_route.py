from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.config_store as config_store
from api.routes import config as config_route

_PAIRS = {"XBTEUR": {}, "ETHEUR": {}}
_FLAT = {
    "target_pct": 30.0,
    "hodl_pct": 10.0,
    "k_act": 2.0,
    "min_margin": 0.0,
    "stop_pct_ll": 0.9,
    "stop_pct_lv": 0.9,
    "stop_pct_mv": 0.9,
    "stop_pct_hv": 0.9,
    "stop_pct_hh": 0.9,
}


def _client(monkeypatch):
    monkeypatch.setattr(config_route, "PAIRS", _PAIRS)
    app = FastAPI()
    app.include_router(config_route.router)
    return TestClient(app)


def test_get_config_all(monkeypatch):
    monkeypatch.setattr(config_store, "get_all", lambda: {p: dict(_FLAT) for p in _PAIRS})
    body = _client(monkeypatch).get("/config").json()
    pairs = {item["pair"] for item in body}
    assert pairs == {"XBTEUR", "ETHEUR"}


def test_get_config_pair(monkeypatch):
    monkeypatch.setattr(config_store, "get_pair", lambda pair: dict(_FLAT))
    body = _client(monkeypatch).get("/config/XBTEUR").json()
    assert body["pair"] == "XBTEUR"
    assert body["k_act"] == 2.0


def test_get_config_unknown_pair_404(monkeypatch):
    assert _client(monkeypatch).get("/config/DOGEUR").status_code == 404


def test_patch_config_success(monkeypatch):
    captured = {}

    def _apply(pair, fields, updated_by=None):
        captured["pair"] = pair
        captured["fields"] = fields
        return {**_FLAT, **fields}

    monkeypatch.setattr(config_store, "apply_patch", _apply)
    resp = _client(monkeypatch).patch("/config/XBTEUR", json={"target_pct": 25.0})
    assert resp.status_code == 200
    assert resp.json()["target_pct"] == 25.0
    assert captured["fields"] == {"target_pct": 25.0}


def test_patch_config_explicit_null_k_act_is_forwarded(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        config_store,
        "apply_patch",
        lambda pair, fields, updated_by=None: captured.update(fields) or {**_FLAT, "k_act": None, "min_margin": 0.001},
    )
    resp = _client(monkeypatch).patch("/config/XBTEUR", json={"k_act": None, "min_margin": 0.001})
    assert resp.status_code == 200
    assert captured["k_act"] is None


def test_patch_config_validation_error_422(monkeypatch):
    def _apply(pair, fields, updated_by=None):
        raise config_store.ConfigValidationError(["XBTEUR_TARGET_PCT must be <= 100 (got 150)"])

    monkeypatch.setattr(config_store, "apply_patch", _apply)
    resp = _client(monkeypatch).patch("/config/XBTEUR", json={"target_pct": 99.0})
    assert resp.status_code == 422
    assert "TARGET_PCT" in resp.json()["detail"]


def test_patch_config_empty_body_422(monkeypatch):
    resp = _client(monkeypatch).patch("/config/XBTEUR", json={})
    assert resp.status_code == 422


def test_patch_config_unknown_pair_404(monkeypatch):
    assert _client(monkeypatch).patch("/config/DOGEUR", json={"target_pct": 1.0}).status_code == 404
