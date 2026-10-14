import sqlite3
import re
from contextlib import contextmanager

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None

MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id BIGINT NOT NULL UNIQUE,
        full_name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id BIGINT NOT NULL UNIQUE,
        added_by BIGINT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS queue_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER NOT NULL,
        teacher_id INTEGER NOT NULL,
        lesson_title TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN ('open', 'closed', 'archived')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        closed_at TEXT
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_one_unfinished_session
        ON queue_sessions (subject_id, teacher_id)
        WHERE status IN ('open', 'closed');

    CREATE TABLE IF NOT EXISTS queue_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL REFERENCES queue_sessions(id),
        user_id INTEGER REFERENCES users(id),
        position INTEGER,
        status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'completed', 'left', 'removed', 'transferred')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_active_user_in_session
        ON queue_entries (session_id, user_id)
        WHERE status = 'active' AND user_id IS NOT NULL;
    CREATE UNIQUE INDEX IF NOT EXISTS ux_active_position
        ON queue_entries (session_id, position)
        WHERE status = 'active';

    CREATE TABLE IF NOT EXISTS admin_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_telegram_id BIGINT NOT NULL,
        action TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
]

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS admins (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL UNIQUE,
    added_by BIGINT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS queue_sessions (
    id SERIAL PRIMARY KEY,
    subject_id INTEGER NOT NULL,
    teacher_id INTEGER NOT NULL,
    lesson_title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'closed', 'archived')),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP WITH TIME ZONE
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_unfinished_session
    ON queue_sessions (subject_id, teacher_id)
    WHERE status IN ('open', 'closed');

CREATE TABLE IF NOT EXISTS queue_entries (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES queue_sessions(id),
    user_id INTEGER REFERENCES users(id),
    position INTEGER,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'completed', 'left', 'removed', 'transferred')),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_active_user_in_session
    ON queue_entries (session_id, user_id)
    WHERE status = 'active' AND user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_active_position
    ON queue_entries (session_id, position)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS admin_log (
    id SERIAL PRIMARY KEY,
    admin_telegram_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class PGRow(dict):
    """Емулює sqlite3.Row для словника psycopg."""
    def __getitem__(self, item):
        if isinstance(item, int):
            return list(self.values())[item]
        return super().__getitem__(item)


class PGCursorWrapper:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return PGRow(row)

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [PGRow(r) for r in rows]

    def __iter__(self):
        for r in self._cursor:
            yield PGRow(r)


class PGConnectionWrapper:
    """Обгортка над з'єднанням psycopg, що надає інтерфейс sqlite3.Connection
    (автоматично трансформує '?' в '%s' та підтримує lastrowid)."""

    def __init__(self, raw_conn):
        self.raw = raw_conn
        self._init_schema()

    def _init_schema(self):
        with self.raw.cursor() as cur:
            cur.execute(PG_SCHEMA)
        self.raw.commit()

    def execute(self, sql: str, params: tuple = ()):
        # Замінюємо ? на %s для psycopg
        pg_sql = sql.replace("?", "%s")
        # CURRENT_TIMESTAMP в SQLite і PG збігаються
        is_insert = pg_sql.strip().upper().startswith("INSERT INTO")
        lastrowid = None

        cur = self.raw.cursor(row_factory=dict_row)
        if is_insert and "RETURNING" not in pg_sql.upper():
            pg_sql_returning = pg_sql.rstrip(" ;") + " RETURNING id"
            try:
                cur.execute(pg_sql_returning, params)
                res = cur.fetchone()
                if res and "id" in res:
                    lastrowid = res["id"]
            except Exception:
                # Якщо у таблиці немає стовпця id або RETURNING не спрацював
                cur.execute(pg_sql, params)
        else:
            cur.execute(pg_sql, params)

        return PGCursorWrapper(cur, lastrowid=lastrowid)

    def close(self):
        self.raw.close()


def connect(db_target: str):
    """Якщо target починається з postgresql:// або postgres:// — підключаємося до PG.
    Інакше — відкриваємо локальний SQLite."""
    if db_target and (db_target.startswith("postgresql://") or db_target.startswith("postgres://")):
        if psycopg is None:
            raise RuntimeError("psycopg is required to connect to PostgreSQL. Run: pip install psycopg[binary]")
        # Neon вимагає SSL
        clean_url = db_target.replace("&channel_binding=require", "")
        raw = psycopg.connect(clean_url, autocommit=False)
        return PGConnectionWrapper(raw)

    conn = sqlite3.connect(db_target, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate_sqlite(conn)
    return conn


def migrate_sqlite(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number in range(version, len(MIGRATIONS)):
        conn.executescript(MIGRATIONS[number])
        conn.execute(f"PRAGMA user_version = {number + 1}")


@contextmanager
def transaction(conn):
    if isinstance(conn, PGConnectionWrapper):
        try:
            yield
            conn.raw.commit()
        except BaseException:
            conn.raw.rollback()
            raise
    else:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
