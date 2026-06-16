import pytest

import core.config as config
import core.config_store as config_store


def _seed_dicts(monkeypatch, pairs):
    monkeypatch.setattr(config, "PAIRS", pairs)
    monkeypatch.setattr(config_store, "PAIRS", pairs)
    monkeypatch.setattr(
        config,
        "TRADING_PARAMS",
        {p: {"K_ACT": 2.0, "MIN_MARGIN": 0.0, "K_STOP": {"buy": {}, "sell": {}}} for p in pairs},
    )
    monkeypatch.setattr(config, "ASSET_ALLOCATION", {p: {"TARGET_PCT": 30.0, "HODL_PCT": 10.0} for p in pairs})
    monkeypatch.setattr(
        config,
        "STOP_PERCENTILES",
        {p: {lvl: 0.90 for lvl in config.VOLATILITY_LEVELS} for p in pairs},
    )


def test_load_or_seed_inserts_when_row_absent(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    monkeypatch.setattr(config_store.db, "load_all_pair_config", lambda: {})
    saved = {}
    monkeypatch.setattr(
        config_store.db, "upsert_pair_config", lambda pair, values, updated_by=None: saved.update({pair: values})
    )

    config_store.load_or_seed()

    assert saved["XBTEUR"]["k_act"] == 2.0
    assert saved["XBTEUR"]["target_pct"] == 30.0


def test_load_or_seed_loads_when_row_present(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    row = {
        "pair": "XBTEUR", "target_pct": 40.0, "hodl_pct": 5.0, "k_act": None,
        "min_margin": 0.002, "stop_pct_ll": 0.8, "stop_pct_lv": 0.8, "stop_pct_mv": 0.8,
        "stop_pct_hv": 0.8, "stop_pct_hh": 0.9, "updated_at": None, "updated_by": None,
    }
    monkeypatch.setattr(config_store.db, "load_all_pair_config", lambda: {"XBTEUR": row})
    monkeypatch.setattr(
        config_store.db, "upsert_pair_config", lambda *a, **k: pytest.fail("should not seed when row present")
    )

    config_store.load_or_seed()

    assert config.get_pair_config("XBTEUR")["target_pct"] == 40.0
    assert config.get_pair_config("XBTEUR")["k_act"] is None
    assert config.get_pair_config("XBTEUR")["min_margin"] == 0.002


def test_apply_patch_unknown_pair_raises(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    with pytest.raises(config_store.UnknownPairError):
        config_store.apply_patch("DOGEUR", {"target_pct": 1.0})


def test_apply_patch_updates_dict_and_persists(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    saved = {}
    monkeypatch.setattr(
        config_store.db, "upsert_pair_config", lambda pair, values, updated_by=None: saved.update({pair: values})
    )

    result = config_store.apply_patch("XBTEUR", {"target_pct": 25.0}, updated_by="api")

    assert result["target_pct"] == 25.0
    assert config.get_pair_config("XBTEUR")["target_pct"] == 25.0
    assert saved["XBTEUR"]["target_pct"] == 25.0


def test_apply_patch_invalid_leaves_dict_and_db_untouched(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    monkeypatch.setattr(config_store.db, "upsert_pair_config", lambda *a, **k: pytest.fail("must not persist on invalid"))

    with pytest.raises(config_store.ConfigValidationError):
        config_store.apply_patch("XBTEUR", {"target_pct": 150.0})

    assert config.get_pair_config("XBTEUR")["target_pct"] == 30.0  # unchanged


def test_apply_patch_target_sum_over_100_rejected(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR", "ETHEUR"])  # each currently 30
    monkeypatch.setattr(config_store.db, "upsert_pair_config", lambda *a, **k: pytest.fail("must not persist"))

    with pytest.raises(config_store.ConfigValidationError) as exc:
        config_store.apply_patch("XBTEUR", {"target_pct": 95.0})  # 95 + 30 > 100
    assert any("TARGET_PCT" in e for e in exc.value.errors)


def test_apply_patch_stop_pct_change_sets_dirty_flag(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    monkeypatch.setattr(config_store.db, "upsert_pair_config", lambda *a, **k: None)
    marked = []
    monkeypatch.setattr(config_store.runtime, "mark_config_dirty", lambda pair: marked.append(pair))

    config_store.apply_patch("XBTEUR", {"stop_pct_hh": 0.95})
    assert marked == ["XBTEUR"]


def test_apply_patch_non_stop_change_does_not_set_dirty_flag(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    monkeypatch.setattr(config_store.db, "upsert_pair_config", lambda *a, **k: None)
    marked = []
    monkeypatch.setattr(config_store.runtime, "mark_config_dirty", lambda pair: marked.append(pair))

    config_store.apply_patch("XBTEUR", {"target_pct": 20.0})
    assert marked == []


def test_apply_patch_k_act_null_keeps_min_margin(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    monkeypatch.setattr(config_store.db, "upsert_pair_config", lambda *a, **k: None)
    result = config_store.apply_patch("XBTEUR", {"k_act": None, "min_margin": 0.001})
    assert result["k_act"] is None
    assert result["min_margin"] == 0.001
