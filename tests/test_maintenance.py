import pytest
from queue_bot.services import maintenance, queues, users
from queue_bot.services.errors import BotError


def test_maintenance_flag_and_time(conn):
    assert not maintenance.is_maintenance_active(conn)
    assert maintenance.get_maintenance_end_time(conn) == "20:15"

    maintenance.set_maintenance_mode(conn, True, "20:15")
    assert maintenance.is_maintenance_active(conn)
    assert maintenance.get_maintenance_end_time(conn) == "20:15"

    maintenance.set_maintenance_mode(conn, False)
    assert not maintenance.is_maintenance_active(conn)


def test_queue_actions_blocked_during_maintenance(conn):
    u = users.register_user(conn, 100, "Тестовий Студент")
    sess_id = queues.create_queue(conn, 100, 1, 1, "Пара")

    # Turn on maintenance
    maintenance.set_maintenance_mode(conn, True, "20:15")

    # Joining queue must fail
    with pytest.raises(BotError) as exc:
        queues.join_queue(conn, 100, sess_id)
    assert "технічні роботи" in str(exc.value).lower()

    # Creating queue must fail
    with pytest.raises(BotError) as exc:
        queues.create_queue(conn, 100, 1, 1, "Ще пара")
    assert "технічні роботи" in str(exc.value).lower()

    # Turn off maintenance
    maintenance.set_maintenance_mode(conn, False)

    # Now joining succeeds
    pos, total = queues.join_queue(conn, 100, sess_id)
    assert pos == 1
    assert total == 1


@pytest.mark.asyncio
async def test_maintenance_handlers_flow(conn):
    from unittest.mock import AsyncMock, MagicMock
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.fsm.storage.base import StorageKey
    from queue_bot.handlers.admin import (
        start_maintenance_prompt,
        process_maintenance_time_entry,
        disable_maintenance,
        AdminManageState,
        AdminPanelState,
    )

    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=100, user_id=100))

    message = MagicMock()
    message.from_user.id = 100
    message.answer = AsyncMock()
    message.bot = MagicMock()
    message.bot.send_message = AsyncMock()

    # 1. Початок введення часу техробіт
    await start_maintenance_prompt(message, state, conn)
    assert await state.get_state() == AdminManageState.entering_maintenance_time
    assert "Увімкнення режиму технічних робіт" in message.answer.call_args[0][0]

    # 2. Введення часу (наприклад, "22:00")
    message.text = "22:00"
    await process_maintenance_time_entry(message, state, conn)
    assert await state.get_state() == AdminPanelState.in_settings_menu
    assert maintenance.is_maintenance_active(conn) is True
    assert maintenance.get_maintenance_end_time(conn) == "22:00"

    # 3. Вимкнення техробіт
    await disable_maintenance(message, state, conn)
    assert maintenance.is_maintenance_active(conn) is False
    assert "ВИМКНЕНО" in message.answer.call_args[0][0]

