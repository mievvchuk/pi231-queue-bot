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
    """
    ALTER TABLE users ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0;
    """,
    """
    CREATE TABLE IF NOT EXISTS bot_updates (
        version TEXT PRIMARY KEY,
        description TEXT NOT NULL,
        sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS bot_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    ALTER TABLE queue_sessions ADD COLUMN queue_type TEXT NOT NULL DEFAULT 'lesson';
    DROP INDEX IF EXISTS ux_one_unfinished_session;
    CREATE UNIQUE INDEX IF NOT EXISTS ux_one_unfinished_session
        ON queue_sessions (subject_id, teacher_id, queue_type)
        WHERE status IN ('open', 'closed');
    """,
    """
    CREATE TABLE IF NOT EXISTS replacements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        group_name TEXT NOT NULL,
        lesson_number INTEGER NOT NULL,
        old_subject TEXT,
        new_subject TEXT NOT NULL,
        teacher TEXT,
        room TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_replacements_unique
        ON replacements (date, group_name, lesson_number, new_subject);
    """,
    """
    CREATE TABLE IF NOT EXISTS working_saturdays (
        date TEXT PRIMARY KEY,
        replaces_day TEXT NOT NULL,
        week_type TEXT,
        description TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    ALTER TABLE queue_entries ADD COLUMN ready_to_swap INTEGER NOT NULL DEFAULT 0;
    """,
    """
    ALTER TABLE queue_entries ADD COLUMN swap_wish TEXT DEFAULT NULL;
    """,
    """
    ALTER TABLE replacements ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0;
    """,
    """
    CREATE TABLE IF NOT EXISTS polls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        options_json TEXT NOT NULL,
        is_anonymous INTEGER NOT NULL DEFAULT 1,
        allows_multiple INTEGER NOT NULL DEFAULT 0,
        target TEXT NOT NULL DEFAULT 'private',
        status TEXT NOT NULL DEFAULT 'active',
        created_by BIGINT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        closes_at TEXT DEFAULT NULL,
        closed_at TEXT DEFAULT NULL
    );
    CREATE TABLE IF NOT EXISTS poll_instances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        poll_id INTEGER NOT NULL REFERENCES polls(id),
        chat_id BIGINT NOT NULL,
        message_id BIGINT NOT NULL,
        tg_poll_id TEXT NOT NULL,
        options_counts_json TEXT DEFAULT NULL
    );
    CREATE TABLE IF NOT EXISTS poll_votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        poll_id INTEGER NOT NULL REFERENCES polls(id),
        voter_telegram_id BIGINT NOT NULL,
        voter_name TEXT NOT NULL DEFAULT '',
        option_index INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
]

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS working_saturdays (
    date TEXT PRIMARY KEY,
    replaces_day TEXT NOT NULL,
    week_type TEXT,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS replacements (
    id SERIAL PRIMARY KEY,
    date TEXT NOT NULL,
    group_name TEXT NOT NULL,
    lesson_number INTEGER NOT NULL,
    old_subject TEXT,
    new_subject TEXT NOT NULL,
    teacher TEXT,
    room TEXT,
    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_replacements_unique
    ON replacements (date, group_name, lesson_number, new_subject);

CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS bot_updates (
    version TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
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
    queue_type TEXT NOT NULL DEFAULT 'lesson',
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'closed', 'archived')),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS queue_entries (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES queue_sessions(id),
    user_id INTEGER REFERENCES users(id),
    position INTEGER,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'completed', 'left', 'removed', 'transferred')),
    ready_to_swap BOOLEAN NOT NULL DEFAULT FALSE,
    swap_wish TEXT DEFAULT NULL,
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
CREATE TABLE IF NOT EXISTS polls (
    id SERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    options_json TEXT NOT NULL,
    is_anonymous BOOLEAN NOT NULL DEFAULT TRUE,
    allows_multiple BOOLEAN NOT NULL DEFAULT FALSE,
    target TEXT NOT NULL DEFAULT 'private',
    status TEXT NOT NULL DEFAULT 'active',
    created_by BIGINT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closes_at TEXT DEFAULT NULL,
    closed_at TIMESTAMP WITH TIME ZONE DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS poll_instances (
    id SERIAL PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES polls(id),
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    tg_poll_id TEXT NOT NULL,
    options_counts_json TEXT DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS poll_votes (
    id SERIAL PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES polls(id),
    voter_telegram_id BIGINT NOT NULL,
    voter_name TEXT NOT NULL DEFAULT '',
    option_index INTEGER NOT NULL,
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
    (автоматично трансформує '?' в '%s', підтримує lastrowid та автоперепідключення)."""

    def __init__(self, raw_conn, clean_url: str):
        self.raw = raw_conn
        self.clean_url = clean_url
        self._init_schema()

    def _connect_raw(self):
        import time
        last_err = None
        for attempt in range(5):
            try:
                return psycopg.connect(self.clean_url, autocommit=True)
            except Exception as e:
                last_err = e
                time.sleep(1.5)
        raise last_err

    def _ensure_connected(self):
        try:
            if self.raw.closed:
                self.raw = self._connect_raw()
            else:
                self.raw.execute("SELECT 1")
        except Exception:
            try:
                self.raw.close()
            except Exception:
                pass
            self.raw = self._connect_raw()

    def _init_schema(self):
        with self.raw.cursor() as cur:
            cur.execute(PG_SCHEMA)
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_blocked BOOLEAN NOT NULL DEFAULT FALSE;")
            cur.execute("ALTER TABLE queue_sessions ADD COLUMN IF NOT EXISTS queue_type TEXT NOT NULL DEFAULT 'lesson';")
            cur.execute("ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS ready_to_swap BOOLEAN NOT NULL DEFAULT FALSE;")
            cur.execute("ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS swap_wish TEXT DEFAULT NULL;")
            cur.execute("ALTER TABLE replacements ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE;")
            cur.execute("DROP INDEX IF EXISTS ux_one_unfinished_session;")
            cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_one_unfinished_session
                ON queue_sessions (subject_id, teacher_id, queue_type)
                WHERE status IN ('open', 'closed');""")
        self.raw.commit()

    def execute(self, sql: str, params: tuple = ()):
        self._ensure_connected()
        # Замінюємо ? на %s для psycopg
        pg_sql = sql.replace("?", "%s")
        is_insert = pg_sql.strip().upper().startswith("INSERT INTO")
        lastrowid = None

        cur = self.raw.cursor(row_factory=dict_row)
        tables_without_id = {"bot_updates", "bot_state", "working_saturdays"}
        table_match = re.search(r"insert\s+into\s+([a-zA-Z_0-9]+)", pg_sql, re.IGNORECASE)
        table_name = table_match.group(1).lower() if table_match else ""

        if is_insert and "RETURNING" not in pg_sql.upper() and table_name not in tables_without_id:
            try:
                pg_sql_returning = pg_sql.rstrip(" ;") + " RETURNING id"
                cur.execute(pg_sql_returning, params)
                res = cur.fetchone()
                if res and "id" in res:
                    lastrowid = res["id"]
            except Exception as e:
                err_str = str(e).lower()
                if 'column "id" does not exist' in err_str or "undefinedcolumn" in err_str:
                    tables_without_id.add(table_name)
                    cur.execute(pg_sql, params)
                else:
                    raise
        else:
            cur.execute(pg_sql, params)

        return PGCursorWrapper(cur, lastrowid=lastrowid)

    def close(self):
        try:
            self.raw.close()
        except Exception:
            pass


def connect(db_target: str):
    """Якщо target починається з postgresql:// або postgres:// — підключаємося до PG.
    Інакше — відкриваємо локальний SQLite."""
    if db_target and (db_target.startswith("postgresql://") or db_target.startswith("postgres://")):
        if psycopg is None:
            raise RuntimeError("psycopg is required to connect to PostgreSQL. Run: pip install psycopg[binary]")
        # Neon вимагає SSL
        clean_url = db_target.replace("&channel_binding=require", "")
        import time
        raw = None
        last_err = None
        for attempt in range(5):
            try:
                raw = psycopg.connect(clean_url, autocommit=True)
                break
            except Exception as e:
                last_err = e
                time.sleep(2)
        if raw is None and last_err:
            raise last_err
        return PGConnectionWrapper(raw, clean_url)

    conn = sqlite3.connect(db_target, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.create_function("lower", 1, lambda s: s.lower() if isinstance(s, str) else s)
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
        with conn.raw.transaction():
            yield
    else:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
