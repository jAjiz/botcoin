import pytest

import core.config as config
import exchange.kraken as kraken

# ============================================================================
# HTTP timeout (guards against a stalled socket blocking the caller forever)
# ============================================================================


def test_get_balance_passes_http_timeout(monkeypatch) -> None:
    captured: dict = {}

    def _mock(method, data=None, timeout=None):
        captured["timeout"] = timeout
        return {"error": [], "result": {}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)

    kraken.get_balance()

    assert captured["timeout"] == kraken.KRAKEN_HTTP_TIMEOUT
    assert captured["timeout"] is not None


def test_query_public_limited_passes_http_timeout_with_data(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(kraken, "_wait_rate_limit", lambda: None)

    def _mock(method, data=None, timeout=None):
        captured["timeout"] = timeout
        return {"error": [], "result": {}}

    monkeypatch.setattr(kraken.api, "query_public", _mock)

    kraken._query_public_limited("Ticker", {"pair": "XBTEUR"})

    assert captured["timeout"] == kraken.KRAKEN_HTTP_TIMEOUT


def test_query_public_limited_passes_http_timeout_without_data(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(kraken, "_wait_rate_limit", lambda: None)

    def _mock(method, data=None, timeout=None):
        captured["timeout"] = timeout
        return {"error": [], "result": {}}

    monkeypatch.setattr(kraken.api, "query_public", _mock)

    kraken._query_public_limited("AssetPairs")

    assert captured["timeout"] == kraken.KRAKEN_HTTP_TIMEOUT


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
# Order state
# ============================================================================


def test_get_order_state_returns_status_price_and_vol_exec_when_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {
            "error": [],
            "result": {"ORD001": {"status": "closed", "price": "69099.7", "vol_exec": "0.01000000"}},
        },
    )

    result = kraken.get_order_state("ORD001")

    assert result == kraken.OrderState(status="closed", avg_price=69099.7, vol_exec=0.01)


def test_get_order_state_returns_status_when_open_regardless_of_price(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {
            "error": [],
            "result": {"ORD001": {"status": "open", "price": "0.00000", "vol_exec": "0.00000000"}},
        },
    )

    result = kraken.get_order_state("ORD001")

    # The function reports whatever Kraken sends; it never infers completion
    # from the price itself. Callers must branch on status, not price.
    assert result == kraken.OrderState(status="open", avg_price=0.0, vol_exec=0.0)


def test_get_order_state_reports_zero_price_when_canceled(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {
            "error": [],
            "result": {"ORD001": {"status": "canceled", "price": "0.00000", "vol_exec": "0.00000000"}},
        },
    )

    result = kraken.get_order_state("ORD001")

    # This is exactly the case that used to be mistaken for a filled order at
    # price 0.0 by the old price-only helper: a canceled order with no fill.
    assert result == kraken.OrderState(status="canceled", avg_price=0.0, vol_exec=0.0)


def test_get_order_state_returns_none_when_order_id_missing_from_result(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": [], "result": {}},
    )

    assert kraken.get_order_state("ORD001") is None


def test_get_order_state_returns_none_on_api_error(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": ["EGeneral:Invalid"], "result": {}},
    )

    assert kraken.get_order_state("ORD001") is None


def test_get_order_state_passes_http_timeout(monkeypatch) -> None:
    captured: dict = {}

    def _mock(method, data=None, timeout=None):
        captured["timeout"] = timeout
        return {"error": [], "result": {"ORD001": {"status": "closed", "price": "1.0", "vol_exec": "1.0"}}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)

    kraken.get_order_state("ORD001")

    assert captured["timeout"] == kraken.KRAKEN_HTTP_TIMEOUT


# ============================================================================
# Cancel order
# ============================================================================


def test_cancel_order_returns_true_on_success(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": [], "result": {"count": 1}},
    )

    assert kraken.cancel_order("ORD001") is True


def test_cancel_order_returns_false_on_api_error(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": ["EOrder:Unknown order"], "result": {}},
    )

    assert kraken.cancel_order("ORD001") is False


def test_cancel_order_returns_false_when_nothing_was_canceled(monkeypatch) -> None:
    # count: 0 means Kraken accepted the request but canceled nothing (e.g. the
    # order already reached a terminal state) — must not be read as "canceled".
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": [], "result": {"count": 0}},
    )

    assert kraken.cancel_order("ORD001") is False


