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
