import core.config as config
import core.validation as validation


def test_validate_common_params_collects_expected_errors(monkeypatch) -> None:
    monkeypatch.setattr(validation, "KRAKEN_API_KEY", None)
    monkeypatch.setattr(validation, "KRAKEN_API_SECRET", None)
    monkeypatch.setattr(validation, "TELEGRAM_ENABLED", True)
    monkeypatch.setattr(validation, "TELEGRAM_TOKEN", None)
    monkeypatch.setattr(validation, "TELEGRAM_USER_ID", "abc")
    monkeypatch.setattr(validation, "TELEGRAM_POLL_INTERVAL", -1)
    monkeypatch.setattr(validation, "SLEEPING_INTERVAL", 0)
    monkeypatch.setattr(validation, "PARAM_SESSIONS", 0)
    monkeypatch.setattr(validation, "CANDLE_TIMEFRAME", 0)
    monkeypatch.setattr(validation, "ATR_PERIOD", 0)
    monkeypatch.setattr(validation, "ATR_DESV_LIMIT", -0.1)
    monkeypatch.setattr(validation, "PAIRS", {})

    errors = []
    validation.validate_common_params(errors)

    assert "KRAKEN_API_KEY is missing" in errors
    assert "KRAKEN_API_SECRET is missing" in errors
    assert "TELEGRAM_TOKEN is missing" in errors
    assert "TELEGRAM_USER_ID must be a positive integer" in errors
    assert "PAIRS is missing or empty" in errors


def test_build_and_validate_pairs_adds_error_on_exception(monkeypatch) -> None:
    def _boom(_pairs):
        raise RuntimeError("kraken error")

    monkeypatch.setattr(validation, "build_pairs_map", _boom)

    errors = []
    validation.build_and_validate_pairs(errors)

    assert errors
    assert "Failed to fetch pairs" in errors[0]


def test_build_and_validate_pairs_adds_error_when_no_valid_pairs(monkeypatch) -> None:
    monkeypatch.setattr(validation, "build_pairs_map", lambda _pairs: None)
    monkeypatch.setattr(validation, "PAIRS", {"XBTEUR": {}})

    errors = []
    validation.build_and_validate_pairs(errors)

    assert "No valid pairs found" in errors


def test_validate_config_returns_false_on_errors(monkeypatch) -> None:
    def _invalid(errors):
        errors.append("invalid")

    monkeypatch.setattr(validation, "validate_common_params", _invalid)

    assert validation.validate_config() is False


def test_validate_config_returns_true_on_success(monkeypatch) -> None:
    monkeypatch.setattr(validation, "validate_common_params", lambda errors: None)
    monkeypatch.setattr(validation, "build_and_validate_pairs", lambda errors: None)
    monkeypatch.setattr(validation, "validate_pair_params", lambda errors: None)

    called = {"summary": False}

    def _log_summary() -> None:
        called["summary"] = True

    monkeypatch.setattr(validation, "log_configuration_summary", _log_summary)

    assert validation.validate_config() is True
    assert called["summary"] is True


# ============================================================================
# validate_pair_params
# ============================================================================


def _stub_pair_config(
    monkeypatch,
    pair="XBTEUR",
    *,
    trading=None,
    allocation=None,
    stops=None,
):
    """Replace the config-module dicts that validate_pair_params reads/writes."""
    monkeypatch.setattr(config, "PAIRS", {pair: {}})
    default_trading = {"K_ACT": "1.2", "MIN_MARGIN": "0", "K_STOP": {"buy": {}, "sell": {}}}
    default_allocation = {"TARGET_PCT": "50", "HODL_PCT": "25"}
    default_stops = {lvl: "0.9" for lvl in ("LL", "LV", "MV", "HV", "HH")}
    monkeypatch.setattr(config, "TRADING_PARAMS", {pair: trading or default_trading})
    monkeypatch.setattr(config, "ASSET_ALLOCATION", {pair: allocation or default_allocation})
    monkeypatch.setattr(config, "STOP_PERCENTILES", {pair: stops or default_stops})


def test_validate_pair_params_happy_path_normalizes_values(monkeypatch) -> None:
    _stub_pair_config(monkeypatch)
    errors = []
    validation.validate_pair_params(errors)

    assert errors == []
    assert config.TRADING_PARAMS["XBTEUR"]["K_ACT"] == 1.2
    assert config.TRADING_PARAMS["XBTEUR"]["MIN_MARGIN"] == 0.0
    assert config.ASSET_ALLOCATION["XBTEUR"]["TARGET_PCT"] == 50.0
    assert config.ASSET_ALLOCATION["XBTEUR"]["HODL_PCT"] == 25.0
    assert config.STOP_PERCENTILES["XBTEUR"]["LL"] == 0.9


