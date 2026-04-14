import exchange.kraken as kraken


# ── get_balance ──────────────────────────────────────────────────────────────

def test_get_balance_returns_result_on_success(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api, "query_private",
        lambda *args, **kwargs: {"error": [], "result": {"ZEUR": "1000.0", "XXBT": "0.5"}},
    )

    result = kraken.get_balance()

    assert result == {"ZEUR": "1000.0", "XXBT": "0.5"}


def test_get_balance_returns_none_on_api_error(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api, "query_private",
        lambda *args, **kwargs: {"error": ["EGeneral:Invalid"], "result": {}},
    )

    result = kraken.get_balance()

    assert result is None


def test_get_balance_returns_none_on_exception(monkeypatch) -> None:
    def _raise(*args, **kwargs):
        raise RuntimeError("network error")

    monkeypatch.setattr(kraken.api, "query_private", _raise)

    result = kraken.get_balance()

    assert result is None


# ── get_order_status ─────────────────────────────────────────────────────────

def test_get_order_status_returns_status_on_success(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api, "query_private",
        lambda *args, **kwargs: {"error": [], "result": {"ORDER123": {"status": "closed"}}},
    )

    result = kraken.get_order_status("ORDER123")

    assert result == "closed"


def test_get_order_status_returns_none_on_api_error(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api, "query_private",
        lambda *args, **kwargs: {"error": ["EOrder:Unknown order"], "result": {}},
    )

    result = kraken.get_order_status("INVALID")

    assert result is None


# ── get_last_prices ───────────────────────────────────────────────────────────

def test_get_last_prices_returns_prices_on_success(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken, "_query_public_limited",
        lambda *args, **kwargs: {
            "error": [],
            "result": {"XXBTZEUR": {"c": ["82500.0"]}},
        },
    )
    pairs_dict = {"XBTEUR": {"primary": "XXBTZEUR"}}

    result = kraken.get_last_prices(pairs_dict)

    assert result == {"XBTEUR": 82500.0}


def test_get_last_prices_returns_none_on_api_error(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken, "_query_public_limited",
        lambda *args, **kwargs: {"error": ["EQuery:Unknown asset pair"]},
    )

    result = kraken.get_last_prices({"XBTEUR": {"primary": "XXBTZEUR"}})

    assert result is None


# ── place_limit_order ─────────────────────────────────────────────────────────

def test_place_limit_order_returns_order_id_on_success(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api, "query_private",
        lambda *args, **kwargs: {"error": [], "result": {"txid": ["ORDER456"]}},
    )

    result = kraken.place_limit_order("XBTEUR", "buy", 80000.0, 0.001)

    assert result == "ORDER456"


def test_place_limit_order_returns_none_on_api_error(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api, "query_private",
        lambda *args, **kwargs: {"error": ["EOrder:Insufficient funds"], "result": {}},
    )

    result = kraken.place_limit_order("XBTEUR", "buy", 80000.0, 0.001)

    assert result is None


# ── fetch_ohlc_data ───────────────────────────────────────────────────────────

_OHLC_ROW = ["1713052800", "80000.0", "81000.0", "79500.0", "80500.0", "80200.0", "1.5", 42]


def test_fetch_ohlc_data_returns_dataframe_on_valid_result(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken, "_query_public_limited",
        lambda *args, **kwargs: {
            "error": [],
            "result": {"XXBTZEUR": [_OHLC_ROW, _OHLC_ROW]},
        },
    )

    result = kraken.fetch_ohlc_data("XBTEUR", 15)

    assert result is not None
    assert not result.empty
    assert {"open", "high", "low", "close", "volume"}.issubset(result.columns)
    assert result["close"].dtype == float


def test_fetch_ohlc_data_passes_since_param(monkeypatch) -> None:
    captured = {}

    def _mock(method, data=None):
        captured["data"] = data
        return {"error": [], "result": {"XXBTZEUR": [_OHLC_ROW]}}

    monkeypatch.setattr(kraken, "_query_public_limited", _mock)

    kraken.fetch_ohlc_data("XBTEUR", 15, since=1713052800)

    assert captured["data"].get("since") == 1713052800


def test_fetch_ohlc_data_returns_none_on_api_error(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken, "_query_public_limited",
        lambda *args, **kwargs: {"error": ["EGeneral:Invalid"]},
    )

    result = kraken.fetch_ohlc_data("XBTEUR", 15)

    assert result is None


def test_fetch_ohlc_data_returns_empty_dataframe_on_empty_result(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken, "_query_public_limited",
        lambda *args, **kwargs: {"error": [], "result": {"XXBTZEUR": []}},
    )

    result = kraken.fetch_ohlc_data("XBTEUR", 15)

    assert result is not None
    assert result.empty