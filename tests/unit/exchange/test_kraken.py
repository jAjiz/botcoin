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
                "ordermin": "0.0001",
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
        "ordermin": 0.0001,
    }


def test_build_pairs_map_ordermin_absent_reads_none(monkeypatch) -> None:
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

    assert pairs_dict["XBTEUR"]["ordermin"] is None


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

    assert result == kraken.OrderState(status=kraken.OrderStatus.CLOSED, avg_price=69099.7, vol_exec=0.01)


def test_get_order_state_returns_total_ordered_volume(monkeypatch) -> None:
    """`vol` is the order's own size. Callers compare it against `vol_exec` to
    decide whether anything is left, instead of trusting their own bookkeeping."""
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {
            "error": [],
            "result": {
                "ORD001": {"status": "canceled", "price": "69099.7", "vol": "0.02000000", "vol_exec": "0.01000000"}
            },
        },
    )

    result = kraken.get_order_state("ORD001")

    assert result == kraken.OrderState(status=kraken.OrderStatus.CANCELED, avg_price=69099.7, vol=0.02, vol_exec=0.01)


def test_get_order_state_reports_zero_total_volume_when_kraken_omits_it(monkeypatch) -> None:
    """An unknown order size must never read as 'fully executed'; 0.0 makes the
    remainder check fail closed."""
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {
            "error": [],
            "result": {"ORD001": {"status": "canceled", "price": "0.00000", "vol_exec": "0.00000000"}},
        },
    )

    result = kraken.get_order_state("ORD001")

    assert result.vol == 0.0


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
    assert result == kraken.OrderState(status=kraken.OrderStatus.OPEN, avg_price=0.0, vol_exec=0.0)


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
    assert result == kraken.OrderState(status=kraken.OrderStatus.CANCELED, avg_price=0.0, vol_exec=0.0)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pending", kraken.OrderStatus.PENDING),
        ("open", kraken.OrderStatus.OPEN),
        ("closed", kraken.OrderStatus.CLOSED),
        ("canceled", kraken.OrderStatus.CANCELED),
        # Both mean the same thing to a caller: off the book, no further fills.
        ("expired", kraken.OrderStatus.CANCELED),
    ],
)
def test_map_order_status_translates_kraken_vocabulary(raw, expected) -> None:
    assert kraken.map_order_status(raw) is expected


@pytest.mark.parametrize("raw", ["some-status-kraken-invented", "", None])
def test_map_order_status_reports_anything_unmodelled_as_unknown(raw) -> None:
    """An unmodelled status must never be guessed at: callers treat UNKNOWN as
    'do not act', because acting on it could leave two live exits."""
    assert kraken.map_order_status(raw) is kraken.OrderStatus.UNKNOWN


def test_get_order_state_reports_not_found_when_order_id_missing_from_result(monkeypatch) -> None:
    """Kraken answered and does not know the order. That is a fact about the order,
    not an API failure, so it must be distinguishable from None."""
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": [], "result": {}},
    )

    state = kraken.get_order_state("ORD001")

    assert state is not None
    assert state.status is kraken.OrderStatus.NOT_FOUND


def test_get_order_state_reports_unknown_when_kraken_omits_the_status(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": [], "result": {"ORD001": {"price": "1.0", "vol_exec": "1.0"}}},
    )

    assert kraken.get_order_state("ORD001").status is kraken.OrderStatus.UNKNOWN


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
# Order lookup by client order id
# ============================================================================


def _order(status: str, cl_ord_id: str = "abc123", price: str = "0.0") -> dict:
    return {"status": status, "price": price, "vol": "0.5", "vol_exec": "0.0", "cl_ord_id": cl_ord_id}


