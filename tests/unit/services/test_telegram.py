import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.telegram.app as tg_module
import services.telegram.polling as polling


class MockMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class MockUpdate:
    def __init__(self, user_id=123456789):
        self.effective_user = MagicMock(id=user_id)
        self.message = MockMessage()


class MockContext:
    def __init__(self, args=None):
        self.args = args or []


def _mock_response(json_data):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def _mock_client(*, get=None, post=None):
    c = MagicMock()
    if get is not None:
        c.get = AsyncMock(side_effect=get) if inspect.isfunction(get) else AsyncMock(return_value=get)
    if post is not None:
        c.post = AsyncMock(return_value=post)
    return c


_MARKET_ITEM = {"pair": "XBTEUR", "last_price": 80000.0, "atr": 500.0, "volatility_level": "MV"}


# ============================================================================
# Pause / Resume commands
# ============================================================================


@pytest.mark.asyncio
async def test_pause_command_calls_control_pause_api(monkeypatch) -> None:
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")
    mock = _mock_client(post=_mock_response({"paused": True, "updated_by": "telegram"}))
    monkeypatch.setattr(polling, "client", mock)

    update = MockUpdate()
    await polling.pause_command(update, MockContext())

    mock.post.assert_called_once_with("/control/pause", json={"updated_by": "telegram"})
    assert "⏸" in update.message.replies[0]


@pytest.mark.asyncio
async def test_resume_command_calls_control_resume_api(monkeypatch) -> None:
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")
    mock = _mock_client(post=_mock_response({"paused": False, "updated_by": "telegram"}))
    monkeypatch.setattr(polling, "client", mock)

    update = MockUpdate()
    await polling.resume_command(update, MockContext())

    mock.post.assert_called_once_with("/control/resume", json={"updated_by": "telegram"})
    assert "▶️" in update.message.replies[0]


# ============================================================================
# Status command
# ============================================================================


@pytest.mark.asyncio
async def test_status_command_shows_paused_state(monkeypatch) -> None:
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")

    monkeypatch.setattr(polling, "client", _mock_client(get=_mock_response({"paused": True, "last_run_at": None})))
    update = MockUpdate()
    await polling.status_command(update, MockContext())
    assert "PAUSED" in update.message.replies[0]

    monkeypatch.setattr(polling, "client", _mock_client(get=_mock_response({"paused": False, "last_run_at": None})))
    update = MockUpdate()
    await polling.status_command(update, MockContext())
    assert "RUNNING" in update.message.replies[0]


# ============================================================================
# Market command
# ============================================================================


@pytest.mark.asyncio
async def test_market_command_shows_all_pairs(monkeypatch) -> None:
    """Market command fetches /market and /balance then formats a summary."""
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")
    monkeypatch.setattr(polling, "PAIRS", {"XBTEUR": {"base": "XXBT"}})
    monkeypatch.setattr(polling, "FIAT_CODE", "ZEUR")

    async def _fake_get(url):
        if url == "/market":
            return _mock_response([_MARKET_ITEM])
        return _mock_response({"balance": {"XXBT": 0.5, "ZEUR": 1500.0}})

    monkeypatch.setattr(polling, "client", _mock_client(get=_fake_get))
    update = MockUpdate()
    await polling.market_command(update, MockContext())
    assert "XBTEUR" in update.message.replies[0]
    assert "80" in update.message.replies[0]


@pytest.mark.asyncio
async def test_market_command_rejects_unknown_pair(monkeypatch) -> None:
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")
    monkeypatch.setattr(polling, "PAIRS", {"XBTEUR": {}})
    monkeypatch.setattr(polling, "client", MagicMock())

    update = MockUpdate()
    await polling.market_command(update, MockContext(args=["UNKNOWN"]))
    assert "Unknown pair" in update.message.replies[0]


# ============================================================================
# Positions command
# ============================================================================


