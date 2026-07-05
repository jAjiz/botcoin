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


def test_register_session_failure_alerts_once_at_threshold():
    runtime._shared_data["consecutive_session_failures"] = 0
    runtime._shared_data["session_failure_alerted"] = False

    assert runtime.register_session_failure(3) is None  # 1st
    assert runtime.register_session_failure(3) is None  # 2nd
    assert runtime.register_session_failure(3) == 3  # 3rd → alert, returns count
    assert runtime.register_session_failure(3) is None  # 4th → already alerted
    assert runtime.register_session_failure(3) is None  # 5th → still silent


def test_register_session_success_signals_recovery_only_when_alerted():
    runtime._shared_data["consecutive_session_failures"] = 0
    runtime._shared_data["session_failure_alerted"] = False

    # No prior alert → no recovery signal, streak reset.
    runtime.register_session_failure(3)
    assert runtime.register_session_success() is False
    assert runtime._shared_data["consecutive_session_failures"] == 0

    # Reach the alert state, then recover once.
    runtime.register_session_failure(1)
    assert runtime.register_session_success() is True
    assert runtime._shared_data["session_failure_alerted"] is False
    # A second success without a new alert does not re-signal.
    assert runtime.register_session_success() is False
