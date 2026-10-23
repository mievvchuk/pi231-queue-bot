from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, Message

import db
from handlers.common import Guard
from services import admins, queues, users, restrictions
from services.errors import BotError, PermissionDenied


@pytest.fixture
def student(conn, make_user):
    make_user(1, 'Петренко Іван')
    return users.get_user(conn, 1)


def test_timed_ban_removes_entries_and_expires_without_restoring_places(conn, student):
    now = datetime.now(timezone.utc)
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid)
    until = restrictions.set_restriction(conn, 100, student['id'], days=2, now=now)
    assert datetime.fromisoformat(until) == now + timedelta(days=2)
    assert restrictions.restriction_message(conn, 1, now=now)
    assert queues.get_user_position(conn, 1, sid) is None
    assert queues.get_history(conn, sid)[0]['status'] == 'removed'
    assert restrictions.restriction_message(conn, 1, now=now + timedelta(days=2)) is None
    assert not users.get_user(conn, 1)['is_blocked']
    assert queues.get_user_position(conn, 1, sid) is None


def test_ban_survives_connection_restart(conn, db_path, student):
    now = datetime.now(timezone.utc)
    restrictions.set_restriction(conn, 100, student['id'], days=1, now=now)
    other = db.connect(db_path)
    try:
        assert restrictions.restriction_message(other, 1, now=now)
        assert restrictions.restriction_message(other, 1, now=now + timedelta(days=1)) is None
    finally:
        other.close()


def test_indefinite_block_and_manual_unban(conn, student):
    restrictions.set_restriction(conn, 100, student['id'])
    assert restrictions.restriction_message(conn, 1, now=datetime.now(timezone.utc) + timedelta(days=10000))
    restrictions.clear_restriction(conn, 100, student['id'])
    assert restrictions.restriction_message(conn, 1) is None


@pytest.mark.parametrize('days', [0, -1, 1.5, True, 999999999])
def test_invalid_duration_is_rejected(conn, student, days):
    with pytest.raises(BotError):
        restrictions.set_restriction(conn, 100, student['id'], days=days)


def test_only_admin_can_ban_and_admins_cannot_be_banned(conn, student, make_user):
    with pytest.raises(PermissionDenied):
        restrictions.set_restriction(conn, 1, student['id'], days=1)
    make_user(100, 'Адмін Власник')
    with pytest.raises(BotError):
        restrictions.set_restriction(conn, 100, users.get_user(conn, 100)['id'], days=1)


def test_block_removal_rolls_back_atomically(conn, student, monkeypatch):
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid)
    def fail(*args):
        raise RuntimeError('simulated failure')
    monkeypatch.setattr(queues, '_close_entry', fail)
    with pytest.raises(RuntimeError):
        restrictions.set_restriction(conn, 100, student['id'], days=1)
    assert not users.get_user(conn, 1)['is_blocked']
    assert queues.get_user_position(conn, 1, sid) == 1


def test_blocked_student_cannot_create_or_join_queue(conn, student):
    restrictions.set_restriction(conn, 100, student['id'])
    sid = queues.create_session(conn, 100, 1, 1)
    with pytest.raises(BotError):
        queues.create_queue(conn, 1, 2, 2)
    with pytest.raises(BotError):
        queues.join_queue(conn, 1, sid)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['message', 'callback'])
async def test_guard_stops_blocked_user_before_handler(conn, student, kind):
    restrictions.set_restriction(conn, 100, student['id'], days=1)
    event = MagicMock(spec=Message if kind == 'message' else CallbackQuery)
    event.from_user = MagicMock(id=1)
    if kind == 'message':
        event.chat = MagicMock(type='private')
    event.answer = AsyncMock()
    handler = AsyncMock()
    await Guard()(handler, event, {'conn': conn})
    handler.assert_not_awaited()
    event.answer.assert_awaited()


def test_existing_indefinite_toggle_removes_queues(conn, student):
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid)
    assert users.admin_toggle_block_user(conn, 100, student['id'])
    assert queues.get_user_position(conn, 1, sid) is None
    assert not users.admin_toggle_block_user(conn, 100, student['id'])


def test_banned_private_poll_answer_and_counts_are_ignored(conn, student):
    from services import polls
    pid = polls.create_poll(conn, 100, 'Питання', ['Так', 'Ні'])
    polls.register_poll_instance(conn, pid, 1, 1, 'poll-a')
    restrictions.set_restriction(conn, 100, student['id'], days=1)
    assert polls.record_poll_answer(conn, 'poll-a', 1, 'Петренко Іван', [0]) is None
    assert polls.record_poll_state_update(conn, 'poll-a', [1, 0]) is None
    assert polls.get_poll_results(conn, pid)['total_votes_cast'] == 0


def test_unblock_from_blocked_list_is_explicit_not_toggle(conn):
    # A stale unblock action must never re-block a user who has already expired.
    user = users.register_user(conn, 5, 'Мельник Олег')
    restrictions.clear_restriction(conn, 100, user['id'])
    restrictions.clear_restriction(conn, 100, user['id'])
    assert restrictions.restriction_message(conn, 5) is None


def test_sqlite_upgrade_preserves_existing_user_and_queue(tmp_path):
    import sqlite3
    path = str(tmp_path / 'older.db')
    old = sqlite3.connect(path, isolation_level=None)
    for migration in db.MIGRATIONS[:-1]:
        old.executescript(migration)
    old.execute(f'PRAGMA user_version = {len(db.MIGRATIONS) - 1}')
    old.execute("INSERT INTO users (telegram_id, full_name) VALUES (1, 'Петренко Іван')")
    old.execute('INSERT INTO queue_sessions (subject_id, teacher_id) VALUES (1, 1)')
    old.execute('INSERT INTO queue_entries (session_id, user_id, position) VALUES (1, 1, 1)')
    old.close()
    upgraded = db.connect(path)
    try:
        assert users.get_user(upgraded, 1)['banned_until'] is None
        assert queues.get_user_position(upgraded, 1, 1) == 1
        assert queues.get_session(upgraded, 1)['status'] == 'open'
    finally:
        upgraded.close()


def test_expired_ban_restores_notifications_without_student_message(conn, student, monkeypatch):
    from services.updates import get_active_user_ids
    now = datetime.now(timezone.utc)
    restrictions.set_restriction(conn, 100, student['id'], days=1, now=now)
    monkeypatch.setattr(restrictions, 'utc_now', lambda: now + timedelta(days=2))
    assert 1 in get_active_user_ids(conn)
    assert users.list_blocked_users(conn) == []


def test_admin_can_add_student_after_ban_expires(conn, student, monkeypatch):
    now = datetime.now(timezone.utc)
    restrictions.set_restriction(conn, 100, student['id'], days=1, now=now)
    monkeypatch.setattr(restrictions, 'utc_now', lambda: now + timedelta(days=2))
    sid = queues.create_session(conn, 100, 1, 1)
    assert queues.add_student_by_admin(conn, 100, sid, student['id']) == 1
