from unittest.mock import AsyncMock, MagicMock

import pytest

import services.telegram.polling as polling


class MockMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class MockUser:
    def __init__(self, user_id=123456789):
        self.id = user_id


class MockUpdate:
    def __init__(self, user_id=123456789):
        self.effective_user = MockUser(user_id)
        self.message = MockMessage()


class MockContext:
    def __init__(self):
        self.args = []


def _mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


def _mock_client(*, get=None, post=None):
    c = MagicMock()
    if get is not None:
        c.get = AsyncMock(return_value=get)
    if post is not None:
        c.post = AsyncMock(return_value=post)
    return c


# ============================================================================
# Core Functionality: Pause/Resume
# ============================================================================


@pytest.mark.asyncio
async def test_pause_command_calls_control_pause_api(monkeypatch) -> None:
    """Pause command calls POST /control/pause and confirms to user."""
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")
    mock = _mock_client(post=_mock_response({"paused": True, "updated_by": "telegram"}))
    monkeypatch.setattr(polling, "client", mock)

    update = MockUpdate(user_id=123456789)
    await polling.pause_command(update, MockContext())

    mock.post.assert_called_once_with("/control/pause", json={"updated_by": "telegram"})
    assert len(update.message.replies) == 1
    assert "⏸" in update.message.replies[0]


@pytest.mark.asyncio
async def test_resume_command_calls_control_resume_api(monkeypatch) -> None:
    """Resume command calls POST /control/resume and confirms to user."""
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")
    mock = _mock_client(post=_mock_response({"paused": False, "updated_by": "telegram"}))
    monkeypatch.setattr(polling, "client", mock)

    update = MockUpdate(user_id=123456789)
    await polling.resume_command(update, MockContext())

    mock.post.assert_called_once_with("/control/resume", json={"updated_by": "telegram"})
    assert len(update.message.replies) == 1
    assert "▶️" in update.message.replies[0]


# ============================================================================
# Status Display
# ============================================================================


@pytest.mark.asyncio
async def test_status_command_shows_paused_state(monkeypatch) -> None:
    """Status command displays PAUSED or RUNNING based on API response."""
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")

    monkeypatch.setattr(
        polling, "client",
        _mock_client(get=_mock_response({"paused": True, "last_run_at": None})),
    )
    update = MockUpdate(user_id=123456789)
    await polling.status_command(update, MockContext())
    assert "PAUSED" in update.message.replies[0]

    monkeypatch.setattr(
        polling, "client",
        _mock_client(get=_mock_response({"paused": False, "last_run_at": None})),
    )
    update = MockUpdate(user_id=123456789)
    await polling.status_command(update, MockContext())
    assert "RUNNING" in update.message.replies[0]


# ============================================================================
# Authorization
# ============================================================================


@pytest.mark.asyncio
async def test_all_commands_reject_unauthorized_users(monkeypatch) -> None:
    """Authorization check protects all commands from unauthorized access."""
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")

    wrong_user_update = MockUpdate(user_id=999999)
    context = MockContext()

    await polling.pause_command(wrong_user_update, context)
    await polling.resume_command(wrong_user_update, context)
    await polling.status_command(wrong_user_update, context)

    assert len(wrong_user_update.message.replies) == 0
