from pathlib import Path

import pandas as pd
import pytest

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_dataframe() -> pd.DataFrame:
    """25-row OHLC fixture loaded from tests/unit/fixtures/sample_dataframe.csv.

    Price structure:
    - 2 uptrend events  (rows 0→6, 12→18)
    - 2 downtrend events (rows 6→12, 18→24)
    ATR cycles through [0.5, 1.5, 2.5, 3.5, 4.5] in each segment,
    covering all 5 volatility levels (LL/LV/MV/HV/HH).
    """
    return pd.read_csv(
        _FIXTURES_DIR / "sample_dataframe.csv",
        parse_dates=["dtime"],
    )