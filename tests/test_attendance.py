from unittest.mock import AsyncMock

import pytest

from queue_bot.services import attendance as a, queues
from queue_bot.services.lesson_notifier import deliver
from queue_bot.services.errors import BotError
from queue_bot.storage.database import connect


@pytest.fixture
def lesson(conn, make_user, monkeypatch):
    clock = [10000]
    monkeypatch.setattr(a, 'now_seconds', lambda: clock[0])
    for uid, name in enumerate(('Петренко Іван', 'Коваль Андрій', 'Шевченко Олег', 'Мельник Олена'), 1):
        make_user(uid, name)
    sid = queues.create_session(conn, 100, 1, 1)
    for uid in (1, 2, 3, 4):
        queues.join_queue(conn, uid, sid, lab_numbers=[1, 2], planned_minutes=15)
    a.start(conn, 100, sid, minutes=80)
    return sid, clock


def own(conn, sid, uid):
    return a.view(conn, sid, uid)['own']


def answer(conn, sid, uid, action, **kwargs):
    return a.respond(conn, uid, own(conn, sid, uid)['token'], action, **kwargs)


@pytest.mark.asyncio
async def test_both_deadlines_are_ten_minutes_and_skip_never_reorders(conn, lesson):
    sid, clock = lesson
    before = [(e['id'], e['position']) for e in queues.get_entries(conn, sid)]
    bot = AsyncMock()
    await deliver(bot, conn, [])
    assert own(conn, sid, 1)['deadline'] == clock[0] + 600
    answer(conn, sid, 2, 'present')
    await deliver(bot, conn, [])
    assert own(conn, sid, 2)['state'] == 'called'
    assert own(conn, sid, 2)['deadline'] == clock[0] + 600
    clock[0] += 599
    a.tick(conn)
    assert own(conn, sid, 1)['state'] == 'pending'
    assert own(conn, sid, 2)['state'] == 'called'
    clock[0] += 1
    a.tick(conn)
    assert own(conn, sid, 1)['state'] == 'skipped'
    assert own(conn, sid, 2)['state'] == 'skipped'
    assert before == [(e['id'], e['position']) for e in queues.get_entries(conn, sid)]
    answer(conn, sid, 1, 'present')
    assert own(conn, sid, 1)['state'] == 'called'


@pytest.mark.parametrize('order', [(4, 3, 2), (2, 4, 3), (3, 4, 2)])
def test_presence_response_order_does_not_change_call_order(conn, lesson, order):
    sid, _ = lesson
    answer(conn, sid, 1, 'present')
    answer(conn, sid, 1, 'ready')
    for uid in order:
        answer(conn, sid, uid, 'present')
        assert own(conn, sid, uid)['state'] == 'present'
    assert a.view(conn, sid, 1)['ready_ids'] == [own(conn, sid, i)['entry_id'] for i in (1, 2, 3, 4)]
    queues.finish_own_entry(conn, 1, sid, passed=True)
    for uid in (2, 3, 4):
        a.tick(conn)
        assert own(conn, sid, uid)['state'] == 'called'
        answer(conn, sid, uid, 'ready')
        queues.finish_own_entry(conn, uid, sid, passed=True)
        a.tick(conn)


@pytest.mark.parametrize('action', ['late', 'absent', 'skip'])
def test_explicit_unavailability_releases_next_present_student(conn, lesson, action):
    sid, _ = lesson
    answer(conn, sid, 1, 'present')
    answer(conn, sid, 2, 'present')
    assert own(conn, sid, 2)['state'] == 'present'
    answer(conn, sid, 1, action, **({'minutes': 25} if action == 'late' else {}))
    assert own(conn, sid, 2)['state'] == 'called'


def test_no_answer_does_not_hold_back_confirmed_students(conn, lesson):
    sid, _ = lesson
    answer(conn, sid, 2, 'present')
    assert own(conn, sid, 1)['state'] == 'pending'
    assert own(conn, sid, 2)['state'] == 'called'