def test_find_order_by_cl_ord_id_resolves_a_resting_order_without_calling_closed_orders(monkeypatch) -> None:
    calls: list[str] = []

    def _mock(method, data=None, timeout=None):
        calls.append(method)
        if method == "OpenOrders":
            return {"error": [], "result": {"open": {"TXID2": _order("open")}}}
        pytest.fail("ClosedOrders must not be called once the order is found resting")

    monkeypatch.setattr(kraken.api, "query_private", _mock)

    result = kraken.find_order_by_cl_ord_id("abc123")

    assert result == kraken.OrderLookup(
        txid="TXID2",
        state=kraken.OrderState(status=kraken.OrderStatus.OPEN, avg_price=0.0, vol=0.5, vol_exec=0.0),
    )
    assert calls == ["OpenOrders"]


def test_find_order_by_cl_ord_id_falls_back_to_closed_orders_on_miss(monkeypatch) -> None:
    calls: list[str] = []

    def _mock(method, data=None, timeout=None):
        calls.append(method)
        if method == "OpenOrders":
            return {"error": [], "result": {"open": {}}}
        if method == "ClosedOrders":
            closed = {"TXID1": {**_order("closed", price="100.0"), "vol_exec": "0.5"}}
            return {"error": [], "result": {"closed": closed}}
        pytest.fail(f"unexpected method {method}")

    monkeypatch.setattr(kraken.api, "query_private", _mock)

    result = kraken.find_order_by_cl_ord_id("abc123")

    assert result == kraken.OrderLookup(
        txid="TXID1",
        state=kraken.OrderState(status=kraken.OrderStatus.CLOSED, avg_price=100.0, vol=0.5, vol_exec=0.5),
    )
    assert calls == ["OpenOrders", "ClosedOrders"]


def test_find_order_by_cl_ord_id_prefers_a_resting_order_over_a_dead_one_carrying_the_same_id(monkeypatch) -> None:
    """Unreachable with per-attempt ids, so it means an assumption broke. Adopting the
    terminal one would finalize the trade and orphan the exit that is still resting —
    which is why the resting endpoint is the one asked first."""

    def _mock(method, data=None, timeout=None):
        if method == "OpenOrders":
            return {"error": [], "result": {"open": {"TXID_LIVE": _order("open")}}}
        return {"error": [], "result": {"closed": {"TXID_DEAD": _order("canceled")}}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)

    result = kraken.find_order_by_cl_ord_id("abc123")

    assert result.txid == "TXID_LIVE"
    assert result.state.status is kraken.OrderStatus.OPEN


def test_find_order_by_cl_ord_id_returns_absent_when_missing_from_both(monkeypatch) -> None:
    def _mock(method, data=None, timeout=None):
        key = "closed" if method == "ClosedOrders" else "open"
        return {"error": [], "result": {key: {}}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)

    result = kraken.find_order_by_cl_ord_id("abc123")

    assert result == kraken.OrderLookup(txid=None, state=None)


def test_find_order_by_cl_ord_id_returns_none_when_open_orders_errors_and_closed_is_empty(monkeypatch) -> None:
    def _mock(method, data=None, timeout=None):
        if method == "OpenOrders":
            return {"error": ["EGeneral:Invalid"], "result": {}}
        return {"error": [], "result": {"closed": {}}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)

    assert kraken.find_order_by_cl_ord_id("abc123") is None


def test_find_order_by_cl_ord_id_still_asks_closed_orders_when_open_orders_errors(monkeypatch) -> None:
    """One endpoint failing is not a reason to give up on the other: the answer may be there."""

    def _mock(method, data=None, timeout=None):
        if method == "OpenOrders":
            return {"error": ["EGeneral:Invalid"], "result": {}}
        return {"error": [], "result": {"closed": {"TXID1": {**_order("closed", price="100.0"), "vol_exec": "0.5"}}}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)

    assert kraken.find_order_by_cl_ord_id("abc123").txid == "TXID1"


def test_find_order_by_cl_ord_id_returns_none_when_closed_orders_errors_after_empty_open(monkeypatch) -> None:
    """The load-bearing case: an error must never be read as 'absent' — that is
    the path to a double sell."""

    def _mock(method, data=None, timeout=None):
        if method == "OpenOrders":
            return {"error": [], "result": {"open": {}}}
        return {"error": ["EGeneral:Invalid"], "result": {}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)

    assert kraken.find_order_by_cl_ord_id("abc123") is None


