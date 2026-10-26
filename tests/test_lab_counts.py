import sqlite3

import pytest

from queue_bot.handlers.common import format_entry
from queue_bot.services import queues, restrictions, users
from queue_bot.services.errors import BotError
from queue_bot.storage import database


def test_quantity_is_per_entry_edit_keeps_position_and_swap_follows_student(conn, make_user):
    make_user(1, 'Петренко Іван')
    make_user(2, 'Коваль Андрій')
    sid = queues.create_session(conn, 100, 1, 1)
    other = queues.create_session(conn, 100, 2, 1)
    queues.join_queue(conn, 1, sid, lab_count=2)
    queues.join_queue(conn, 2, sid, lab_count=3)
    queues.join_queue(conn, 1, other, lab_count=1)
    queues.set_own_lab_count(conn, 1, sid, 4)
    a, b = queues.get_entries(conn, sid)
    assert a['lab_count'] == 4 and a['position'] == 1
    queues.swap_entries(conn, a['id'], b['id'])
    assert [(r['telegram_id'], r['lab_count']) for r in queues.get_entries(conn, sid)] == [(2, 3), (1, 4)]
    assert queues.get_entries(conn, other)[0]['lab_count'] == 1


@pytest.mark.parametrize('bad', [0, -1, 21, 1.5, True, '2'])
def test_invalid_quantity_cannot_create_or_modify_entry(conn, make_user, bad):
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 1, 1)
    with pytest.raises(BotError):
        queues.join_queue(conn, 1, sid, lab_count=bad)
    assert queues.get_entries(conn, sid) == []
    queues.join_queue(conn, 1, sid, lab_count=2)
    with pytest.raises(BotError):
        queues.set_own_lab_count(conn, 1, sid, bad)
    assert queues.get_entries(conn, sid)[0]['lab_count'] == 2


def test_leave_keeps_history_but_clears_count_from_vacant_slot(conn, make_user):
    for uid in (1, 2, 3):
        make_user(uid, ['Петренко Іван', 'Коваль Андрій', 'Мельник Роман'][uid - 1])
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_count=2)
    queues.join_queue(conn, 2, sid, lab_count=3)
    queues.leave_queue(conn, 1, sid)
    assert queues.get_entries(conn, sid)[0]['lab_count'] is None
    assert conn.execute("SELECT lab_count FROM queue_entries WHERE status = 'left'").fetchone()[0] == 2
    queues.join_queue(conn, 3, sid, 1)
    assert queues.get_entries(conn, sid)[0]['lab_count'] is None


def test_transfer_retry_and_archive_preserve_quantity(conn, make_user):
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_count=2)
    queues.finish_own_entry(conn, 1, sid, passed=False)
    entry = queues.get_entries(conn, sid)[0]
    new = queues.finish_session(conn, 100, sid, create_new=True, carry={entry['id']: 3})
    assert [r['lab_count'] for r in queues.get_entries(conn, new)] == [None, None, 2]
    assert queues.get_entry(conn, entry['id'])['lab_count'] == 2
    with pytest.raises(BotError):
        queues.set_own_lab_count(conn, 1, sid, 1)


def test_edit_is_own_only_closed_allowed_banned_rejected(conn, make_user):
    for uid in (1, 2):
        make_user(uid, ['Петренко Іван', 'Коваль Андрій'][uid - 1])
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_count=2)
    with pytest.raises(BotError):
        queues.set_own_lab_count(conn, 2, sid, 9)
    queues.set_signup_open(conn, 100, sid, False)
    queues.set_own_lab_count(conn, 1, sid, None)
    assert queues.get_entries(conn, sid)[0]['lab_count'] is None
    restrictions.set_restriction(conn, 100, users.get_user(conn, 1)['id'], days=1)
    with pytest.raises(BotError):
        queues.set_own_lab_count(conn, 1, sid, 3)


def test_old_sqlite_migrates_without_filling_unknown_counts(tmp_path):
    path = str(tmp_path / 'old.db')
    raw = sqlite3.connect(path, isolation_level=None)
    for migration in database.MIGRATIONS[:-1]:
        raw.executescript(migration)
    raw.execute(f'PRAGMA user_version = {len(database.MIGRATIONS) - 1}')
    raw.execute("INSERT INTO users (telegram_id, full_name) VALUES (1, 'Петренко Іван')")
    raw.execute('INSERT INTO queue_sessions (subject_id, teacher_id) VALUES (1, 1)')
    raw.execute('INSERT INTO queue_entries (session_id, user_id, position) VALUES (1, 1, 1)')
    raw.close()
    conn = database.connect(path)
    try:
        assert queues.get_entries(conn, 1)[0]['lab_count'] is None
        assert queues.get_entries(conn, 1)[0]['full_name'] == 'Петренко Іван'
    finally:
        conn.close()


@pytest.mark.parametrize('count,want', [(None, ''), (1, '1 лаб. · ≈7–10 хв'), (2, '2 лаб. · ≈15–20 хв'), (3, '3 лаб. · ≈20–30 хв')])
def test_queue_entry_displays_duration_only_for_known_quantity(count, want):
    row = {'position': 1, 'full_name': 'Іван <Тест>', 'telegram_id': 1, 'lab_count': count}
    text = format_entry(row)
    assert 'Іван &lt;Тест&gt;' in text
    if want:
        assert want in text
    else:
        assert 'лаб.' not in text and 'хв' not in text


def test_estimates_use_configured_minutes(monkeypatch):
    from queue_bot import config
    monkeypatch.setattr(config, 'LAB_MIN_MINUTES', 10)
    monkeypatch.setattr(config, 'LAB_MAX_MINUTES', 15)
    text = format_entry({'position': 1, 'full_name': 'Іван', 'telegram_id': 1, 'lab_count': 2})
    assert '2 лаб. · ≈20–30 хв' in text


def test_inverted_duration_settings_fail_preflight(monkeypatch):
    from queue_bot import config
    monkeypatch.setattr(config, 'LAB_MIN_MINUTES', 15)
    monkeypatch.setattr(config, 'LAB_MAX_MINUTES', 10)
    with pytest.raises(RuntimeError, match='LAB_MIN_MINUTES'):
        config.validate_runtime()