@pytest.mark.asyncio
async def test_not_ready_skips_immediately_is_visible_and_returns_with_priority(conn, lesson):
    from queue_bot.handlers.common import format_queue
    from queue_bot.services.lesson_notifier import keyboard
    sid, _ = lesson
    before = [(e['id'], e['position']) for e in queues.get_entries(conn, sid)]
    answer(conn, sid, 1, 'present')
    answer(conn, sid, 2, 'present')
    answer(conn, sid, 3, 'late', minutes=25)
    old_token = own(conn, sid, 1)['token']
    answer(conn, sid, 1, 'skip')
    assert own(conn, sid, 1)['state'] == 'not_ready'
    assert own(conn, sid, 2)['state'] == 'called'
    data = a.view(conn, sid, 1)
    assert data['ready_ids'] == [own(conn, sid, 2)['entry_id']]
    assert data['deferred_ids'][0] == own(conn, sid, 1)['entry_id']
    text = format_queue([], queues.get_session(conn, sid), queues.get_entries(conn, sid), 1, lesson=data)
    assert text.index('Коваль Андрій') < text.index('Поки пропускаємо') < text.index('Петренко Іван')
    assert 'Не готовий · тимчасово пропускаємо' in text
    assert 'Запізнюється' in text and 'очікуємо о ' in text
    assert any('Вже готовий' in b.text for row in keyboard(data['own']).inline_keyboard for b in row)
    with pytest.raises(BotError):
        a.respond(conn, 1, old_token, 'ready')
    await deliver(AsyncMock(), conn, [])
    a.tick(conn)
    assert own(conn, sid, 1)['state'] == 'not_ready'
    answer(conn, sid, 1, 'present')
    assert own(conn, sid, 2)['state'] == 'called'
    answer(conn, sid, 2, 'ready')
    queues.finish_own_entry(conn, 2, sid, passed=True)
    a.tick(conn)
    assert own(conn, sid, 1)['state'] == 'called'
    assert before[0] == (queues.get_entries(conn, sid)[0]['id'], queues.get_entries(conn, sid)[0]['position'])
    assert own(conn, sid, 3)['state'] == 'late'


@pytest.mark.asyncio
async def test_unanswered_late_reminder_does_not_hold_next_turn(conn, lesson):
    sid, clock = lesson
    answer(conn, sid, 1, 'late', minutes=5)
    answer(conn, sid, 2, 'present')
    answer(conn, sid, 2, 'ready')
    answer(conn, sid, 3, 'present')
    clock[0] += 300
    a.tick(conn)
    await deliver(AsyncMock(), conn, [])
    assert own(conn, sid, 1)['state'] == 'pending'
    queues.finish_own_entry(conn, 2, sid, passed=True)
    a.tick(conn)
    assert own(conn, sid, 3)['state'] == 'called'


@pytest.mark.asyncio
async def test_late_arrival_keeps_priority_and_never_interrupts_turn(conn, lesson):
    sid, clock = lesson
    answer(conn, sid, 1, 'late', minutes=5)
    answer(conn, sid, 2, 'present')
    await deliver(AsyncMock(), conn, [])
    answer(conn, sid, 2, 'ready')
    clock[0] += 300
    a.tick(conn)
    await deliver(AsyncMock(), conn, [])
    assert own(conn, sid, 1)['state'] == 'pending'
    assert own(conn, sid, 1)['deadline'] == clock[0] + 600
    answer(conn, sid, 1, 'present')
    assert own(conn, sid, 2)['state'] == 'serving'
    assert own(conn, sid, 1)['state'] == 'present'
    assert a.view(conn, sid, 1)['people_ahead'] == 1
    queues.finish_own_entry(conn, 2, sid, passed=False)
    a.tick(conn)
    assert own(conn, sid, 1)['state'] == 'called'
    assert queues.get_user_position(conn, 1, sid) == 1
    assert queues.get_user_position(conn, 2, sid) == 4


