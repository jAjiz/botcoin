import core.runtime as runtime


def test_get_last_balance_returns_copy():
    runtime.update_balance({"EUR": 1000.0})
    result = runtime.get_last_balance()
    result["EUR"] = 9999.0
    assert runtime.get_last_balance()["EUR"] == 1000.0


def test_get_pair_data_returns_copy():
    runtime.update_pair_data("XBTEUR", price=50000.0, atr=200.0, volatility_level="MV")
    result = runtime.get_pair_data("XBTEUR")
    result["last_price"] = 9999.0
    assert runtime.get_pair_data("XBTEUR")["last_price"] == 50000.0


def test_config_dirty_flag_set_and_pop():
    assert runtime.pop_config_dirty("XBTEUR") is False
    runtime.mark_config_dirty("XBTEUR")
    assert runtime.pop_config_dirty("XBTEUR") is True
    # second pop returns False (cleared)
    assert runtime.pop_config_dirty("XBTEUR") is False


def test_config_dirty_flag_is_per_pair():
    runtime.mark_config_dirty("ETHEUR")
    assert runtime.pop_config_dirty("XBTEUR") is False
    assert runtime.pop_config_dirty("ETHEUR") is True
