"""Acceptance stories for the agreed queue workflow, including real Telegram routing."""
from unittest.mock import AsyncMock

import pytest

from queue_bot.handlers.attendance import LateTime, screen_keyboard
from queue_bot.services import attendance as a, queues
from queue_bot.services.lesson_notifier import deliver
from tests.test_attendance import lesson, own, answer
from tests.test_numbered_flow import app, context, key, send, click


def test_first_roll_call_uses_priority_even_when_answers_arrive_backwards(conn, lesson):
    sid, clock = lesson
    conn.execute('UPDATE lesson_runs SET started_at = ? WHERE session_id = ?', (clock[0], sid))
    for uid in (4, 3, 2):
        answer(conn, sid, uid, 'present')
        assert a.view(conn, sid, uid)['collecting']
        assert a.view(conn, sid, uid)['current_name'] is None
    answer(conn, sid, 1, 'present')
    assert own(conn, sid, 1)['state'] == 'called'
    assert not a.view(conn, sid, 1)['collecting']
    assert a.view(conn, sid, 1)['ready_ids'] == [own(conn, sid, uid)['entry_id'] for uid in (1, 2, 3, 4)]


def test_new_signup_does_not_restart_collection_after_first_student_finishes(conn, lesson, make_user):
    sid, clock = lesson
    conn.execute('UPDATE lesson_runs SET started_at = ? WHERE session_id = ?', (clock[0], sid))
    for uid in (4, 3, 2, 1):
        answer(conn, sid, uid, 'present')
    queues.finish_own_entry(conn, 1, sid, passed=True)
    make_user(5, 'Новий Студент')
    queues.join_queue(conn, 5, sid, lab_numbers=[1], planned_minutes=10)
    a.tick(conn)
    assert own(conn, sid, 5)['state'] == 'pending'
    assert own(conn, sid, 2)['state'] == 'called'
    assert not a.view(conn, sid, 2)['collecting']


@pytest.mark.asyncio
async def test_collection_deadline_early_arrival_and_not_ready_do_not_reorder(conn, lesson):
    sid, clock = lesson
    conn.execute('UPDATE lesson_runs SET started_at = ? WHERE session_id = ?', (clock[0], sid))
    initial = [(e['id'], e['position']) for e in queues.get_entries(conn, sid)]
    answer(conn, sid, 1, 'late', minutes=25)
    answer(conn, sid, 3, 'absent')
    answer(conn, sid, 2, 'present')
    await deliver(AsyncMock(), conn, [])
    clock[0] += 599
    a.tick(conn)
    assert a.view(conn, sid, 2)['current_name'] is None
    clock[0] += 1
    a.tick(conn)
    assert own(conn, sid, 2)['state'] == 'called'
    assert own(conn, sid, 4)['state'] == 'skipped'
    answer(conn, sid, 2, 'ready')
    clock[0] += 10 * 60  # Student 1 arrives after 20, earlier than their 25-minute estimate.
    answer(conn, sid, 1, 'present')
    assert own(conn, sid, 2)['state'] == 'serving'
    assert own(conn, sid, 1)['state'] == 'present'
    queues.finish_own_entry(conn, 2, sid, passed=True)
    a.tick(conn)
    assert own(conn, sid, 1)['state'] == 'called'
    answer(conn, sid, 1, 'skip')
    assert own(conn, sid, 1)['state'] == 'not_ready'
    # Only explicit successful completion removed student 2; all other priorities survive.
    assert [e['id'] for e in queues.get_entries(conn, sid)] == [eid for eid, _ in initial if eid != initial[1][0]]


@pytest.mark.asyncio
async def test_telegram_notification_switches_queue_and_old_button_only_refreshes(conn, app):
    sid = app[2]
    queues.join_queue(conn, 1, sid, lab_numbers=[1, 2], planned_minutes=15)
    a.start(conn, 100, sid)
    old = a.view(conn, sid, 1)['own']['token']
    other = queues.create_session(conn, 100, 2, 1)
    await app[0].storage.set_data(key(), {'session_id':other})
    await app[0].storage.set_state(key(), LateTime.minutes)
    await click(app, f'qa:{sid}', 'present', nonce=old)
    assert (await context(app))['session_id'] == sid
    assert await app[0].storage.get_state(key()) is None
    current = dict(a.view(conn, sid, 1)['own'])
    await click(app, f'qa:{sid}', 'absent', nonce=old)
    assert dict(a.view(conn, sid, 1)['own']) == current
    calls = [str(c) for c in app[1].call_args_list]
    assert any('актуальні дії' in c for c in calls)
    assert not any('Unexpected' in c or '<b>Помилка:</b>' in c for c in calls)


@pytest.mark.asyncio
async def test_time_prompt_does_not_intercept_navigation_buttons(conn, app):
    await app[0].storage.set_data(key(), {'session_id':app[2],'attendance_token':'old'})
    await app[0].storage.set_state(key(), LateTime.minutes)
    await send(app, '🏠 Головне меню')
    assert await app[0].storage.get_state(key()) is None
    assert not any('ціле число' in str(c) for c in app[1].call_args_list)


def test_main_queue_only_shows_actions_for_the_current_state(conn, lesson):
    sid, _ = lesson
    answer(conn, sid, 1, 'present')
    buttons = [b for row in screen_keyboard(conn, sid, 1).inline_keyboard for b in row]
    assert len(buttons) == 4
    assert [b.text for b in buttons[:2]] == ['Готовий здавати', 'Не готовий, пропустити мене']
    answer(conn, sid, 1, 'ready')
    buttons = [b for row in screen_keyboard(conn, sid, 1).inline_keyboard for b in row]
    assert [b.text for b in buttons[:2]] == ['Позначити здані роботи', 'Завершити підхід']
    assert all(b.callback_data.startswith(('qa:', 'queue:')) for b in buttons)


@pytest.mark.asyncio
@pytest.mark.parametrize('section,expected', [('Черги', '👥 Мої черги'), ('Ще', '❓ Допомога')])
async def test_main_sections_exit_unfinished_input_and_return_to_three_buttons(conn, app, section, expected):
    await app[0].storage.set_data(key(), {'session_id':app[2], 'ui_mode':'admin'})
    await app[0].storage.set_state(key(), LateTime.minutes)
    await send(app, section)
    assert await app[0].storage.get_state(key()) is None
    assert (await context(app))['ui_mode'] == 'student'
    method = app[1].call_args[0][0]
    assert expected in [b.text for r in method.reply_markup.keyboard for b in r]
    await send(app, '🏠 Головне меню')
    method = app[1].call_args[0][0]
    assert [b.text for r in method.reply_markup.keyboard for b in r] == ['Черги', '📅 Розклад', 'Ще']