def test_validate_pair_params_empty_k_act_becomes_none_and_min_margin_used(monkeypatch) -> None:
    _stub_pair_config(
        monkeypatch,
        trading={"K_ACT": "", "MIN_MARGIN": "0.5", "K_STOP": {"buy": {}, "sell": {}}},
    )
    errors = []
    validation.validate_pair_params(errors)

    assert errors == []
    assert config.TRADING_PARAMS["XBTEUR"]["K_ACT"] is None
    assert config.TRADING_PARAMS["XBTEUR"]["MIN_MARGIN"] == 0.5


def test_validate_pair_params_min_margin_required_when_k_act_unset(monkeypatch) -> None:
    _stub_pair_config(
        monkeypatch,
        trading={"K_ACT": None, "MIN_MARGIN": "", "K_STOP": {"buy": {}, "sell": {}}},
    )
    errors = []
    validation.validate_pair_params(errors)
    # K_ACT unset AND MIN_MARGIN unset → error.
    assert any("XBTEUR_MIN_MARGIN is required" in e for e in errors)


def test_validate_pair_params_invalid_k_act_flagged(monkeypatch) -> None:
    _stub_pair_config(
        monkeypatch,
        trading={"K_ACT": "not-a-number", "MIN_MARGIN": "0", "K_STOP": {"buy": {}, "sell": {}}},
    )
    errors = []
    validation.validate_pair_params(errors)
    assert any("XBTEUR_K_ACT must be a float" in e for e in errors)


def test_validate_pair_params_min_margin_unused_when_k_act_defined(monkeypatch) -> None:
    _stub_pair_config(
        monkeypatch,
        trading={"K_ACT": "1.5", "MIN_MARGIN": "", "K_STOP": {"buy": {}, "sell": {}}},
    )
    errors = []
    validation.validate_pair_params(errors)
    # No error: MIN_MARGIN unused when K_ACT is defined. Normalized to 0.
    assert errors == []
    assert config.TRADING_PARAMS["XBTEUR"]["MIN_MARGIN"] == 0.0


def test_validate_pair_params_target_pct_over_100_flagged(monkeypatch) -> None:
    _stub_pair_config(monkeypatch, allocation={"TARGET_PCT": "120", "HODL_PCT": "10"})
    errors = []
    validation.validate_pair_params(errors)
    assert any("XBTEUR_TARGET_PCT must be <= 100" in e for e in errors)


def test_validate_pair_params_hodl_pct_over_100_flagged(monkeypatch) -> None:
    _stub_pair_config(monkeypatch, allocation={"TARGET_PCT": "50", "HODL_PCT": "150"})
    errors = []
    validation.validate_pair_params(errors)
    assert any("XBTEUR_HODL_PCT must be <= 100" in e for e in errors)


def test_validate_pair_params_target_pct_sum_over_100_flagged(monkeypatch) -> None:
    monkeypatch.setattr(config, "PAIRS", {"XBTEUR": {}, "ETHEUR": {}})
    trading = {"K_ACT": "1.2", "MIN_MARGIN": "0", "K_STOP": {"buy": {}, "sell": {}}}
    stops = {lvl: "0.9" for lvl in ("LL", "LV", "MV", "HV", "HH")}
    monkeypatch.setattr(config, "TRADING_PARAMS", {"XBTEUR": dict(trading), "ETHEUR": dict(trading)})
    monkeypatch.setattr(
        config,
        "ASSET_ALLOCATION",
        {
            "XBTEUR": {"TARGET_PCT": "60", "HODL_PCT": "10"},
            "ETHEUR": {"TARGET_PCT": "60", "HODL_PCT": "10"},
        },
    )
    monkeypatch.setattr(config, "STOP_PERCENTILES", {"XBTEUR": dict(stops), "ETHEUR": dict(stops)})

    errors = []
    validation.validate_pair_params(errors)
    assert any("Sum of TARGET_PCT across all pairs must not exceed 100" in e for e in errors)


def test_validate_pair_params_stop_pct_out_of_range_flagged(monkeypatch) -> None:
    stops = {lvl: "0.9" for lvl in ("LL", "LV", "MV", "HV", "HH")}
    stops["HH"] = "1.5"
    _stub_pair_config(monkeypatch, stops=stops)
    errors = []
    validation.validate_pair_params(errors)
    assert any("XBTEUR_STOP_PCT_HH must be <= 1" in e for e in errors)


def test_validate_pair_params_stop_pct_empty_uses_default(monkeypatch) -> None:
    stops = {lvl: "" for lvl in ("LL", "LV", "MV", "HV", "HH")}
    _stub_pair_config(monkeypatch, stops=stops)
    errors = []
    validation.validate_pair_params(errors)
    assert errors == []
    for lvl in ("LL", "LV", "MV", "HV", "HH"):
        assert config.STOP_PERCENTILES["XBTEUR"][lvl] == 0.90
