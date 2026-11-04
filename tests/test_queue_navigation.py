from datetime import datetime

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Chat, Message, Update, User

from queue_bot.handlers.common import queue_label
from queue_bot.handlers.student import StudentFlow
from queue_bot.services import queues
from tests.test_numbered_flow import app


async def send(app, text, uid=1):
    message = Message(message_id=1, date=datetime.now(), chat=Chat(id=uid, type='private'),
                      from_user=User(id=uid, is_bot=False, first_name='Студент'), text=text)
    await app[0].feed_update(app[1], Update(update_id=1, message=message))


def state(app, uid=1):
    from aiogram.fsm.context import FSMContext
    return FSMContext(app[0].storage, StorageKey(bot_id=5, chat_id=uid, user_id=uid))


def button(app, conn, sid):
    return '📋 ' + queue_label(app[0]['subjects'], queues.get_session(conn, sid))


def last_buttons(app):
    return [b.text for row in app[1].call_args[0][0].reply_markup.keyboard for b in row]


@pytest.mark.asyncio
async def test_my_queue_return_survives_settings_and_never_shows_another_students_records(conn, app):
    sid = app[2]
    queues.join_queue(conn, 1, sid, lab_numbers=[1], planned_minutes=10)
    other = queues.create_session(conn, 100, 2, 1)
    queues.join_queue(conn, 2, other)
    await send(app, '👥 Мої черги')
    await send(app, button(app, conn, sid))
    await send(app, '🧪 Роботи та час')
    await send(app, '🔙 Назад до черги')
    await send(app, '🔙 До моїх черг')
    assert await state(app).get_state() == StudentFlow.browsing_my_queues.state
    assert button(app, conn, sid) in last_buttons(app)
    assert button(app, conn, other) not in last_buttons(app)


@pytest.mark.asyncio
async def test_each_student_has_their_own_return_destination(conn, app):
    sid = app[2]
    for uid in (1, 2):
        queues.join_queue(conn, uid, sid)
    await send(app, '👥 Мої черги', uid=1)
    await send(app, button(app, conn, sid), uid=1)
    await send(app, '📋 Усі черги', uid=2)
    await send(app, '📖 Черги на пари', uid=2)
    await send(app, button(app, conn, sid), uid=2)
    # Older keyboards with the generic back label also respect the saved source.
    await send(app, '🔙 До черг', uid=1)
    await send(app, '🔙 До черг', uid=2)
    assert await state(app, 1).get_state() == StudentFlow.browsing_my_queues.state
    assert await state(app, 2).get_state() == StudentFlow.browsing_queues.state
    # Opening from All afterwards replaces only that student's previous source.
    await send(app, '📋 Усі черги', uid=1)
    await send(app, '📖 Черги на пари', uid=1)
    await send(app, button(app, conn, sid), uid=1)
    await send(app, '🔙 До черг', uid=1)
    assert await state(app, 1).get_state() == StudentFlow.browsing_queues.state


@pytest.mark.asyncio
async def test_my_queue_page_is_restored_and_clamped_after_completing_last_entry(conn, app):
    sids = [app[2]] + [queues.create_session(conn, 100, n, 1) for n in range(2, 10)]
    for sid in sids:
        queues.join_queue(conn, 1, sid)
    await send(app, '👥 Мої черги')
    await send(app, '➡️')
    assert (await state(app).get_data())['page'] == 2
    selected = next(b for b in last_buttons(app) if b.startswith('📋 '))
    await send(app, selected)
    await send(app, '🔙 До моїх черг')
    assert (await state(app).get_data())['page'] == 2
    await send(app, selected)
    await send(app, '✅ Здав')
    await send(app, '🔙 До моїх черг')
    assert await state(app).get_state() == StudentFlow.browsing_my_queues.state
    assert (await state(app).get_data())['page'] == 1
    assert len(queues.list_user_sessions(conn, 1)) == 8


@pytest.mark.asyncio
async def test_finishing_the_only_queue_returns_to_empty_my_queues(conn, app):
    queues.join_queue(conn, 1, app[2])
    await send(app, '👥 Мої черги')
    await send(app, button(app, conn, app[2]))
    await send(app, '✅ Здав')
    await send(app, '🔙 До моїх черг')
    assert await state(app).get_state() == StudentFlow.browsing_my_queues.state
    assert not queues.list_user_sessions(conn, 1)
    assert 'Мої черги' in app[1].call_args[0][0].text
