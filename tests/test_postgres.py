"""Integration tests use a disposable schema in an explicitly supplied TEST database."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import db
from services import admins, queues, restrictions, users


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
