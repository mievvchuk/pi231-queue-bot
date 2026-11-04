JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    period TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    subject_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('lab', 'attestation')),
    number INTEGER NOT NULL CHECK(number BETWEEN 1 AND 100),
    grade DOUBLE PRECISION NOT NULL CHECK(grade BETWEEN 0 AND 5),
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
    review_note TEXT NOT NULL DEFAULT '',
    reviewed_by BIGINT,
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, period, subject_key, kind, number)
);
CREATE INDEX IF NOT EXISTS ix_journal_period ON journal_records(period, user_id, status);
CREATE TABLE IF NOT EXISTS journal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER NOT NULL REFERENCES journal_records(id),
    actor_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS journal_dates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    number INTEGER NOT NULL CHECK(number BETWEEN 1 AND 100),
    due_date TEXT,
    updated_by BIGINT NOT NULL,
    UNIQUE(period, subject_key, number)
);
"""
