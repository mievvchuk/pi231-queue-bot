import pytest
from unittest.mock import AsyncMock, MagicMock
from services.updates import (
    ensure_updates_table,
    is_version_sent,
    mark_version_sent,
    get_active_user_ids,
    broadcast_message_to_users,
    check_and_broadcast_updates,
)


def test_updates_db_flow(conn):
    ensure_updates_table(conn)
    assert not is_version_sent(conn, "v1.0-test")
    
    mark_version_sent(conn, "v1.0-test", "Test description")
    assert is_version_sent(conn, "v1.0-test")


def test_get_active_user_ids(conn, make_user):
    make_user(1001, "Іван Іванов")
    make_user(1002, "Петро Петров")

    user_ids = get_active_user_ids(conn)
    assert 1001 in user_ids
    assert 1002 in user_ids


@pytest.mark.asyncio
async def test_broadcast_message_to_users():
    bot = MagicMock()
    bot.send_message = AsyncMock()

    sent, failed = await broadcast_message_to_users(bot, [1001, 1002], "Hello update")
    assert sent == 2
    assert failed == 0
    assert bot.send_message.call_count == 2


@pytest.mark.asyncio
async def test_check_and_broadcast_updates(conn, make_user):
    make_user(1001, "Іван Іванов")
    bot = MagicMock()
    bot.send_message = AsyncMock()

    # First run sends updates
    await check_and_broadcast_updates(bot, conn)
    assert bot.send_message.call_count > 0

    # Second run does not resend
    bot.send_message.reset_mock()
    await check_and_broadcast_updates(bot, conn)
    assert bot.send_message.call_count == 0


def test_alert_state_storage(conn):
    from services.alerts import get_stored_alert_status, set_stored_alert_status

    # Initially None
    assert get_stored_alert_status(conn) is None

    # Set True
    set_stored_alert_status(conn, True)
    assert get_stored_alert_status(conn) is True

    # Set False
    set_stored_alert_status(conn, False)
    assert get_stored_alert_status(conn) is False

