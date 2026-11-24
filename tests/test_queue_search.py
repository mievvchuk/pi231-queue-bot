from datetime import date

import pytest

from queue_bot.handlers.student import search_sessions
from queue_bot.services import queues, schedule, queue_navigation, attendance
from tests.test_queue_navigation import app, send, state


def test_search_matches_abbreviations_teachers_and_queue_types(conn):
    subjects = [{'id': 1, 'name': 'Людино-машинна взаємодія',
                 'teachers': [{'id': 1, 'name': 'Ковальчук Іван'}]}]
    sid = queues.create_session(conn, 100, 1, 1)
    sessions = queues.list_sessions(conn, ('open', 'closed'))
    for query in ('ЛМВ', 'людино', 'МАШИННА', 'ковальчук', ''):
        assert [r['id'] for r in search_sessions(sessions, subjects, query)] == [sid]
    assert search_sessions(sessions, subjects, 'неіснуючий') == []


@pytest.mark.asyncio
async def test_search_has_five_results_and_restores_query_after_view(conn, app):
    for n in range(2, 9):
        queues.create_session(conn, 100, n, 1)
    await send(app, '📋 Усі черги')
    await send(app, 'Предмет')
    markup = app[1].call_args.args[0].reply_markup
    assert len([r for r in markup.inline_keyboard if r[0].callback_data.startswith('findq:')]) == 5
    await _click_search(app, 'findpage:2')
    assert (await state(app).get_data())['page'] == 2
    selected = app[1].call_args.args[0].reply_markup.inline_keyboard[0][0].callback_data
    sid = int(selected.split(':')[1])
    await _click_search(app, selected)
    assert queue_navigation.return_to(conn, 1, sid)['query'] == 'Предмет'
    await send(app, '🔙 До черг')
    data = await state(app).get_data()
    assert data['query'] == 'Предмет' and data['page'] == 2
    await send(app, 'неіснуючий')
    assert 'Нічого не знайдено' in app[1].call_args.args[0].text
    await send(app, 'Показати всі черги')
    assert (await state(app).get_data())['query'] == ''


async def _click_search(app, data):
    from datetime import datetime
    from aiogram.types import CallbackQuery, Chat, Message, Update, User
    message = Message(message_id=10, date=datetime.now(), chat=Chat(id=1, type='private'),
                      from_user=User(id=5, is_bot=True, first_name='Бот'), text='Пошук')
    callback = CallbackQuery(id='search', from_user=User(id=1, is_bot=False, first_name='Студент'),
                             chat_instance='private', message=message, data=data)
    await app[0].feed_update(app[1], Update(update_id=2, callback_query=callback))


@pytest.mark.asyncio
async def test_retired_manual_buttons_do_not_change_active_lesson(conn, app):
    sid = app[2]
    attendance.start(conn, 100, sid)
    before = dict(attendance.active_run(conn, sid))
    for button in ('Почати здачу на парі', 'Зупинити здачу на парі'):
        await state(app, 100).update_data(session_id=sid)
        await send(app, button, uid=100)
        assert dict(attendance.active_run(conn, sid)) == before
        menu = app[1].call_args_list[-2].args[0].reply_markup
        keys = [b.text for r in menu.keyboard for b in r]
        assert button not in keys


@pytest.mark.parametrize('numbers', [[2], [1, 2, 5], [3, 4]])
def test_schedule_shows_all_empty_weekday_slots(conn, numbers):
    data = {'monday': [{'number': n, 'subject': f'Предмет {n}'} for n in numbers]}
    text = schedule.format_schedule_for_date(conn, data, date(2026, 11, 2))
    for number in schedule.PAIR_TIMES:
        assert f'{number} пара' in text
        if number not in numbers:
            assert f'{number} пара — немає заняття' in text
    empty = schedule.format_schedule_for_date(conn, {}, date(2026, 11, 2))
    for number in schedule.PAIR_TIMES:
        assert f'{number} пара — немає заняття' in empty
    weekend = schedule.format_schedule_for_date(conn, {}, date(2026, 11, 8))
    assert 'Занять немає' in weekend and '1 пара' not in weekend


@pytest.mark.asyncio
async def test_remaining_pairs_appear_in_telegram_queue_search_and_details(conn, app, monkeypatch):
    from queue_bot.handlers import common
    from queue_bot.services.lesson_scheduler import subject_name

    sid = app[2]
    session = queues.get_session(conn, sid)
    subjects = app[0]["subjects"]
    subject, _ = common.session_names(subjects, session)
    monkeypatch.setattr(schedule, "get_remaining_pairs", lambda conn: {
        subject_name(subject): {"remaining": 2, "finish_date": "2026-12-03"},
    })
    expected = "Залишилось пар: <b>2</b> · остання пара 03.12.2026"

    await send(app, "📋 Усі черги")
    await send(app, "Показати всі черги")
    assert expected in app[1].call_args.args[0].text
    await _click_search(app, f"findq:{sid}")
    assert expected in app[1].call_args.args[0].text


@pytest.mark.asyncio
async def test_queue_home_shows_results_and_mine_filter_survives_return(conn, app):
    sid = app[2]
    other = queues.create_session(conn, 100, 2, 1)
    queues.join_queue(conn, 1, sid)

    await send(app, 'Черги')
    action = app[1].call_args.args[0]
    assert f'findq:{sid}' in str(action.reply_markup) and f'findq:{other}' in str(action.reply_markup)
    assert 'Показати всі черги' not in str(action.reply_markup)

    await _click_search(app, 'findscope:mine')
    action = app[1].call_args.args[0]
    assert action.message_id == 10
    assert f'findq:{sid}' in str(action.reply_markup) and f'findq:{other}' not in str(action.reply_markup)
    await _click_search(app, f'findq:{sid}')
    await send(app, '🔙 До моїх черг')
    assert (await state(app).get_data())['queue_scope'] == 'mine'
    assert 'Мої черги' in app[1].call_args.args[0].text
    await _click_search(app, 'findnew')
    assert 'Оберіть тип черги' in app[1].call_args.args[0].text
