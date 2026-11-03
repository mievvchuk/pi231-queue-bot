from unittest.mock import AsyncMock

import pytest

from tests.test_attendance import lesson, own, answer
from queue_bot.services import attendance as a, queues
from queue_bot.services.errors import BotError
from queue_bot.services.lesson_notifier import deliver
from queue_bot.storage.database import connect


def begin(conn, sid):
    answer(conn, sid, 1, 'present')
    answer(conn, sid, 1, 'ready')
    answer(conn, sid, 2, 'present')


@pytest.mark.asyncio
async def test_student_duration_then_ten_minutes_releases_turn_without_passing_labs(conn, lesson):
    sid, clock = lesson
    begin(conn, sid)
    due = clock[0] + 15 * 60  # Fixture's student-supplied planned_minutes.
    assert own(conn, sid, 1)['turn_due'] == due
    assert own(conn, sid, 1)['deadline'] is None
    clock[0] = due - 1
    a.tick(conn)
    assert own(conn, sid, 1)['turn_due'] == due
    clock[0] = due
    a.tick(conn)
    assert own(conn, sid, 1)['turn_due'] == 0
    assert own(conn, sid, 1)['deadline'] is None
    bot = AsyncMock()
    await deliver(bot, conn, [])
    assert own(conn, sid, 1)['deadline'] == due + 600
    assert any('Ви вже завершили' in c.args[1] and '10 хвилин' in c.args[1] for c in bot.send_message.call_args_list)
    clock[0] = due + 599
    a.tick(conn)
    assert own(conn, sid, 1)['state'] == 'serving'
    clock[0] += 1
    a.tick(conn)
    assert own(conn, sid, 1)['state'] == 'skipped'
    assert own(conn, sid, 2)['state'] == 'called'
    assert queues.get_user_position(conn, 1, sid) == 4
    entry = queues.get_entry(conn, own(conn, sid, 1)['entry_id'])
    assert entry['selected_labs'] == '[1, 2]' and entry['passed_labs'] == '[]'
    assert entry['labs_passed'] == 0 and entry['status'] == 'active'
    assert not queues.get_history(conn, sid)


@pytest.mark.asyncio
async def test_extension_replaces_old_deadline_and_rejects_old_buttons(conn, lesson):
    sid, clock = lesson
    begin(conn, sid)
    clock[0] += 900
    a.tick(conn)
    await deliver(AsyncMock(), conn, [])
    old_deadline = own(conn, sid, 1)['deadline']
    token = own(conn, sid, 1)['token']
    clock[0] += 599
    answer(conn, sid, 1, 'extend', minutes=5)
    extended = own(conn, sid, 1)
    assert extended['deadline'] is None and extended['turn_due'] == clock[0] + 300
    with pytest.raises(BotError):
        a.respond(conn, 1, token, 'extend', minutes=5)
    clock[0] = old_deadline
    a.tick(conn)
    assert own(conn, sid, 1)['state'] == 'serving'
    clock[0] = extended['turn_due']
    a.tick(conn)
    await deliver(AsyncMock(), conn, [])
    assert own(conn, sid, 1)['deadline'] == clock[0] + 600


@pytest.mark.asyncio
async def test_partial_pass_survives_timeout_and_original_timer_is_not_reset(conn, lesson):
    sid, clock = lesson
    begin(conn, sid)
    entry = queues.get_entry(conn, own(conn, sid, 1)['entry_id'])
    due = own(conn, sid, 1)['turn_due']
    clock[0] += 120
    assert queues.record_own_passed_lab_numbers(conn, 1, sid, [1], entry_id=entry['id'], expected_revision=entry['entry_revision']) == [2]
    assert own(conn, sid, 1)['turn_due'] == due
    clock[0] = due
    a.tick(conn)
    await deliver(AsyncMock(), conn, [])
    clock[0] += 600
    a.tick(conn)
    current = queues.get_entry(conn, entry['id'])
    assert current['position'] == 4
    assert current['selected_labs'] == '[2]' and current['passed_labs'] == '[1]' and current['labs_passed'] == 1


@pytest.mark.asyncio
async def test_reminder_waits_for_delivery_and_does_not_repeat(conn, lesson):
    sid, clock = lesson
    begin(conn, sid)
    clock[0] += 900
    a.tick(conn)
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError('offline')
    await deliver(bot, conn, [])
    clock[0] += 601
    a.tick(conn)
    assert own(conn, sid, 1)['state'] == 'serving' and own(conn, sid, 1)['deadline'] is None
    bot.send_message.side_effect = None
    await deliver(bot, conn, [])
    assert own(conn, sid, 1)['deadline'] == clock[0] + 600
    bot.reset_mock()
    a.tick(conn)
    await deliver(bot, conn, [])
    assert not any('Ви вже завершили' in c.args[1] for c in bot.send_message.call_args_list)