def test_cancel_order_returns_false_when_cancellation_is_pending(monkeypatch) -> None:
    # pending: true means cancellation is queued but not yet definitive — the
    # order can still fill, so the caller must not treat this as a dead order.
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": [], "result": {"count": 1, "pending": True}},
    )

    assert kraken.cancel_order("ORD001") is False


def test_cancel_order_passes_http_timeout(monkeypatch) -> None:
    captured: dict = {}

    def _mock(method, data=None, timeout=None):
        captured["timeout"] = timeout
        return {"error": [], "result": {"count": 1}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)

    kraken.cancel_order("ORD001")

    assert captured["timeout"] == kraken.KRAKEN_HTTP_TIMEOUT


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


def test_get_last_prices_skips_pair_missing_from_ticker_response(monkeypatch) -> None:
    # ETHEUR's primary ("XETHZEUR") is absent from the Ticker result (e.g. Kraken
    # dropped it, or the pair metadata is stale) — this must not take down XBTEUR.
    monkeypatch.setattr(
        kraken,
        "_query_public_limited",
        lambda *args, **kwargs: {
            "error": [],
            "result": {"XXBTZEUR": {"c": ["82500.0"]}},
        },
    )
    pairs_dict = {"XBTEUR": {"primary": "XXBTZEUR"}, "ETHEUR": {"primary": "XETHZEUR"}}

    result = kraken.get_last_prices(pairs_dict)

    assert result == {"XBTEUR": 82500.0}


def test_get_last_prices_returns_none_when_no_pair_resolves(monkeypatch) -> None:
    # Ticker responded successfully but with none of the requested pairs.
    monkeypatch.setattr(
        kraken,
        "_query_public_limited",
        lambda *args, **kwargs: {"error": [], "result": {}},
    )
    pairs_dict = {"XBTEUR": {"primary": "XXBTZEUR"}, "ETHEUR": {"primary": "XETHZEUR"}}

    result = kraken.get_last_prices(pairs_dict)

    assert result is None


@pytest.mark.parametrize(
    "bad_entry",
    [
        {},  # present but no "c" key
        {"c": []},  # "c" present but empty
        {"c": ["not-a-number"]},  # unparseable price
        {"c": [None]},
    ],
    ids=["missing_c", "empty_c", "non_numeric", "null_price"],
)
def test_get_last_prices_skips_pair_with_malformed_entry(monkeypatch, bad_entry) -> None:
    # A pair PRESENT in the response but with an unusable payload must be skipped
    # like an absent one. Parsing happens after _safe_call returned and outside the
    # scheduler's per-pair try, so an unhandled raise here kills pricing for every
    # pair and fails the whole session.
    monkeypatch.setattr(
        kraken,
        "_query_public_limited",
        lambda *args, **kwargs: {
            "error": [],
            "result": {"XXBTZEUR": {"c": ["82500.0"]}, "XETHZEUR": bad_entry},
        },
    )
    pairs_dict = {"XBTEUR": {"primary": "XXBTZEUR"}, "ETHEUR": {"primary": "XETHZEUR"}}

    result = kraken.get_last_prices(pairs_dict)

    assert result == {"XBTEUR": 82500.0}


def test_get_last_prices_returns_none_when_every_entry_is_malformed(monkeypatch) -> None:
    # Nothing resolved -> None, so call_with_retry retries and the session reports
    # "could not fetch prices" rather than proceeding with an empty price map.
    monkeypatch.setattr(
        kraken,
        "_query_public_limited",
        lambda *args, **kwargs: {"error": [], "result": {"XXBTZEUR": {"c": []}}},
    )
    pairs_dict = {"XBTEUR": {"primary": "XXBTZEUR"}}

    result = kraken.get_last_prices(pairs_dict)

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

    def _mock(method, data=None, timeout=None):
        captured["data"] = data
        return {"error": [], "result": {"txid": ["ORDER789"]}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)
    monkeypatch.setitem(config.PAIRS, "USDCEUR", {"pair_decimals": 4, "lot_decimals": 8})

    kraken.place_limit_order("USDCEUR", "sell", 1.031274, 12.123456789)

    assert captured["data"]["price"] == "1.0313"
    assert captured["data"]["volume"] == "12.12345679"


def test_place_limit_order_without_known_decimals_sends_unrounded(monkeypatch) -> None:
    captured: dict = {}

    def _mock(method, data=None, timeout=None):
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
