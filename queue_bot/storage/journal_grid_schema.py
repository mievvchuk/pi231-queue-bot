"""Additive grid storage: keep original records and their audit untouched."""
GRID_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_cells (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    period TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    subject_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('lab', 'attestation')),
    number INTEGER NOT NULL CHECK(number BETWEEN 1 AND 100),
    part INTEGER NOT NULL DEFAULT 1 CHECK(part BETWEEN 1 AND 5),
    grade DOUBLE PRECISION NOT NULL CHECK(grade BETWEEN 0 AND 5),
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
    review_note TEXT NOT NULL DEFAULT '',
    reviewed_by BIGINT,
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    UNIQUE(user_id, period, subject_key, kind, number, part)
);
CREATE INDEX IF NOT EXISTS ix_journal_cells_period ON journal_cells(period, user_id, status);
CREATE TABLE IF NOT EXISTS journal_cell_events (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES journal_cells(id),
    actor_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

GRID_COPY = """
INSERT INTO journal_cells(id, user_id, period, subject_key, subject_name, kind, number, part,
    grade, note, status, review_note, reviewed_by, revision, updated_at)
SELECT 'legacy:' || CAST(id AS TEXT), user_id, period, subject_key, subject_name, kind, number, 1,
    grade, note, status, review_note, reviewed_by, revision, updated_at FROM journal_records
WHERE 1 = 1 ON CONFLICT DO NOTHING;
INSERT INTO journal_cell_events(id, record_id, actor_id, action, snapshot, created_at)
SELECT 'legacy:' || CAST(id AS TEXT), 'legacy:' || CAST(record_id AS TEXT), actor_id,
    action, snapshot, created_at FROM journal_events
WHERE EXISTS (SELECT 1 FROM journal_cells c WHERE c.id = 'legacy:' || CAST(journal_events.record_id AS TEXT))
ON CONFLICT DO NOTHING;
"""