def test_find_order_by_cl_ord_id_returns_none_when_rows_come_back_without_echoing_the_id(monkeypatch) -> None:
    """Two things at once: a row carrying someone else's id is never adopted, and rows
    that do not echo the filtered id are not evidence of absence either — we cannot tell
    'not ours' from 'the echo is gone', and absent is the answer that clears the fields
    and re-places. An unreadable answer must freeze instead."""

    def _mock(method, data=None, timeout=None):
        key = "closed" if method == "ClosedOrders" else "open"
        return {"error": [], "result": {key: {"TXID1": _order("closed", cl_ord_id="someone-elses-id")}}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)
    errors: list[str] = []
    monkeypatch.setattr(kraken.logging, "error", lambda msg: errors.append(msg))

    assert kraken.find_order_by_cl_ord_id("abc123") is None
    assert errors


def test_find_order_by_cl_ord_id_still_resolves_when_only_the_other_endpoint_echoes_the_id(monkeypatch) -> None:
    """An unreadable answer from one endpoint must not hide a clean hit on the other."""

    def _mock(method, data=None, timeout=None):
        if method == "OpenOrders":
            return {"error": [], "result": {"open": {"TXID_OTHER": {"status": "open", "vol": "0.5"}}}}
        closed = {"TXID1": {**_order("closed", price="100.0"), "vol_exec": "0.5"}}
        return {"error": [], "result": {"closed": closed}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)
    monkeypatch.setattr(kraken.logging, "error", lambda msg: None)

    assert kraken.find_order_by_cl_ord_id("abc123").txid == "TXID1"


def test_find_order_by_cl_ord_id_logs_when_several_orders_carry_the_id(monkeypatch) -> None:
    """Impossible with per-attempt ids: adopting either one is a guess, so the operator
    must at least find it in the log."""

    def _mock(method, data=None, timeout=None):
        if method == "OpenOrders":
            return {"error": [], "result": {"open": {"TXID_A": _order("open"), "TXID_B": _order("open")}}}
        return {"error": [], "result": {"closed": {}}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)
    errors: list[str] = []
    monkeypatch.setattr(kraken.logging, "error", lambda msg: errors.append(msg))

    result = kraken.find_order_by_cl_ord_id("abc123")

    assert result.txid in {"TXID_A", "TXID_B"}
    assert len(errors) == 1
    assert "abc123" in errors[0]


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

    assert result.txid == "ORDER456"


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


def test_place_limit_order_includes_cl_ord_id_when_given(monkeypatch) -> None:
    captured: dict = {}

    def _mock(method, data=None, timeout=None):
        captured["data"] = data
        return {"error": [], "result": {"txid": ["ORDER456"]}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)

    kraken.place_limit_order("XBTEUR", "buy", 80000.0, 0.001, cl_ord_id="deadbeef")

    assert captured["data"]["cl_ord_id"] == "deadbeef"


def test_place_limit_order_omits_cl_ord_id_key_when_not_given(monkeypatch) -> None:
    captured: dict = {}

    def _mock(method, data=None, timeout=None):
        captured["data"] = data
        return {"error": [], "result": {"txid": ["ORDER456"]}}

    monkeypatch.setattr(kraken.api, "query_private", _mock)

    kraken.place_limit_order("XBTEUR", "buy", 80000.0, 0.001)

    assert "cl_ord_id" not in captured["data"]


def test_place_limit_order_returns_the_submitted_amounts(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": [], "result": {"txid": ["ORDER456"]}},
    )
    monkeypatch.setitem(config.PAIRS, "USDCEUR", {"pair_decimals": 4, "lot_decimals": 8})

    placed = kraken.place_limit_order("USDCEUR", "sell", 1.031274, 12.123456789)

    assert placed.txid == "ORDER456"
    assert placed.price == 1.0313
    assert placed.volume == 12.12345679


def test_place_limit_order_returns_none_when_txid_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": [], "result": {}},
    )

    assert kraken.place_limit_order("XBTEUR", "buy", 80000.0, 0.001) is None


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
