from datetime import UTC, datetime

from core.utils import now_utc


def test_now_utc_returns_timezone_aware_utc_datetime() -> None:
    value = now_utc()
    assert isinstance(value, datetime)
    assert value.tzinfo == UTC
    assert value.year >= 2000
