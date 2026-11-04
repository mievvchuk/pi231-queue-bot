import json
from collections import OrderedDict
from datetime import datetime
from unittest.mock import AsyncMock

import psycopg
import pytest
from aiogram.types import Chat, Message, User, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from queue_bot.handlers.common import Guard
from queue_bot.mini_app import api
from queue_bot.services import admins, bugs, diagnostics, queues
from tests.test_mini_app import client, headers


@pytest.fixture(autouse=True)
def clean_diagnostics(monkeypatch):
    monkeypatch.setattr(diagnostics, '_recent', OrderedDict())
    monkeypatch.setattr(diagnostics, '_sent', OrderedDict())


@pytest.mark.asyncio
async def test_full_traceback_goes_only_to_owner_and_bug_report_keeps_its_snapshot(conn, make_user, monkeypatch):
    for uid, name in ((1, 'Петренко Іван'), (2, 'Коваль Андрій'), (200, 'Адмін Другий')):
        make_user(uid, name)
    admins.add_admin(conn, 100, 200)
    monkeypatch.setenv('TEST_SECRET_TOKEN', 'private-diagnostic-secret')
    bot = AsyncMock()
    try:
        raise RuntimeError('private-diagnostic-secret postgresql://u:password@host/db')
    except RuntimeError as error:
        record = await diagnostics.report(error, bot=bot, conn=conn, telegram_id=1,
            context={'source':'telegram','handler':'mark_passed','session_id':7})
    assert [c.args[0] for c in bot.send_document.call_args_list] == [100]
    raw = bot.send_document.call_args.args[1].data.decode()
    assert 'Traceback' in raw and 'mark_passed' in raw and 'RuntimeError' in raw
    assert 'private-diagnostic-secret' not in raw and 'postgresql://' not in raw
    report_id = bugs.create_bug_report(conn, 1, 'Не працює кнопка')
    bugs.create_bug_report(conn, 2, 'Інша проблема')
    assert conn.execute('SELECT COUNT(*) FROM bot_state WHERE key LIKE ?', ('bug_diagnostic:%',)).fetchone()[0] == 1
    diagnostics._recent.clear()  # A later exception/restart must not change an existing report's attachment.
    bot.reset_mock()
    await bugs.notify_admins_new_bug(bot, conn, report_id)
    assert bot.send_document.call_args.kwargs['chat_id'] == 100
    attachment = json.loads(bot.send_document.call_args.kwargs['document'].data)
    assert attachment['code'] == record['code']
    assert bot.send_message.call_args.kwargs['chat_id'] == 100


@pytest.mark.asyncio
async def test_telegram_student_gets_explanation_and_owner_gets_full_context(conn, make_user):
    make_user(1, 'Тест Студент')
    bot = AsyncMock()
    message = Message(message_id=1, date=datetime.now(), chat=Chat(id=1,type='private'),
        from_user=User(id=1,is_bot=False,first_name='Тест'),text='🔄 Оновити').as_(bot)
    async def handler(event, data):
        raise psycopg.ProgrammingError("only '%s', '%b', '%t' are allowed as placeholders, got '%'")
    state = AsyncMock()
    state.get_state.return_value = None
    state.get_data.return_value = {'session_id':7,'password':'never-include-form-values'}
    await Guard()(handler, message, {'conn':conn,'state':state})
    student_text = bot.send_message.call_args.args[1]
    assert 'базою даних' in student_text and 'ERR-' in student_text
    assert 'ProgrammingError' not in student_text and 'Traceback' not in student_text
    owner_data = json.loads(bot.send_document.call_args.args[1].data)
    assert owner_data['context']['button'] == '🔄 Оновити'
    assert owner_data['context']['session_id'] == '7'
    assert 'never-include-form-values' not in str(owner_data)


@pytest.mark.asyncio
async def test_mini_app_unexpected_error_has_human_reason_and_owner_only_trace(client, conn, monkeypatch):
    sid = queues.create_session(conn, 100, 1, 1)
    def fail(*args, **kwargs):
        raise psycopg.ProgrammingError('invalid SQL')
    monkeypatch.setattr(api, 'queue_view', fail)
    result = await client.get(f'/api/miniapp/queues/{sid}', headers=headers(1))
    assert result.status == 500
    data = await result.json()
    assert data['error_code'].startswith('ERR-') and 'базою даних' in data['error']
    assert 'Traceback' not in str(data) and 'invalid SQL' not in str(data)
    bot = client.server.app[api.CONTEXT]['bot']
    assert bot.send_document.call_args.args[0] == 100
    assert json.loads(bot.send_document.call_args.args[1].data)['context']['route'] == '/api/miniapp/queues/{sid}'


@pytest.mark.asyncio
async def test_repeated_worker_errors_are_logged_without_flooding_owner(conn, caplog):
    bot = AsyncMock()
    for _ in range(3):
        await diagnostics.report(RuntimeError('offline'), bot=bot, conn=conn,
                                 context={'source':'worker','operation':'lesson_tick'})
    assert bot.send_document.await_count == 1
    assert sum('Incident ERR-' in r.message for r in caplog.records) == 3


@pytest.mark.asyncio
async def test_callback_failure_reports_button_label_without_callback_token(conn, make_user):
    make_user(1, 'Тест Студент')
    bot = AsyncMock()
    payload = 'qa:7:private-callback-token:ready'
    message = Message(message_id=1,date=datetime.now(),chat=Chat(id=1,type='private'),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='Готовий здавати',callback_data=payload)]]))
    event = CallbackQuery(id='test',from_user=User(id=1,is_bot=False,first_name='Тест'),
        chat_instance='private',message=message,data=payload).as_(bot)
    async def fail(event, data):
        raise RuntimeError('broken callback')
    await Guard()(fail, event, {'conn':conn})
    data = json.loads(bot.send_document.call_args.args[1].data)
    assert data['context']['button'] == 'Готовий здавати'
    assert 'private-callback-token' not in str(data)
    assert 'Готовий здавати' in bot.send_document.call_args.kwargs['caption']
