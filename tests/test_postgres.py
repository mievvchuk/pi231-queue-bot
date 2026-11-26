"""Integration tests use a disposable schema in an explicitly supplied TEST database."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from queue_bot.storage import database as db
from queue_bot.services import admins, queues, restrictions, users
from queue_bot.services import swap_requests


def test_postgres_subgroup_restart_and_permanent_queue_deletion(pg_conn):
    from queue_bot.services import attendance

    conn = pg_conn
    users.register_user(conn, 100, 'Адмін Власник')
    users.set_subgroup(conn, 1, 2)
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid)
    queues.join_queue(conn, 100, sid)
    entries = queues.get_entries(conn, sid)
    swap_requests.create(conn, 1, entries[0]['id'], entries[1]['id'])
    attendance.start(conn, 100, sid, minutes=10)
    conn._init_schema()
    assert users.get_user(conn, 1)['subgroup'] == 2
    queues.delete_session(conn, 100, sid)
    assert queues.get_session(conn, sid) is None
    for table in ('queue_entries', 'swap_requests', 'lesson_runs', 'lesson_attendance', 'lesson_messages'):
        assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0


def test_postgres_grid_imports_v1_grades_and_keeps_independent_components(pg_conn):
    from queue_bot.services import journal
    conn=pg_conn
    key='конструюванняпрограмногозабезпечення'
    subjects=[{'id':5,'name':'Конструювання програмного забезпечення','teachers':[]}]
    uid=users.get_user(conn,1)['id']
    old=conn.execute('''INSERT INTO journal_records(user_id,period,subject_key,subject_name,kind,number,grade,status)
        VALUES (?,'2026-2027:1',?,'Конструювання програмного забезпечення','lab',1,4,'approved')''',(uid,key)).lastrowid
    conn.execute("INSERT INTO journal_events(record_id,actor_id,action,snapshot) VALUES (?,100,'approved','{}')",(old,))
    conn._init_schema()
    values={'period':'2026-2027:1','subject_key':key,'kind':'lab','number':1,'grade':5}
    journal.submit(conn,1,subjects,{**values,'record_id':old,'revision':0})
    journal.submit(conn,1,subjects,{**values,'part':2,'grade':2})
    conn._init_schema()
    rows=journal.view(conn,1,subjects,period='2026-2027:1')['records']
    assert [(r['part'],r['grade']) for r in rows] == [(1,5),(2,2)]
    assert all('status' not in row for row in rows)
    assert conn.execute('SELECT COUNT(*) FROM journal_cell_events').fetchone()[0] == 3
    assert conn.execute('SELECT grade FROM journal_records WHERE id=?',(old,)).fetchone()[0] == 4


def test_postgres_free_slots_retirement_and_journal_survive_schema_restart(pg_conn):
    from queue_bot.services import journal
    conn = pg_conn
    sid = queues.create_session(conn,100,1,1)
    queues.join_queue(conn,1,sid,position=14)
    uid = users.get_user(conn,1)['id']
    values = {'period':'2026-2027:1','subject_key':'програмування','kind':'lab','number':1,'grade':5}
    subjects = [{'id':1,'name':'Програмування','teachers':[]}]
    rid = journal.submit(conn,1,subjects,values)
    journal.submit(conn,1,subjects,{**values,'record_id':rid,'revision':0,'grade':4.2})
    journal.set_date(conn,100,subjects,{**values,'due_date':'2026-12-01'})
    queues.leave_queue(conn,1,sid)
    users.admin_delete_user(conn,100,uid)
    conn._init_schema()
    assert users.get_user(conn,1) is None
    assert queues.get_entries(conn,sid)[13]['user_id'] is None
    assert queues.get_history(conn,sid)[0]['status'] == 'left'
    assert conn.execute('SELECT COUNT(*) FROM journal_cell_events').fetchone()[0] == 2
    users.register_user(conn,1,'Петренко Іван')
    data = journal.view(conn,1,subjects,period='2026-2027:1')
    assert 'status' not in data['records'][0]
    assert data['records'][0]['grade'] == 4.2
    assert data['dates'][0]['due_date'] == '2026-12-01'
    queues.join_queue(conn,1,sid,position=14)
    assert queues.get_user_position(conn,1,sid) == 14


@pytest.mark.asyncio
async def test_postgres_turn_reminder_releases_without_marking_passed(pg_conn, monkeypatch):
    from unittest.mock import AsyncMock
    from queue_bot.services import attendance as a
    from queue_bot.services.lesson_notifier import deliver
    clock = [10000]
    monkeypatch.setattr(a, 'now_seconds', lambda: clock[0])
    sid = queues.create_session(pg_conn, 100, 1, 1)
    queues.join_queue(pg_conn, 1, sid, lab_numbers=[1,2], planned_minutes=15)
    a.start(pg_conn, 100, sid)
    token = lambda: a.view(pg_conn, sid, 1)['own']['token']
    a.respond(pg_conn, 1, token(), 'present')
    a.respond(pg_conn, 1, token(), 'ready')
    clock[0] += 900
    a.tick(pg_conn)
    await deliver(AsyncMock(), pg_conn, [])
    assert a.view(pg_conn, sid, 1)['own']['deadline'] == clock[0] + 600
    clock[0] += 600
    a.tick(pg_conn)
    row = queues.get_entries(pg_conn, sid)[0]
    assert row['passed_labs'] == '[]' and row['labs_passed'] == 0
    assert a.view(pg_conn, sid, 1)['own']['state'] == 'skipped'


def test_postgres_scheduled_attendance_is_durable_and_keeps_priority(pg_conn):
    from queue_bot.services import attendance, lesson_scheduler, schedule
    sid = queues.create_session(pg_conn, 100, 1, 1)
    queues.join_queue(pg_conn, 1, sid)
    subjects = [{'id':1,'name':'КПЗ','teachers':[{'id':1,'name':'Щуцький С.В.'}]}]
    data = {'tuesday':[{'pair':1,'subject':'КПЗ','teacher':'Щуцький С.В.'}]}
    now = int(datetime(2026, 11, 3, 8, 35, tzinfo=schedule.UK_TZ).timestamp())
    lesson_scheduler.sync_schedule(pg_conn, subjects, now=now, schedule_data=data)
    first = attendance.active_run(pg_conn, sid, now=now)
    assert first and first['ends_at'] == now + 75 * 60
    lesson_scheduler.sync_schedule(pg_conn, subjects, now=now + 5, schedule_data=data)
    assert attendance.active_run(pg_conn, sid, now=now + 5)['id'] == first['id']
    assert queues.get_user_position(pg_conn, 1, sid) == 1


@pytest.mark.asyncio
async def test_postgres_lesson_migration_deadlines_and_priority(pg_conn, monkeypatch):
    from unittest.mock import AsyncMock
    from queue_bot.services import attendance
    from queue_bot.services.lesson_notifier import deliver
    monkeypatch.setattr(attendance, 'now_seconds', lambda: 10000)
    sid = queues.create_session(pg_conn, 100, 1, 1)
    queues.join_queue(pg_conn, 1, sid)
    attendance.start(pg_conn, 100, sid)
    await deliver(AsyncMock(), pg_conn, [])
    assert attendance.view(pg_conn, sid, 1)['own']['deadline'] == 10600
    attendance.tick(pg_conn, now=10600)
    assert attendance.view(pg_conn, sid, 1)['own']['state'] == 'skipped'
    assert queues.get_user_position(pg_conn, 1, sid) == 1


@pytest.fixture
def pg_conn():
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL not configured; PostgreSQL integration runs in CI')
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    schema = 'test_pi231_' + uuid.uuid4().hex
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    conn = None
    try:
        dsn = make_conninfo(url, options=f'-csearch_path={schema}')
        raw = psycopg.connect(dsn, autocommit=True)
        # Simulate an older deployed user table before the additive startup migration.
        raw.execute('CREATE TABLE users (id SERIAL PRIMARY KEY, telegram_id BIGINT NOT NULL UNIQUE, '
                    'full_name TEXT NOT NULL, is_blocked BOOLEAN NOT NULL DEFAULT FALSE, '
                    'created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        raw.execute("INSERT INTO users (telegram_id, full_name) VALUES (1, 'Петренко Іван')")
        conn = db.PGConnectionWrapper(raw, dsn)
        admins.init_admins(conn, [100])
        yield conn
    finally:
        if conn:
            conn.close()
        admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        admin.close()


def test_postgres_additive_migration_ban_and_restart(pg_conn):
    conn = pg_conn
    assert users.get_user(conn, 1)['full_name'] == 'Петренко Іван'
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid)
    now = datetime.now(timezone.utc)
    restrictions.set_restriction(conn, 100, users.get_user(conn, 1)['id'], days=1, now=now)
    conn.close()
    # Reopen through the real adapter to exercise persisted restrictions.
    conn.raw = conn._connect_raw()
    conn._init_schema()
    assert restrictions.restriction_message(conn, 1, now=now)
    assert queues.get_history(conn, sid)[0]['status'] == 'removed'
    assert restrictions.restriction_message(conn, 1, now=now + timedelta(days=1)) is None
    assert queues.get_user_position(conn, 1, sid) is None


def test_postgres_consultation_renewal(pg_conn):
    lesson = queues.create_session(pg_conn, 100, 1, 1)
    consultation = queues.create_session(pg_conn, 100, 1, 1, queue_type='consultation')
    renewed = queues.finish_session(pg_conn, 100, consultation, create_new=True)
    assert queues.get_session(pg_conn, renewed)['queue_type'] == 'consultation'
    assert queues.get_session(pg_conn, lesson)['status'] == 'open'


@pytest.mark.asyncio
async def test_postgres_mini_app_shares_bot_signup_completion_and_replacements(pg_conn, monkeypatch):
    import hashlib
    import hmac
    import json
    import time
    from urllib.parse import urlencode
    from unittest.mock import AsyncMock
    from aiohttp.test_utils import TestClient, TestServer
    from queue_bot.web import create_web_app
    token = '123456:offline-miniapp-pg'
    fields = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': 1})}
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    fields['hash'] = hmac.new(secret, '\n'.join(f'{k}={v}' for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
    auth = {'Authorization': 'tma ' + urlencode(fields)}
    sid = queues.create_session(pg_conn, 100, 1, 1)
    from queue_bot import config
    monkeypatch.setattr(config, 'MINI_APP_URL', 'https://offline.example/app/')
    users.register_user(pg_conn, 100, 'Адмін Власник')
    catalog = [{'id':1, 'name':'Програмування в Інтернет', 'teachers':[{'id':1, 'name':'Викладач'}]}]
    app = create_web_app(conn=pg_conn, subjects=catalog, bot=AsyncMock(), bot_token=token)
    async with TestClient(TestServer(app)) as client:
        path = f'/api/miniapp/queues/{sid}'
        assert (await client.post(path+'/join', headers=auth, json={'labs':[1], 'minutes':10})).status == 200
        row = queues._own_active_entry(pg_conn, 1, sid)
        assert json.loads(row['selected_labs']) == [1]
        assert (await client.post(path+'/complete', headers=auth,
            json={'entry_id':row['id'], 'revision':row['entry_revision']})).status == 200
        assert queues.get_user_position(pg_conn, 1, sid) is None
        assert queues.get_history(pg_conn, sid)[0]['labs_passed'] == 1
        from queue_bot.services import replacements
        replacements.save_replacement(pg_conn, {'date':'2026-10-30', 'lesson_number':3,
            'new_subject':'Філософія', 'teacher':'Багас Л.М.', 'room':'138'})
        response = await client.get('/api/miniapp/schedule?date=2026-10-30', headers=auth)
        assert response.status == 200
        data = await response.json()
        lesson = next(item for item in data['lessons'] if item['number'] == 3)
        assert lesson['subject'] == 'Філософія' and lesson['is_replacement']
        assert data['replacements'][0]['new_subject'] == 'Філософія'
        assert 'created_at' not in data['replacements'][0]
        owner_fields = {'auth_date':str(int(time.time())), 'user':json.dumps({'id':100})}
        owner_fields['hash'] = hmac.new(secret, '\n'.join(f'{k}={v}' for k,v in sorted(owner_fields.items())).encode(), hashlib.sha256).hexdigest()
        owner_auth = {'Authorization':'tma '+urlencode(owner_fields)}
        response = await client.post(f'/api/miniapp/admin/queues/{sid}?mode=admin', headers=owner_auth,
            json={'action':'add_students','names':['Коваль Андрій','Мельник Олена']})
        assert response.status == 200
        history = await (await client.get(f'/api/miniapp/admin/manage/history?mode=admin&sid={sid}',headers=owner_auth)).json()
        assert len([r for r in history['entries'] if r['status']=='active']) == 2
        response = await client.get('/api/miniapp/admin/manage/reports?mode=admin',headers=owner_auth)
        assert response.status == 410 and 'прибрано' in (await response.json())['error']
        expected = ['Гнатюк Іван', 'Ґалаган Іван', 'Денисюк Іван', 'Емець Іван', 'Євчук Михайло',
                    'Жук Іван', 'Зінченко Іван', 'Иваненко Іван', 'Іваненко Іван', 'Їжакевич Іван', 'Йовенко Іван']
        for name in reversed(expected):
            users.admin_create_placeholder(pg_conn, 100, name)
        response = await client.get('/api/miniapp/admin/users?mode=admin', headers=owner_auth)
        assert response.status == 200
        roster = (await response.json())['users']
        assert len(roster) == len(expected) + 4
        assert [row['full_name'] for row in roster if row['full_name'] in expected] == expected
        assert (await client.get('/api/miniapp/polls',headers=auth)).status == 404
        assert (await client.post('/api/miniapp/bugs',headers=auth,json={'text':'Перевірка PostgreSQL'})).status == 200
        response = await client.get('/api/miniapp/admin/manage/bugs?mode=admin',headers=owner_auth)
        assert response.status == 200 and isinstance((await response.json())['items'][0]['created_at'],str)


def test_postgres_swap_deadline_restart_and_single_answer(pg_conn):
    sid = queues.create_session(pg_conn, 100, 1, 1)
    users.register_user(pg_conn, 2, 'Студент Два')
    queues.join_queue(pg_conn, 1, sid)
    queues.join_queue(pg_conn, 2, sid)
    a, b = queues.get_entries(pg_conn, sid)
    now = datetime.now(timezone.utc)
    old = swap_requests.create(pg_conn, 1, a['id'], b['id'], now=now)
    pg_conn.close()
    pg_conn.raw = pg_conn._connect_raw()
    pg_conn._init_schema()
    later = now + timedelta(minutes=10)
    fresh = swap_requests.create(pg_conn, 1, a['id'], b['id'], now=later)
    from queue_bot.services.errors import BotError
    with pytest.raises(BotError):
        swap_requests.respond(pg_conn, 2, old['token'], accept=True, now=later)
    swap_requests.respond(pg_conn, 2, fresh['token'], accept=True, now=later)
    assert queues.get_entry(pg_conn, a['id'])['position'] == 2
    swap_requests.respond(pg_conn, 2, fresh['token'], accept=True, now=later + timedelta(hours=1))
    assert queues.get_entry(pg_conn, a['id'])['position'] == 1
    # Leaving must not be prevented by invitation foreign keys.
    queues.leave_queue(pg_conn, 1, sid)


def test_postgres_select_survives_schema_change_and_reconnect(pg_conn):
    # Reproduce a hot SELECT * cached before another deployment adds a column.
    query = 'SELECT * FROM users WHERE telegram_id = ?'
    for _ in range(12):
        assert pg_conn.execute(query, (1,)).fetchone()['full_name'] == 'Петренко Іван'
    import psycopg
    with psycopg.connect(pg_conn.clean_url, autocommit=True) as migration:
        migration.execute('ALTER TABLE users ADD COLUMN plan_regression TEXT')
    assert pg_conn.execute(query, (1,)).fetchone()['plan_regression'] is None
    pg_conn.close()
    # The adapter reconnects automatically; the policy must survive reconnection.
    assert pg_conn.execute(query, (1,)).fetchone()['full_name'] == 'Петренко Іван'
    assert pg_conn.raw.prepare_threshold is None


def test_postgres_lab_count_migration_transfer_and_reconnect(pg_conn):
    # Fixture creates the old users table and starts the real additive adapter.
    sid = queues.create_session(pg_conn, 100, 1, 1)
    queues.join_queue(pg_conn, 1, sid, lab_count=2, planned_minutes=17)
    entry = queues.get_entries(pg_conn, sid)[0]
    assert entry['lab_count'] == 2 and entry['planned_minutes'] == 17
    new = queues.finish_session(pg_conn, 100, sid, create_new=True, carry={entry['id']: 2})
    pg_conn.close()
    assert [r['lab_count'] for r in queues.get_entries(pg_conn, new)] == [None, 2]
    assert [r['planned_minutes'] for r in queues.get_entries(pg_conn, new)] == [None, 17]
    queues.set_own_lab_count(pg_conn, 1, new, 3)
    assert queues.get_entries(pg_conn, new)[1]['lab_count'] == 3


def test_postgres_existing_entry_upgrades_to_unknown_count(pg_conn):
    pg_conn.raw.execute('ALTER TABLE queue_entries DROP COLUMN lab_count')
    pg_conn.raw.execute('ALTER TABLE queue_entries DROP COLUMN planned_minutes')
    pg_conn.raw.execute('ALTER TABLE queue_entries DROP COLUMN labs_passed')
    pg_conn.raw.execute('ALTER TABLE queue_entries DROP COLUMN selected_labs')
    pg_conn.raw.execute('ALTER TABLE queue_entries DROP COLUMN passed_labs')
    pg_conn.raw.execute('ALTER TABLE queue_entries DROP COLUMN entry_revision')
    sid = pg_conn.raw.execute('INSERT INTO queue_sessions (subject_id, teacher_id) VALUES (1, 1) RETURNING id').fetchone()[0]
    pg_conn.raw.execute('INSERT INTO queue_entries (session_id, user_id, position) VALUES (%s, 1, 1)', (sid,))
    pg_conn._init_schema()
    row = queues.get_entries(pg_conn, sid)[0]
    assert row['lab_count'] is None and row['planned_minutes'] is None and row['telegram_id'] == 1 and row['position'] == 1
    assert row['labs_passed'] == 0
    assert row['selected_labs'] is None and row['passed_labs'] == '[]' and row['entry_revision'] == 0
    pg_conn._init_schema()  # Repeated deploys are idempotent.
    assert len(queues.get_entries(pg_conn, sid)) == 1


def test_postgres_partial_completion_survives_restart(pg_conn):
    sid = queues.create_session(pg_conn, 100, 1, 1)
    queues.join_queue(pg_conn, 1, sid, lab_count=3, planned_minutes=15)
    entry = queues.get_entries(pg_conn, sid)[0]
    assert queues.record_own_passed_labs(pg_conn, 1, sid, 1, entry_id=entry['id'], expected_count=3) == 2
    pg_conn.close()
    current = queues.get_entries(pg_conn, sid)[0]
    assert current['position'] == 1 and current['labs_passed'] == 1
    assert current['lab_count'] == 2 and current['planned_minutes'] == 15
    assert queues.record_own_passed_labs(pg_conn, 1, sid, 2, entry_id=current['id'], expected_count=2) == 0
    assert queues.get_history(pg_conn, sid)[0]['labs_passed'] == 3


def test_postgres_numbered_labs_partial_transfer_and_restart(pg_conn):
    import json
    sid = queues.create_session(pg_conn, 100, 5, 1)
    queues.join_queue(pg_conn, 1, sid, lab_numbers=[1, 2], planned_minutes=35)
    old = queues.get_entries(pg_conn, sid)[0]
    assert queues.record_own_passed_lab_numbers(pg_conn, 1, sid, [1], entry_id=old['id'], expected_revision=old['entry_revision']) == [2]
    pg_conn.close()
    row = queues.get_entries(pg_conn, sid)[0]
    assert row['planned_minutes'] == 10 and row['position'] == 1
    assert json.loads(row['selected_labs']) == [2] and json.loads(row['passed_labs']) == [1]
    renewed = queues.finish_session(pg_conn, 100, sid, create_new=True, carry={old['id']: 2})
    current = queues.get_entries(pg_conn, renewed)[1]
    assert json.loads(current['selected_labs']) == [2] and current['passed_labs'] == '[]'
    assert current['labs_passed'] == 0
