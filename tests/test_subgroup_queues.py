from datetime import datetime, timedelta

import pytest

from queue_bot import config
from queue_bot.services import queues, users, schedule
from queue_bot.services.errors import BotError
from queue_bot.services.subjects import load_subjects
from tests.test_mini_app import client, headers
from tests.test_navigation_bans import app, send


@pytest.mark.parametrize('queue_type', ['lesson', 'consultation'])
def test_only_kpz_filters_by_teacher_and_subgroup(conn, make_user, queue_type):
    make_user(1, 'Петренко Іван')
    make_user(2, 'Коваль Андрій')
    users.set_subgroup(conn, 2, 2)
    first = queues.create_session(conn, 100, 5, 3, queue_type=queue_type)
    second = queues.create_session(conn, 100, 5, 2, queue_type=queue_type)
    common = [queues.create_session(conn, 100, subject, teacher, queue_type=queue_type)
              for subject, teacher in [(1, 1), (1, 4), (2, 2), (2, 3), (3, 4), (7, 2), (7, 3)]]
    assert {s['id'] for s in queues.list_student_sessions(conn, 1)} == {first, *common}
    assert {s['id'] for s in queues.list_student_sessions(conn, 2)} == {second, *common}
    queues.join_queue(conn, 1, first)
    queues.join_queue(conn, 2, second)
    for uid, wrong in [(1, second), (2, first)]:
        with pytest.raises(BotError, match='іншої підгрупи'):
            queues.join_queue(conn, uid, wrong)
    for sid in common:
        queues.join_queue(conn, 1, sid)
        queues.join_queue(conn, 2, sid)
    assert {s['id'] for s in queues.list_student_sessions(conn, 2, mine=True)} == {second, *common}
    # Correcting a subgroup updates visibility immediately, without deleting old entries.
    users.set_subgroup(conn, 1, 2)
    assert first not in {s['id'] for s in queues.list_student_sessions(conn, 1, mine=True)}
    assert queues.get_user_position(conn, 1, first) == 1


def test_missing_subgroup_and_creation_cannot_bypass_rule(conn, make_user):
    make_user(1, 'Петренко Іван')
    with pytest.raises(BotError, match='іншої підгрупи'):
        queues.create_queue(conn, 1, 5, 2)
    assert queues.list_sessions(conn, ('open',)) == []
    conn.execute('UPDATE users SET subgroup = NULL WHERE telegram_id = 1')
    with pytest.raises(BotError, match='оберіть свою підгрупу'):
        queues.create_queue(conn, 1, 5, 3)
    assert queues.create_queue(conn, 1, 1, 1)


@pytest.mark.asyncio
async def test_miniapp_filters_lists_catalog_and_direct_access_but_preserves_admin_view(conn, client):
    from queue_bot.mini_app import api
    client.server.app[api.CONTEXT]['subjects'] = load_subjects(config.SUBJECTS_PATH)
    users.set_subgroup(conn, 2, 2)
    first = queues.create_session(conn, 100, 5, 3)
    second = queues.create_session(conn, 100, 5, 2, queue_type='consultation')
    common = queues.create_session(conn, 100, 2, 2)
    for uid, own, wrong in [(1, first, second), (2, second, first)]:
        response = await client.get('/api/miniapp/bootstrap', headers=headers(uid))
        assert {row['id'] for row in (await response.json())['queues']} == {own, common}
        response = await client.get('/api/miniapp/catalog', headers=headers(uid))
        subject = next(s for s in (await response.json())['subjects'] if s['id'] == 5)
        assert [t['id'] for t in subject['teachers']] == ([3] if uid == 1 else [2])
        assert (await client.get(f'/api/miniapp/queues/{own}', headers=headers(uid))).status == 200
        assert (await client.get(f'/api/miniapp/queues/{wrong}', headers=headers(uid))).status == 400
        assert (await client.post(f'/api/miniapp/queues/{wrong}/join', headers=headers(uid),
                                  json={'labs': [1], 'minutes': 10})).status == 400
        wrong_session = queues.get_session(conn, wrong)
        assert (await client.post('/api/miniapp/queues', headers=headers(uid), json={
            'subject_id': 5, 'teacher_id': wrong_session['teacher_id'],
            'type': wrong_session['queue_type']})).status == 400
    response = await client.get('/api/miniapp/bootstrap?mode=admin', headers=headers(100))
    assert {row['id'] for row in (await response.json())['queues']} == {first, second, common}
    assert (await client.get(f'/api/miniapp/queues/{second}?mode=admin', headers=headers(100))).status == 200


