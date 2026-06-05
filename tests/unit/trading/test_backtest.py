import core.runtime as runtime
import trading.backtest as backtest
from trading.backtest import BacktestRequest, run_backtest

_PAIR = "XBTEUR"
_LEVELS = ("LL", "LV", "MV", "HV", "HH")

_SUMMARY_KEYS = {
    "ops_count",
    "pnl_samples",
    "win_rate_pct",
    "total_pnl_eur",
    "total_pnl_pct",
    "total_fees_eur",
    "best_op_pnl_eur",
    "worst_op_pnl_eur",
    "avg_op_pnl_eur",
    "median_op_pnl_eur",
    "row_count",
    "source",
}


def _setup_common(monkeypatch, sample_dataframe) -> None:
    monkeypatch.setattr(backtest.db, "load_ohlc_data", lambda _pair, _tf: sample_dataframe.copy())
    monkeypatch.setattr(backtest, "calculate_k_stops", lambda _pair, _events: {lvl: 1.0 for lvl in _LEVELS})
    monkeypatch.setattr(
        backtest,
        "TRADING_PARAMS",
        {_PAIR: {"buy": {"K_ACT": None, "MIN_MARGIN": 0.0}, "sell": {"K_ACT": None, "MIN_MARGIN": 0.0}}},
    )


def test_run_backtest_uses_cache_when_no_slicing(monkeypatch, sample_dataframe) -> None:
    _setup_common(monkeypatch, sample_dataframe)

    def _boom(_df):
        raise AssertionError("analyze_structural_noise must not be called on the cache path")

    monkeypatch.setattr(backtest, "analyze_structural_noise", _boom)
    runtime.update_pair_calibration(
        _PAIR,
        up_events=[],
        down_events=[],
        atr_p20=1.0,
        atr_p50=2.0,
        atr_p80=3.0,
        atr_p95=4.0,
        row_count=10,
    )

    result = run_backtest(BacktestRequest(pair=_PAIR, use_live_config=True))

    assert result.summary["source"] == "cache"


def test_run_backtest_recomputes_when_sliced(monkeypatch, sample_dataframe) -> None:
    _setup_common(monkeypatch, sample_dataframe)
    calls = []

    def _spy(df):
        calls.append(len(df))
        return [], []

    monkeypatch.setattr(backtest, "analyze_structural_noise", _spy)

    result = run_backtest(BacktestRequest(pair=_PAIR, start="2000-01-01"))

    assert calls  # recompute path ran analyze_structural_noise
    assert result.summary["source"] == "slice"


def test_run_backtest_summary_shape(monkeypatch, sample_dataframe) -> None:
    _setup_common(monkeypatch, sample_dataframe)
    monkeypatch.setattr(backtest, "analyze_structural_noise", lambda _df: ([], []))

    result = run_backtest(BacktestRequest(pair=_PAIR))

    s = result.summary
    assert set(s) == _SUMMARY_KEYS
    assert isinstance(s["ops_count"], int)
    assert isinstance(s["pnl_samples"], int)
    assert isinstance(s["row_count"], int)
    assert isinstance(s["win_rate_pct"], float)
    assert isinstance(s["total_pnl_eur"], float)
    assert s["source"] == "recompute"
