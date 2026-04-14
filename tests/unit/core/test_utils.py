from datetime import datetime

from core.utils import now_str


def test_now_str_returns_expected_datetime_format() -> None:
    value = now_str()
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    assert parsed.year >= 2000