@pytest.mark.asyncio
async def test_telegram_all_queues_show_own_subgroup_and_shared(conn, app):
    first = queues.create_session(conn, 100, 5, 3)
    second = queues.create_session(conn, 100, 5, 2)
    common = queues.create_session(conn, 100, 1, 1)
    await send(app, 'Черги', uid=1)
    buttons = app[1].call_args.args[0].reply_markup.inline_keyboard
    links = {b.callback_data for row in buttons for b in row}
    assert {f'findq:{first}', f'findq:{common}'} <= links
    assert f'findq:{second}' not in links
    users.set_subgroup(conn, 1, 2)
    await send(app, 'Черги', uid=1)
    buttons = app[1].call_args.args[0].reply_markup.inline_keyboard
    links = {b.callback_data for row in buttons for b in row}
    assert {f'findq:{second}', f'findq:{common}'} <= links
    assert f'findq:{first}' not in links


def test_remaining_pairs_decrement_at_start_once_and_survive_restart(conn, db_path):
    from queue_bot.storage.database import connect
    schedule.replace_schedule_from_backup(conn, {'thursday': [
        {'pair': 1, 'subject': 'КПЗ'}, {'pair': 1, 'subject': 'Конструювання програмного забезпечення'}]})
    begin = datetime(2026, 11, 26, 8, 30, tzinfo=schedule.UK_TZ)
    schedule.save_subject_pair_plan(conn, 'КПЗ', 2, now=begin - timedelta(hours=1))
    def remaining(connection, now):
        return next(iter(schedule.get_remaining_pairs(connection, now=now, project_finish=False).values()))['remaining']
    assert remaining(conn, begin - timedelta(seconds=1)) == 2
    for now in (begin, begin + timedelta(minutes=1), begin + timedelta(hours=2)):
        assert remaining(conn, now) == 1
    reopened = connect(db_path)
    try:
        assert remaining(reopened, begin) == 1
    finally:
        reopened.close()
    # A manual reset during a class describes the count remaining from that moment.
    schedule.save_subject_pair_plan(conn, 'КПЗ', 3, now=begin + timedelta(minutes=10))
    assert remaining(conn, begin + timedelta(hours=2)) == 3


@pytest.mark.parametrize('replacement, expected', [('Пару скасовано', 2), ('МН', 1)])
def test_remaining_pairs_at_start_respect_cancellation_and_replacement(conn, replacement, expected):
    from queue_bot.services import replacements
    schedule.replace_schedule_from_backup(conn, {'thursday': [{'pair': 2, 'subject': 'КПЗ'}]})
    begin = datetime(2026, 11, 26, 10, 0, tzinfo=schedule.UK_TZ)
    schedule.save_subject_pair_plan(conn, 'МН', 2, now=begin - timedelta(hours=1))
    schedule.save_subject_pair_plan(conn, 'КПЗ', 2, now=begin - timedelta(hours=1))
    replacements.save_replacement(conn, {'date': '2026-11-26', 'lesson_number': 2, 'new_subject': replacement})
    plans = schedule.get_remaining_pairs(conn, now=begin, project_finish=False)
    assert {p['subject_name']: p['remaining'] for p in plans.values()} == {'МН': expected, 'КПЗ': 2}


def test_working_saturday_consumes_pair_at_start(conn):
    from queue_bot.services import working_days
    schedule.replace_schedule_from_backup(conn, {'thursday': [{'pair': 1, 'subject': 'КПЗ'}]})
    working_days.set_working_saturday(conn, '2026-11-21', 'thursday', 'denominator')
    begin = datetime(2026, 11, 21, 8, 30, tzinfo=schedule.UK_TZ)
    schedule.save_subject_pair_plan(conn, 'КПЗ', 1, now=begin - timedelta(hours=1))
    plan, = schedule.get_remaining_pairs(conn, now=begin).values()
    assert (plan['remaining'], plan['finish_date']) == (0, '2026-11-21')