@pytest.mark.asyncio
async def test_positions_command_shows_open_position(monkeypatch) -> None:
    """Positions command shows trailing and stop price when trailing is active."""
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")
    monkeypatch.setattr(polling, "PAIRS", {"XBTEUR": {}})

    async def _fake_get(url):
        if "/positions" in url:
            return _mock_response(
                {
                    "pair": "XBTEUR",
                    "position": {
                        "side": "buy",
                        "volume": 0.01,
                        "entry_price": 80000.0,
                        "activation_atr": 500.0,
                        "activation_price": 81000.0,
                        "created_at": "2026-04-01T12:00:00Z",
                        "trailing_price": 82000.0,
                        "stop_price": 78000.0,
                    },
                }
            )
        return _mock_response(_MARKET_ITEM)

    monkeypatch.setattr(polling, "client", _mock_client(get=_fake_get))
    update = MockUpdate()
    await polling.positions_command(update, MockContext(args=["XBTEUR"]))
    msg = update.message.replies[0]
    assert "Trailing" in msg and "Stop" in msg


@pytest.mark.asyncio
async def test_positions_command_shows_no_position(monkeypatch) -> None:
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")
    monkeypatch.setattr(polling, "PAIRS", {"XBTEUR": {}})

    async def _fake_get(url):
        if "/positions" in url:
            return _mock_response({"pair": "XBTEUR", "position": None})
        return _mock_response(_MARKET_ITEM)

    monkeypatch.setattr(polling, "client", _mock_client(get=_fake_get))
    update = MockUpdate()
    await polling.positions_command(update, MockContext(args=["XBTEUR"]))
    assert "No open position" in update.message.replies[0]


# ============================================================================
# PnL helper
# ============================================================================


def test_pnl_percent() -> None:
    buy_pos = {"side": "buy", "entry_price": 80000.0, "trailing_price": 82000.0, "stop_price": 79000.0}
    assert polling._pnl_percent(buy_pos, 82000.0) == pytest.approx((80000.0 - 79000.0) / 80000.0 * 100)

    no_trailing = {"side": "buy", "entry_price": 80000.0, "trailing_price": None, "stop_price": None}
    assert polling._pnl_percent(no_trailing, 80000.0) is None


# ============================================================================
# Authorization
# ============================================================================


@pytest.mark.asyncio
async def test_all_commands_reject_unauthorized_users(monkeypatch) -> None:
    """Authorization check protects all commands from unauthorized access."""
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")

    wrong_user = MockUpdate(user_id=999999)
    ctx = MockContext()
    await polling.pause_command(wrong_user, ctx)
    await polling.resume_command(wrong_user, ctx)
    await polling.status_command(wrong_user, ctx)

    assert len(wrong_user.message.replies) == 0


# ============================================================================
# Notify route
# ============================================================================


def _notify_client(monkeypatch):
    mock_tg = MagicMock()
    mock_tg.bot.send_message = AsyncMock()
    monkeypatch.setattr(tg_module, "tg_app", mock_tg)
    monkeypatch.setattr(tg_module, "TELEGRAM_USER_ID", "123456789")
    monkeypatch.setattr(tg_module, "API_SECRET_TOKEN", None)
    app = FastAPI()
    app.add_api_route("/notify", tg_module.notify, methods=["POST"], status_code=202)
    return TestClient(app), mock_tg


def test_notify_sends_message_with_level_prefix(monkeypatch):
    client, mock_tg = _notify_client(monkeypatch)
    resp = client.post("/notify", json={"message": "disk full", "level": "warning"})
    assert resp.status_code == 202
    assert resp.json() == {"accepted": True}
    sent_text = mock_tg.bot.send_message.call_args.kwargs["text"]
    assert "⚠️" in sent_text and "disk full" in sent_text


def test_notify_tolerates_send_failure(monkeypatch):
    client, mock_tg = _notify_client(monkeypatch)
    mock_tg.bot.send_message.side_effect = RuntimeError("network error")
    assert client.post("/notify", json={"message": "test", "level": "info"}).status_code == 202
