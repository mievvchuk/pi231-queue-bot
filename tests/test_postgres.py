"""Integration tests use a disposable schema in an explicitly supplied TEST database."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from queue_bot.storage import database as db
from queue_bot.services import admins, queues, restrictions, users


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
    sid = pg_conn.raw.execute('INSERT INTO queue_sessions (subject_id, teacher_id) VALUES (1, 1) RETURNING id').fetchone()[0]
    pg_conn.raw.execute('INSERT INTO queue_entries (session_id, user_id, position) VALUES (%s, 1, 1)', (sid,))
    pg_conn._init_schema()
    row = queues.get_entries(pg_conn, sid)[0]
    assert row['lab_count'] is None and row['planned_minutes'] is None and row['telegram_id'] == 1 and row['position'] == 1
    assert row['labs_passed'] == 0
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
