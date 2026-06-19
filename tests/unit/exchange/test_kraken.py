import core.config as config
import exchange.kraken as kraken

# ============================================================================
# Pairs map
# ============================================================================


def test_build_pairs_map_populates_metadata_and_decimals(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken,
        "get_asset_pairs",
        lambda: {
            "XXBTZEUR": {
                "altname": "XBTEUR",
                "wsname": "XBT/EUR",
                "base": "XXBT",
                "quote": "ZEUR",
                "pair_decimals": 1,
                "lot_decimals": 8,
                "cost_decimals": 5,
            }
        },
    )
    pairs_dict: dict[str, dict] = {"XBTEUR": {}}

    kraken.build_pairs_map(pairs_dict)

    assert pairs_dict["XBTEUR"] == {
        "primary": "XXBTZEUR",
        "wsname": "XBT/EUR",
        "base": "XXBT",
        "quote": "ZEUR",
        "pair_decimals": 1,
        "lot_decimals": 8,
        "cost_decimals": 5,
    }


# ============================================================================
# Balance
# ============================================================================


def test_get_balance_returns_result_on_success(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": [], "result": {"ZEUR": "1000.0", "XXBT": "0.5"}},
    )

    result = kraken.get_balance()

    assert result == {"ZEUR": "1000.0", "XXBT": "0.5"}


def test_get_balance_returns_none_on_api_error(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
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


# ============================================================================
# Closing price
# ============================================================================


def test_get_order_closing_price_returns_price_when_filled(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": [], "result": {"ORD001": {"status": "closed", "price": "69099.7"}}},
    )

    assert kraken.get_order_closing_price("ORD001") == 69099.7


def test_get_order_closing_price_returns_none_when_not_filled(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": [], "result": {"ORD001": {"status": "open", "price": "0"}}},
    )

    assert kraken.get_order_closing_price("ORD001") is None


# ============================================================================
# Last prices
# ============================================================================


def test_get_last_prices_returns_prices_on_success(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken,
        "_query_public_limited",
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
        kraken,
        "_query_public_limited",
        lambda *args, **kwargs: {"error": ["EQuery:Unknown asset pair"]},
    )

    result = kraken.get_last_prices({"XBTEUR": {"primary": "XXBTZEUR"}})

    assert result is None


# ============================================================================
# Limit orders
# ============================================================================


def test_place_limit_order_returns_order_id_on_success(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": [], "result": {"txid": ["ORDER456"]}},
    )

    result = kraken.place_limit_order("XBTEUR", "buy", 80000.0, 0.001)

    assert result == "ORDER456"


def test_place_limit_order_rounds_to_pair_precision(monkeypatch) -> None:
    captured: dict = {}

    def _mock(method, data=None):
        captured["data"] = data
        return {"error": [], "result": {"txid": ["ORDER789"]}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)
    monkeypatch.setitem(config.PAIRS, "USDCEUR", {"pair_decimals": 4, "lot_decimals": 8})

    kraken.place_limit_order("USDCEUR", "sell", 1.031274, 12.123456789)

    assert captured["data"]["price"] == "1.0313"
    assert captured["data"]["volume"] == "12.12345679"


def test_place_limit_order_without_known_decimals_sends_unrounded(monkeypatch) -> None:
    captured: dict = {}

    def _mock(method, data=None):
        captured["data"] = data
        return {"error": [], "result": {"txid": ["ORDER000"]}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)
    monkeypatch.delitem(config.PAIRS, "ZZZEUR", raising=False)

    kraken.place_limit_order("ZZZEUR", "buy", 1.2345, 0.5)

    assert captured["data"]["price"] == "1.2345"
    assert captured["data"]["volume"] == "0.5"


def test_place_limit_order_returns_none_on_api_error(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": ["EOrder:Insufficient funds"], "result": {}},
    )

    result = kraken.place_limit_order("XBTEUR", "buy", 80000.0, 0.001)

    assert result is None


# ============================================================================
# OHLC data
# ============================================================================


_OHLC_ROW = ["1713052800", "80000.0", "81000.0", "79500.0", "80500.0", "80200.0", "1.5", 42]


def test_fetch_ohlc_data_returns_dataframe_and_last_on_valid_result(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken,
        "_query_public_limited",
        lambda *args, **kwargs: {
            "error": [],
            "result": {"XXBTZEUR": [_OHLC_ROW, _OHLC_ROW], "last": 1713053700},
        },
    )

    result = kraken.fetch_ohlc_data("XBTEUR", 15)

    assert result is not None
    df, last = result
    assert not df.empty
    assert {"open", "high", "low", "close", "volume"}.issubset(df.columns)
    assert df["close"].dtype == float
    assert last == 1713053700


def test_fetch_ohlc_data_passes_since_param(monkeypatch) -> None:
    captured = {}

    def _mock(method, data=None):
        captured["data"] = data
        return {"error": [], "result": {"XXBTZEUR": [_OHLC_ROW], "last": 1713053700}}

    monkeypatch.setattr(kraken, "_query_public_limited", _mock)

    result = kraken.fetch_ohlc_data("XBTEUR", 15, since=1713052800)

    assert captured["data"].get("since") == 1713052800
    assert result is not None
    df, last = result
    assert not df.empty
    assert last == 1713053700


def test_fetch_ohlc_data_returns_none_on_api_error(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken,
        "_query_public_limited",
        lambda *args, **kwargs: {"error": ["EGeneral:Invalid"]},
    )

    result = kraken.fetch_ohlc_data("XBTEUR", 15)

    assert result is None


def test_fetch_ohlc_data_returns_empty_dataframe_and_last_on_empty_result(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken,
        "_query_public_limited",
        lambda *args, **kwargs: {"error": [], "result": {"XXBTZEUR": [], "last": 1713053700}},
    )

    result = kraken.fetch_ohlc_data("XBTEUR", 15)

    assert result is not None
    df, last = result
    assert df.empty
    assert last == 1713053700
