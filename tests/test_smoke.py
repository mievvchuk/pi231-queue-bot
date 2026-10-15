import pytest
from aiogram import Dispatcher

import config
from handlers import admin, start, student
from handlers.common import Guard, format_queue
from services import queues
from subjects import load_subjects


def test_subjects_json_is_valid():
    subjects = load_subjects(config.SUBJECTS_PATH)
    assert len(subjects) == 6
    by_name = {s["name"]: [t["name"] for t in s["teachers"]] for s in subjects}
    assert by_name["Машинне навчання"] == ["Барабаш Д.С."]
    assert len(by_name["Людино-машинна взаємодія"]) == 2
    assert not any("Фізичне" in name for name in by_name)


def test_routers_can_be_connected_without_token():
    dp = Dispatcher()
    dp.callback_query.outer_middleware(Guard())
    dp.include_routers(start.router, student.router, admin.router, start.fallback_router)


def test_queue_text_marks_current_user(conn, make_user):
    subjects = load_subjects(config.SUBJECTS_PATH)
    session = queues.create_session(conn, 100, 3, 4)
    make_user(1, "Петренко Іван")
    make_user(2, "Коваль Андрій")
    queues.join_queue(conn, 1, session)
    queues.join_queue(conn, 2, session)
    text = format_queue(subjects, queues.get_session(conn, session),
                        queues.get_entries(conn, session), my_telegram_id=2)
    assert "👉 2. Коваль Андрій — це ви" in text
    assert "1. Петренко Іван" in text and "👉 1." not in text


@pytest.mark.asyncio
async def test_non_admin_back_to_queue_routes_to_student(conn, make_user):
    from unittest.mock import AsyncMock
    from aiogram.types import Chat, Message, User, Update
    from aiogram.fsm.storage.memory import MemoryStorage
    from datetime import datetime
    from aiogram.fsm.storage.base import StorageKey

    subjects = load_subjects(config.SUBJECTS_PATH)
    for r in (start.router, admin.router, student.router, start.fallback_router):
        r._parent_router = None
    dp = Dispatcher(storage=MemoryStorage(), conn=conn, subjects=subjects)
    dp.message.outer_middleware(Guard())
    dp.include_routers(start.router, admin.router, student.router, start.fallback_router)

    make_user(999, "Петренко Іван")
    session = queues.create_session(conn, 100, 3, 4)

    user = User(id=999, is_bot=False, first_name="Іван")
    chat = Chat(id=999, type="private")
    key = StorageKey(bot_id=1, chat_id=999, user_id=999)
    await dp.storage.set_data(key, {"session_id": session})

    from unittest.mock import AsyncMock
    mock_bot = AsyncMock()
    mock_bot.id = 1

    msg = Message(message_id=1, date=datetime.now(), chat=chat, from_user=user, text="🔙 Назад до черги")

    await dp.feed_update(mock_bot, Update(update_id=1, message=msg))
    assert mock_bot.called
    method_called = mock_bot.call_args[0][0]
    reply_text = getattr(method_called, "text", "")
    assert "У вас немає прав адміністратора" not in reply_text
