import pytest
from aiogram import Dispatcher

import config
from handlers import admin, polls, start, student
from handlers.common import Guard, format_queue
from services import queues
from subjects import load_subjects


def test_subjects_json_is_valid():
    subjects = load_subjects(config.SUBJECTS_PATH)
    assert len(subjects) == 7
    by_name = {s["name"]: [t["name"] for t in s["teachers"]] for s in subjects}
    assert by_name["Машинне навчання"] == ["Барабаш Д.С."]
    assert len(by_name["Людино-машинна взаємодія"]) == 2
    assert len(by_name["Курсова робота з КПЗ"]) == 5
    assert not any("Фізичне" in name for name in by_name)


def test_routers_can_be_connected_without_token():
    for r in (start.router, polls.router, student.router, admin.router, start.fallback_router):
        r._parent_router = None
    dp = Dispatcher()
    dp.callback_query.outer_middleware(Guard())
    dp.include_routers(start.router, polls.router, student.router, admin.router, start.fallback_router)


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


@pytest.mark.asyncio
async def test_group_chat_protection_blocks_keyboards_and_photos_but_allows_schedule_commands(conn):
    from datetime import datetime
    from unittest.mock import AsyncMock
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.types import Chat, Message, PhotoSize, Update, User
    from handlers import schedule as schedule_handler

    subjects = load_subjects(config.SUBJECTS_PATH)
    for r in (
        start.router,
        polls.router,
        admin.router,
        schedule_handler.router,
        student.router,
        start.fallback_router,
    ):
        r._parent_router = None

    dp = Dispatcher(storage=MemoryStorage(), conn=conn, subjects=subjects)
    dp.message.outer_middleware(Guard())
    dp.include_routers(
        start.router,
        polls.router,
        admin.router,
        schedule_handler.router,
        student.router,
        start.fallback_router,
    )

    admin_user = User(id=100, is_bot=False, first_name="Адмін")
    group_chat = Chat(id=-1001234567890, type="supergroup", title="ПІ-231")
    mock_bot = AsyncMock()
    mock_bot.id = 1

    # 1. /start у групі ігнорується (не спамить ReplyKeyboardMarkup)
    msg_start = Message(
        message_id=10,
        date=datetime.now(),
        chat=group_chat,
        from_user=admin_user,
        text="/start",
    )
    await dp.feed_update(mock_bot, Update(update_id=10, message=msg_start))
    assert not mock_bot.called

    # 2. Звичайне фото від адміна в групі ігнорується (не запускає розпізнавання замін)
    msg_photo = Message(
        message_id=11,
        date=datetime.now(),
        chat=group_chat,
        from_user=admin_user,
        photo=[PhotoSize(file_id="f1", file_unique_id="u1", width=800, height=600)],
    )
    await dp.feed_update(mock_bot, Update(update_id=11, message=msg_photo))
    assert not mock_bot.called

    # 3. Команда /replacements у групі працює і не прикріплює нижню клавіатуру
    msg_rep = Message(
        message_id=12,
        date=datetime.now(),
        chat=group_chat,
        from_user=admin_user,
        text="/replacements",
    )
    await dp.feed_update(mock_bot, Update(update_id=12, message=msg_rep))
    assert mock_bot.called
    method_called = mock_bot.call_args[0][0]
    assert getattr(method_called, "reply_markup", None) is None
