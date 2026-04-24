import pytest
import services.telegram as telegram
import core.database as db


# Mock classes for testing async command handlers
class MockMessage:
    """Mock telegram.Message for testing."""
    def __init__(self):
        self.replies = []
    
    async def reply_text(self, text):
        self.replies.append(text)


class MockUser:
    """Mock telegram.User for testing."""
    def __init__(self, user_id=123456789):
        self.id = user_id


class MockUpdate:
    """Mock telegram.Update for testing."""
    def __init__(self, user_id=123456789):
        self.effective_user = MockUser(user_id)
        self.message = MockMessage()


class MockContext:
    """Mock telegram.ext.ContextTypes.DEFAULT_TYPE for testing."""
    def __init__(self):
        self.args = []


# ============================================================================
# Core Functionality: Pause/Resume
# ============================================================================


@pytest.mark.asyncio
async def test_pause_command_sets_paused_true_in_database(monkeypatch) -> None:
    """Pause command sets bot_paused=True and confirms to user."""
    calls = []
    monkeypatch.setattr(db, "get_bot_paused", lambda: False)
    monkeypatch.setattr(db, "set_bot_paused", lambda p, updated_by: calls.append((p, updated_by)))
    
    interface = telegram.TelegramInterface(token="test", user_id=123456789)
    update = MockUpdate(user_id=123456789)
    
    await interface.pause_command(update, MockContext())
    
    assert calls == [(True, "telegram")]
    assert len(update.message.replies) == 1
    assert "⏸" in update.message.replies[0]


@pytest.mark.asyncio
async def test_resume_command_sets_paused_false_in_database(monkeypatch) -> None:
    """Resume command sets bot_paused=False and confirms to user."""
    calls = []
    monkeypatch.setattr(db, "get_bot_paused", lambda: True)
    monkeypatch.setattr(db, "set_bot_paused", lambda p, updated_by: calls.append((p, updated_by)))
    
    interface = telegram.TelegramInterface(token="test", user_id=123456789)
    update = MockUpdate(user_id=123456789)
    
    await interface.resume_command(update, MockContext())
    
    assert calls == [(False, "telegram")]
    assert len(update.message.replies) == 1
    assert "▶️" in update.message.replies[0]


# ============================================================================
# Idempotency & Status Display
# ============================================================================


@pytest.mark.asyncio
async def test_pause_resume_handle_idempotent_operations_and_status(monkeypatch) -> None:
    """Commands handle duplicate operations gracefully; status reflects state."""
    monkeypatch.setattr(db, "get_bot_paused", lambda: True)
    monkeypatch.setattr(db, "set_bot_paused", lambda *_: None)
    
    interface = telegram.TelegramInterface(token="test", user_id=123456789)
    
    # Pause when already paused
    update = MockUpdate(user_id=123456789)
    await interface.pause_command(update, MockContext())
    assert "already paused" in update.message.replies[0]
    
    # Status shows paused
    update = MockUpdate(user_id=123456789)
    await interface.status_command(update, MockContext())
    assert "PAUSED" in update.message.replies[0]
    
    # Resume when not paused
    monkeypatch.setattr(db, "get_bot_paused", lambda: False)
    update = MockUpdate(user_id=123456789)
    await interface.resume_command(update, MockContext())
    assert "already running" in update.message.replies[0]
    
    # Status shows running
    update = MockUpdate(user_id=123456789)
    await interface.status_command(update, MockContext())
    assert "RUNNING" in update.message.replies[0]


# ============================================================================
# Authorization
# ============================================================================


@pytest.mark.asyncio
async def test_all_commands_reject_unauthorized_users(monkeypatch) -> None:
    """Authorization check protects all commands from unauthorized access."""
    monkeypatch.setattr(db, "get_bot_paused", lambda: False)
    
    interface = telegram.TelegramInterface(token="test", user_id=123456789)
    wrong_user_update = MockUpdate(user_id=999999)  # Wrong user ID
    context = MockContext()
    
    # All commands should be silently rejected
    await interface.pause_command(wrong_user_update, context)
    await interface.resume_command(wrong_user_update, context)
    await interface.status_command(wrong_user_update, context)
    
    assert len(wrong_user_update.message.replies) == 0
