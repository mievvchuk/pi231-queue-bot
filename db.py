import sqlite3
from contextlib import contextmanager

# Міграції: індекс у списку = версія схеми (PRAGMA user_version).
# Нові зміни додаються в кінець списку, наявні дані при цьому не втрачаються.
MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL UNIQUE,
        full_name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL UNIQUE,
        added_by INTEGER,
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
    -- Лише одна незавершена черга для пари предмет + викладач
    CREATE UNIQUE INDEX IF NOT EXISTS ux_one_unfinished_session
        ON queue_sessions (subject_id, teacher_id)
        WHERE status IN ('open', 'closed');

    -- user_id = NULL означає зарезервоване вільне місце (для нових студентів)
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
        admin_telegram_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
]


def connect(path: str) -> sqlite3.Connection:
    # isolation_level=None: транзакціями керуємо вручну (див. transaction)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number in range(version, len(MIGRATIONS)):
        conn.executescript(MIGRATIONS[number])
        conn.execute(f"PRAGMA user_version = {number + 1}")


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE блокує запис для інших з'єднань, тому два студенти
    не отримають однакову позицію."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
