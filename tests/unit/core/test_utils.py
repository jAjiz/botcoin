from datetime import datetime, timezone

from core.utils import now_utc


def test_now_utc_returns_timezone_aware_utc_datetime() -> None:
    value = now_utc()
    assert isinstance(value, datetime)
    assert value.tzinfo == timezone.utc
    assert value.year >= 2000