@pytest.mark.asyncio
async def test_partial_pass_keeps_turn_until_finish_then_remaining_moves_to_tail(conn, lesson):
    sid, _ = lesson
    entry = queues.get_entries(conn, sid)[0]
    with pytest.raises(BotError, match='дочекайтеся'):
        a.require_turn(conn, queues.get_entry(conn, entry['id']))
    answer(conn, sid, 1, 'present')
    await deliver(AsyncMock(), conn, [])
    answer(conn, sid, 1, 'ready')
    entry = queues.get_entries(conn, sid)[0]
    remaining = queues.record_own_passed_lab_numbers(conn, 1, sid, [1], entry_id=entry['id'], expected_revision=entry['entry_revision'])
    assert remaining == [2]
    assert own(conn, sid, 1)['state'] == 'serving'
    assert queues.get_user_position(conn, 1, sid) == 1
    queues.finish_own_entry(conn, 1, sid, passed=False)
    assert queues.get_user_position(conn, 1, sid) == 4
    entry = queues.get_entry(conn, entry['id'])
    assert entry['selected_labs'] == '[2]'
    assert entry['passed_labs'] == '[1]'
    assert entry['labs_passed'] == 1


@pytest.mark.asyncio
async def test_warning_exactly_two_people_ahead_and_no_repeated_delivery(conn, lesson):
    sid, _ = lesson
    bot = AsyncMock()
    for uid in (1, 2, 3):
        answer(conn, sid, uid, 'present')
    await deliver(bot, conn, [])
    warnings = [c for c in bot.send_message.call_args_list if 'залишилося двоє' in c.args[1]]
    assert len(warnings) == 1 and warnings[0].args[0] == 3
    a.tick(conn)
    await deliver(bot, conn, [])
    assert len([c for c in bot.send_message.call_args_list if 'залишилося двоє' in c.args[1]]) == 1


def test_stale_foreign_and_stopped_buttons_cannot_change_presence(conn, lesson):
    sid, _ = lesson
    token = own(conn, sid, 1)['token']
    with pytest.raises(BotError):
        a.respond(conn, 2, token, 'present')
    answer(conn, sid, 1, 'late', minutes=15)
    with pytest.raises(BotError):
        a.respond(conn, 1, token, 'present')
    token = own(conn, sid, 1)['token']
    a.stop(conn, 100, sid)
    with pytest.raises(BotError):
        a.respond(conn, 1, token, 'present')
    with pytest.raises(BotError):
        a.start(conn, 1, sid)


@pytest.mark.asyncio
async def test_restart_resumes_deadlines_and_next_lesson_resets_presence(conn, db_path, lesson):
    sid, clock = lesson
    await deliver(AsyncMock(), conn, [])
    reopened = connect(db_path)
    try:
        clock[0] += 600
        a.tick(reopened)
        assert own(reopened, sid, 1)['state'] == 'skipped'
        a.stop(reopened, 100, sid)
        a.start(reopened, 100, sid)
        assert own(reopened, sid, 1)['state'] == 'pending'
        assert queues.get_user_position(reopened, 1, sid) == 1
        clock[0] += 90 * 60
        a.tick(reopened)
        assert a.view(reopened, sid, 1) is None
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_delivery_failure_retries_without_starting_response_clock(conn, lesson):
    sid, clock = lesson
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError('offline')
    await deliver(bot, conn, [])
    assert own(conn, sid, 1)['deadline'] is None
    clock[0] += 61
    bot.send_message.side_effect = None
    await deliver(bot, conn, [])
    assert own(conn, sid, 1)['deadline'] == clock[0] + 600


def test_administrator_can_correct_presence_but_student_cannot(conn, lesson):
    sid, _ = lesson
    eid = own(conn, sid, 1)['entry_id']
    with pytest.raises(BotError):
        a.correct(conn, 2, eid, 'present')
    a.correct(conn, 100, eid, 'absent')
    assert own(conn, sid, 1)['state'] == 'absent'
    a.correct(conn, 100, eid, 'present')
    assert own(conn, sid, 1)['state'] == 'called'


