from datetime import datetime

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from queue_bot.handlers.common import format_my_queues_overview, format_queue
from queue_bot.services import queues
from tests.test_queue_navigation import app, send, state, button


def response(app, uid=1, mid=42):
    return Message(message_id=mid, date=datetime.now(), chat=Chat(id=uid, type='private'),
                   from_user=User(id=5, is_bot=True, first_name='Бот'), text='Черга').as_(app[1])


async def refresh_callback(app, sid, mid=42):
    callback = CallbackQuery(id='refresh', from_user=User(id=1, is_bot=False, first_name='Студент'),
                             chat_instance='private', message=response(app, mid=mid),
                             data=f'queue:{sid}:refresh')
    await app[0].feed_update(app[1], Update(update_id=2, callback_query=callback))


@pytest.mark.asyncio
async def test_inline_refresh_edits_same_message_and_updates_content_and_controls(conn, app):
    sid = app[2]
    app[1].return_value = response(app)
    await send(app, '👥 Мої черги')
    await send(app, button(app, conn, sid))
    queues.join_queue(conn, 2, sid, lab_numbers=[1], planned_minutes=10)
    app[1].reset_mock()
    await refresh_callback(app, sid)
    edit = app[1].edit_message_text.await_args.kwargs
    assert edit['chat_id'] == 1 and edit['message_id'] == 42
    assert 'Коваль Андрій' in edit['text']
    assert any(b.callback_data == f'queue:{sid}:refresh' for r in edit['reply_markup'].inline_keyboard for b in r)
    assert not any(isinstance(call.args[0], SendMessage) for call in app[1].call_args_list)
    assert (await state(app).get_data())['session_id'] == sid


@pytest.mark.asyncio
async def test_unchanged_queue_refresh_is_successful_without_new_message(conn, app):
    app[1].edit_message_text.side_effect = TelegramBadRequest(
        method=EditMessageText(text='Черга', chat_id=1, message_id=42),
        message='Bad Request: message is not modified')
    await refresh_callback(app, app[2])
    assert not any(isinstance(call.args[0], SendMessage) for call in app[1].call_args_list)
    assert app[1].call_args.args[0].show_alert is None


@pytest.mark.asyncio
async def test_reply_refresh_restores_message_reference_after_state_change(conn, app):
    app[1].return_value = response(app)
    sid = app[2]
    await send(app, button(app, conn, sid))
    await state(app).clear()
    await state(app).update_data(session_id=sid)
    app[1].reset_mock()
    await send(app, '🔄 Оновити')
    assert app[1].edit_message_text.await_args.kwargs['message_id'] == 42
    assert not app[1].called


@pytest.mark.asyncio
async def test_deleted_queue_message_is_replaced_once_then_refreshed(conn, app):
    app[1].return_value = response(app, mid=99)
    app[1].edit_message_text.side_effect = TelegramBadRequest(
        method=EditMessageText(text='Черга', chat_id=1, message_id=42),
        message='Bad Request: message to edit not found')
    await refresh_callback(app, app[2])
    assert sum(isinstance(c.args[0], SendMessage) for c in app[1].call_args_list) == 1
    app[1].reset_mock(side_effect=True)
    await send(app, '🔄 Оновити')
    assert app[1].edit_message_text.await_args.kwargs['message_id'] == 99
    assert not app[1].called


@pytest.mark.asyncio
async def test_admin_reply_refresh_keeps_admin_view_and_uses_own_message(conn, app, make_user):
    make_user(100, 'Адмін Власник')
    app[1].return_value = response(app, uid=100, mid=88)
    await state(app, 100).update_data(session_id=app[2])
    await send(app, '⚡ Режим адміна', uid=100)
    app[1].reset_mock()
    await send(app, '🔄 Оновити', uid=100)
    edit = app[1].edit_message_text.await_args.kwargs
    assert edit['chat_id'] == 100 and edit['message_id'] == 88
    assert 'Режим адміна' in edit['text']
    assert not app[1].called


def test_active_queue_and_personal_list_have_no_permanence_labels(conn, app):
    sid = app[2]
    queues.join_queue(conn, 1, sid)
    session = queues.get_session(conn, sid)
    for text in (format_queue(app[0]['subjects'], session, queues.get_entries(conn, sid), 1),
                 format_my_queues_overview(conn, [session], app[0]['subjects'], 1)):
        assert 'постійна' not in text.casefold() and 'запис доступний завжди' not in text.casefold()
    assert 'Ваше місце' in format_my_queues_overview(conn, [session], app[0]['subjects'], 1)