@pytest.mark.asyncio
async def test_restart_preserves_turn_and_response_timers(conn, db_path, lesson):
    sid, clock = lesson
    begin(conn, sid)
    due = own(conn, sid, 1)['turn_due']
    reopened = connect(db_path)
    try:
        clock[0] = due
        a.tick(reopened)
        await deliver(AsyncMock(), reopened, [])
        deadline = own(reopened, sid, 1)['deadline']
        clock[0] = deadline
        a.tick(reopened)
        assert queues.get_user_position(reopened, 1, sid) == 4
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_full_completion_cancels_pending_reminder(conn, lesson):
    sid, clock = lesson
    begin(conn, sid)
    clock[0] += 900
    a.tick(conn)
    entry = queues.get_entry(conn, own(conn, sid, 1)['entry_id'])
    queues.record_own_passed_lab_numbers(conn, 1, sid, [1, 2], entry_id=entry['id'], expected_revision=entry['entry_revision'])
    a.tick(conn)
    bot = AsyncMock()
    await deliver(bot, conn, [])
    assert not any(c.args[0] == 1 and 'Ви вже завершили' in c.args[1] for c in bot.send_message.call_args_list)
    assert queues.get_user_position(conn, 1, sid) is None


@pytest.mark.asyncio
async def test_expired_answer_cannot_extend_or_mark_success_before_worker_tick(conn, lesson):
    sid, clock = lesson
    begin(conn, sid)
    clock[0] += 900
    a.tick(conn)
    await deliver(AsyncMock(), conn, [])
    clock[0] += 600
    with pytest.raises(BotError):
        answer(conn, sid, 1, 'extend', minutes=5)
    with pytest.raises(BotError):
        queues.finish_own_entry(conn, 1, sid, passed=True)
    a.tick(conn)
    assert queues.get_user_position(conn, 1, sid) == 4


def test_missing_estimate_requires_student_duration_not_a_guessed_default(conn, make_user, monkeypatch):
    monkeypatch.setattr(a, 'now_seconds', lambda: 10000)
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid)
    a.start(conn, 100, sid)
    answer(conn, sid, 1, 'present')
    with pytest.raises(BotError, match='скільки хвилин'):
        answer(conn, sid, 1, 'ready')
    answer(conn, sid, 1, 'ready', minutes=20)
    assert own(conn, sid, 1)['turn_due'] == 11200


@pytest.mark.parametrize('minutes', [0, -1, True, 1.5, 241, None])
def test_invalid_or_foreign_extension_does_not_change_timer(conn, lesson, minutes):
    sid, _ = lesson
    begin(conn, sid)
    before = dict(own(conn, sid, 1))
    with pytest.raises(BotError):
        answer(conn, sid, 1, 'extend', minutes=minutes)
    with pytest.raises(BotError):
        a.respond(conn, 2, before['token'], 'extend', minutes=5)
    assert dict(own(conn, sid, 1)) == before


@pytest.mark.asyncio
async def test_extension_during_delivery_cannot_be_overwritten_by_old_message(conn, lesson):
    sid, clock = lesson
    begin(conn, sid)
    clock[0] += 900
    a.tick(conn)
    bot = AsyncMock()
    async def extend_while_delivering(uid, text, **kwargs):
        if uid == 1 and 'Ви вже завершили' in text:
            answer(conn, sid, 1, 'extend', minutes=7)
    bot.send_message.side_effect = extend_while_delivering
    await deliver(bot, conn, [])
    assert own(conn, sid, 1)['deadline'] is None
    assert own(conn, sid, 1)['turn_due'] == clock[0] + 420


def test_stopping_pair_cancels_timers_without_reordering(conn, lesson):
    sid, clock = lesson
    begin(conn, sid)
    a.stop(conn, 100, sid)
    clock[0] += 2000
    a.tick(conn)
    assert queues.get_user_position(conn, 1, sid) == 1
    assert a.active_run(conn, sid) is None


def test_upgrade_of_already_serving_student_keeps_results_and_starts_from_known_estimate(tmp_path, monkeypatch):
    from queue_bot.storage import database as db
    from queue_bot.services import admins, users
    monkeypatch.setattr(a, 'now_seconds', lambda: 10000)
    migrations = db.MIGRATIONS
    monkeypatch.setattr(db, 'MIGRATIONS', migrations[:-1])
    path = str(tmp_path / 'legacy_turn.db')
    old = db.connect(path)
    admins.init_admins(old, [100])
    users.register_user(old, 1, 'Петренко Іван')
    sid = queues.create_session(old, 100, 1, 1)
    queues.join_queue(old, 1, sid, lab_numbers=[1,2], planned_minutes=15)
    entry = queues.get_entries(old, sid)[0]
    rid = old.execute('INSERT INTO lesson_runs(session_id,started_at,ends_at) VALUES (?,9000,20000)', (sid,)).lastrowid
    old.execute("INSERT INTO lesson_attendance(run_id,entry_id,user_id,state,token) VALUES (?,?,?,'serving','legacy-turn')", (rid,entry['id'],entry['user_id']))
    old.close()
    monkeypatch.setattr(db, 'MIGRATIONS', migrations)
    new = db.connect(path)
    try:
        a.tick(new)
        assert own(new, sid, 1)['turn_due'] == 10900
        assert queues.get_entries(new, sid)[0]['selected_labs'] == '[1, 2]'
        assert queues.get_user_position(new, 1, sid) == 1
    finally:
        new.close()