def test_reused_vacant_slot_does_not_inherit_absence(conn, lesson, make_user):
    sid, _ = lesson
    answer(conn, sid, 1, 'absent')
    queues.leave_queue(conn, 1, sid)
    make_user(5, 'Новий Студент')
    queues.join_queue(conn, 5, sid, position=1, lab_numbers=[1], planned_minutes=10)
    a.tick(conn)
    assert own(conn, sid, 5)['state'] == 'pending'


def test_additive_upgrade_preserves_existing_queue_and_labs(tmp_path, monkeypatch):
    from queue_bot.storage import database as db
    from queue_bot.services import admins, users
    path = str(tmp_path / 'upgrade.db')
    all_migrations = db.MIGRATIONS
    monkeypatch.setattr(db, 'MIGRATIONS', all_migrations[:-1])
    legacy = db.connect(path)
    admins.init_admins(legacy, [100])
    users.register_user(legacy, 1, 'Петренко Іван')
    sid = queues.create_session(legacy, 100, 1, 1)
    queues.join_queue(legacy, 1, sid, lab_numbers=[1, 2], planned_minutes=15)
    before = dict(queues.get_entries(legacy, sid)[0])
    legacy.close()
    monkeypatch.setattr(db, 'MIGRATIONS', all_migrations)
    upgraded = db.connect(path)
    try:
        assert dict(queues.get_entries(upgraded, sid)[0]) == before
        assert a.active_run(upgraded, sid) is None
        assert queues.get_session(upgraded, sid)['status'] == 'open'
    finally:
        upgraded.close()


def test_restarting_lesson_during_turn_does_not_reuse_old_turn_or_button(conn, lesson):
    sid, _ = lesson
    answer(conn, sid, 1, 'present')
    token = own(conn, sid, 1)['token']
    answer(conn, sid, 1, 'ready')
    a.stop(conn, 100, sid)
    a.start(conn, 100, sid)
    with pytest.raises(BotError):
        a.respond(conn, 1, token, 'ready')
    answer(conn, sid, 1, 'present')
    answer(conn, sid, 1, 'ready')
    queues.finish_own_entry(conn, 1, sid, passed=False)
    assert queues.get_user_position(conn, 1, sid) == 4


@pytest.mark.parametrize('state', ['pending', 'present', 'late', 'absent', 'not_ready', 'skipped'])
def test_actual_out_of_order_results_do_not_take_over_current_turn(conn, lesson, state):
    sid, _ = lesson
    answer(conn, sid, 1, 'present')
    answer(conn, sid, 1, 'ready')
    if state == 'skipped':
        a.correct(conn, 100, own(conn, sid, 2)['entry_id'], 'skipped')
    elif state != 'pending':
        action = {'not_ready':'skip'}.get(state, state)
        answer(conn, sid, 2, action, **({'minutes':25} if state == 'late' else {}))
    assert own(conn, sid, 2)['state'] == state
    current = dict(own(conn, sid, 1))
    entry = queues.get_entries(conn, sid)[1]
    with pytest.raises(BotError):
        queues.record_own_passed_lab_numbers(conn, 3, sid, [1], entry_id=entry['id'], expected_revision=entry['entry_revision'])
    remaining = queues.record_own_passed_lab_numbers(conn, 2, sid, [1], entry_id=entry['id'], expected_revision=entry['entry_revision'])
    assert remaining == [2]
    assert queues.get_user_position(conn, 2, sid) == 2
    assert dict(own(conn, sid, 1)) == current
    with pytest.raises(BotError):
        queues.record_own_passed_lab_numbers(conn, 2, sid, [1], entry_id=entry['id'], expected_revision=entry['entry_revision'])
    queues.finish_own_entry(conn, 2, sid, passed=False)
    assert queues.get_user_position(conn, 2, sid) == 4
    assert dict(own(conn, sid, 1)) == current
    updated = queues.get_entry(conn, entry['id'])
    assert updated['passed_labs'] == '[1]' and updated['selected_labs'] == '[2]'
    assert queues.record_own_passed_lab_numbers(conn, 2, sid, [2], entry_id=entry['id'], expected_revision=updated['entry_revision']) == []
    assert queues.get_entry(conn, entry['id'])['status'] == 'completed'
    assert dict(own(conn, sid, 1)) == current
