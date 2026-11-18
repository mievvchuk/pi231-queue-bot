"""Exercise psycopg's real parameter parser without a PostgreSQL server."""
from unittest.mock import MagicMock

import psycopg
import pytest
from psycopg._queries import PostgresQuery
from psycopg.adapt import Transformer

from queue_bot.storage.database import PGConnectionWrapper


@pytest.mark.parametrize('sql,params,expected', [
    ("SELECT key FROM bot_state WHERE key LIKE 'lesson_schedule:%:signup'", (),
     b"SELECT key FROM bot_state WHERE key LIKE 'lesson_schedule:%:signup'"),
    ("SELECT key FROM bot_state WHERE key LIKE ? AND value != '%'", ('prefix:%',),
     b"SELECT key FROM bot_state WHERE key LIKE $1 AND value != '%'"),
    ("SELECT ? % 2", (5,), b'SELECT $1 % 2'),
    ("SELECT '%s', '%b', '%t', '100%'", (), b"SELECT '%s', '%b', '%t', '100%'"),
    ("SELECT key FROM bot_state WHERE value LIKE ?", ('%student%',), b'SELECT key FROM bot_state WHERE value LIKE $1'),
    ('INSERT INTO bot_state(key,value) VALUES (?,?)', ('key', '100%'), b'INSERT INTO bot_state(key,value) VALUES ($1,$2)'),
])
def test_wrapper_preserves_literal_percents_and_binds_only_actual_parameters(sql, params, expected):
    raw = MagicMock()
    raw.closed = False
    conn = PGConnectionWrapper(raw, 'unused-offline-dsn')
    converted = []
    def execute(query, values):
        parsed = PostgresQuery(Transformer())
        parsed.convert(query, values)
        converted.append(parsed.query)
    raw.cursor.return_value.execute.side_effect = execute
    conn.execute(sql, params)
    assert converted == [expected]


def test_original_scheduler_query_reproduces_reported_programming_error():
    with pytest.raises(psycopg.ProgrammingError, match='placeholders'):
        PostgresQuery(Transformer()).convert("SELECT key FROM bot_state WHERE key LIKE 'lesson_schedule:%:signup'", ())


def test_failed_query_adds_template_but_never_bound_values_to_traceback():
    raw = MagicMock()
    raw.closed = False
    conn = PGConnectionWrapper(raw, 'unused-offline-dsn')
    raw.cursor.return_value.execute.side_effect = psycopg.ProgrammingError('invalid SQL')
    with pytest.raises(psycopg.ProgrammingError) as caught:
        conn.execute('SELECT value FROM bot_state WHERE key = ?', ('private-bound-value',))
    assert 'SELECT value FROM bot_state WHERE key = ?' in str(caught.value.__notes__)
    assert 'private-bound-value' not in str(caught.value.__notes__)


def test_busy_connection_is_not_pinged_before_every_query(monkeypatch):
    from queue_bot.storage import database
    clock = [1000.0]
    monkeypatch.setattr(database, '_monotonic', lambda: clock[0])
    raw = MagicMock()
    raw.closed = False
    conn = PGConnectionWrapper(raw, 'unused-offline-dsn')
    for _ in range(5):
        conn.execute('SELECT value FROM bot_state WHERE key = ?', ('k',))
    assert raw.execute.call_count == 0  # no extra SELECT 1 round trips
    clock[0] += PGConnectionWrapper.PING_IDLE_SECONDS
    conn.execute('SELECT value FROM bot_state WHERE key = ?', ('k',))
    raw.execute.assert_called_once_with('SELECT 1')


def test_dropped_connection_reconnects_without_waiting_for_idle(monkeypatch):
    raw = MagicMock()
    raw.closed = False
    conn = PGConnectionWrapper(raw, 'unused-offline-dsn')
    fresh = MagicMock()
    fresh.closed = False
    monkeypatch.setattr(conn, '_connect_raw', lambda: fresh)
    raw.closed = True
    conn.execute('SELECT 1 FROM bot_state')
    assert conn.raw is fresh
