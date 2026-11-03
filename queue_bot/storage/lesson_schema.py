LESSON_SCHEMA = """
CREATE TABLE IF NOT EXISTS lesson_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES queue_sessions(id),
    active INTEGER NOT NULL DEFAULT 1,
    started_at BIGINT NOT NULL,
    ends_at BIGINT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_active_lesson_run ON lesson_runs(session_id) WHERE active = 1;
CREATE TABLE IF NOT EXISTS lesson_attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES lesson_runs(id),
    entry_id INTEGER NOT NULL REFERENCES queue_entries(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    state TEXT NOT NULL DEFAULT 'pending',
    token TEXT NOT NULL UNIQUE,
    revision INTEGER NOT NULL DEFAULT 0,
    deadline BIGINT,
    warned INTEGER NOT NULL DEFAULT 0,
    UNIQUE(run_id, entry_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_lesson_turn ON lesson_attendance(run_id)
    WHERE state IN ('called', 'serving');
CREATE TABLE IF NOT EXISTS lesson_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_id INTEGER NOT NULL REFERENCES lesson_attendance(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    kind TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    retry_at BIGINT NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    UNIQUE(attendance_id, revision, kind)
);
"""
