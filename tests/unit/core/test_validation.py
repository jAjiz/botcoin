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
    monkeypatch.setattr(validation, "MARKET_DATA_DAYS", 0)
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

    called = {"summary": False}

    def _log_summary() -> None:
        called["summary"] = True

    monkeypatch.setattr(validation, "log_configuration_summary", _log_summary)

    assert validation.validate_config() is True
    assert called["summary"] is True
