import os
import uuid
from datetime import UTC, datetime

import pytest

import core.database as db

pytestmark = pytest.mark.integration

if os.environ.get("RUN_DB_INTEGRATION") != "true":
    pytest.skip("RUN_DB_INTEGRATION not set", allow_module_level=True)


def _make_position_data(closing_order_id: str) -> dict:
    return {
        "side": "buy",
        "volume": 0.5,
        "entry_price": 50000.0,
        "activation_atr": 200.0,
        "activation_price": 50100.0,
        "created_at": datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC),
        "activated_at": None,
        "trailing_price": None,
        "stop_price": None,
        "stop_atr": None,
        "closing_price": 50500.0,
        "closing_order_id": closing_order_id,
        "pnl_percent": 1.0,
    }


def test_record_position_closed_is_idempotent_and_transactional():
    """Calling record_position_closed twice with the same closing_order_id (a
    crash-retry) must converge to exactly one closed_positions row and leave no
    trailing_state row behind -- the idempotency proof this task exists for."""
    pair = f"TEST{uuid.uuid4().hex[:8].upper()}EUR"
    closing_order_id = f"test-record-position-closed-{uuid.uuid4().hex}"
    position_data = _make_position_data(closing_order_id)

    try:
        db.save_trailing_state(pair, position_data)
        assert db.load_trailing_state(pair) is not None

        db.record_position_closed(pair, position_data)
        # Simulate a crash-retry: same close detected again next session.
        db.record_position_closed(pair, position_data)

        closed = db.load_closed_positions(pair=pair)
        assert len(closed) == 1
        assert closed[0]["closing_order_id"] == closing_order_id

        assert db.load_trailing_state(pair) is None
    finally:
        with db.get_session() as session:
            session.query(db.ClosedPosition).filter(db.ClosedPosition.closing_order_id == closing_order_id).delete()
            session.query(db.TrailingState).filter(db.TrailingState.pair == pair).delete()
