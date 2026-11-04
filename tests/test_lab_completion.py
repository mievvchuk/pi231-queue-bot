import pytest

from queue_bot.services import queues, restrictions, users
from queue_bot.services.errors import BotError


def complete(conn, uid, sid, count, entry):
    return queues.record_own_passed_labs(conn, uid, sid, count,
                                        entry_id=entry['id'], expected_count=entry['lab_count'])


def test_partial_completion_keeps_position_and_all_finishes_with_actual_count(conn, make_user):
    make_user(1, 'Петренко Іван')
    make_user(2, 'Коваль Андрій')
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_count=3, planned_minutes=20)
    queues.join_queue(conn, 2, sid)
    old = queues.get_entries(conn, sid)[0]
    assert complete(conn, 1, sid, 1, old) == 2
    current = queues.get_entries(conn, sid)[0]
    assert current['position'] == 1 and current['lab_count'] == 2
    assert current['labs_passed'] == 1 and current['planned_minutes'] == 20
    assert queues.get_user_position(conn, 2, sid) == 2
    with pytest.raises(BotError):  # An old confirmation cannot count the same pass twice.
        complete(conn, 1, sid, 1, old)
    assert complete(conn, 1, sid, 2, current) == 0
    assert queues.get_user_position(conn, 1, sid) is None
    assert queues.get_user_position(conn, 2, sid) == 1
    assert queues.get_history(conn, sid)[0]['labs_passed'] == 3


@pytest.mark.parametrize('count', [0, -1, 4, 1.5, True, None])
def test_invalid_pass_count_cannot_change_remaining_or_order(conn, make_user, count):
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_count=3)
    entry = queues.get_entries(conn, sid)[0]
    with pytest.raises(BotError):
        complete(conn, 1, sid, count, entry)
    current = queues.get_entries(conn, sid)[0]
    assert current['lab_count'] == 3 and current['labs_passed'] == 0 and current['position'] == 1


def test_old_confirmation_cannot_finish_a_new_entry_and_blocked_user_cannot_finish(conn, make_user):
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_count=2)
    old = queues.get_entries(conn, sid)[0]
    queues.leave_queue(conn, 1, sid)
    queues.join_queue(conn, 1, sid, lab_count=2)
    with pytest.raises(BotError):
        complete(conn, 1, sid, 2, old)
    current = queues.get_entries(conn, sid)[0]
    with pytest.raises(BotError):
        complete(conn, 999, sid, 2, current)
    restrictions.set_restriction(conn, 100, users.get_user(conn, 1)['id'], days=1)
    with pytest.raises(BotError):
        complete(conn, 1, sid, 2, current)


@pytest.mark.parametrize('actual', [None, 1, 4])
def test_unknown_quantity_can_finish_without_guessing_remaining(conn, make_user, actual):
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid)
    assert complete(conn, 1, sid, actual, queues.get_entries(conn, sid)[0]) == 0
    assert queues.get_history(conn, sid)[0]['labs_passed'] == (actual or 0)


def test_leaving_preserves_completed_labs_in_history_and_clears_vacant_slot(conn, make_user):
    for uid, name in [(1, 'Петренко Іван'), (2, 'Коваль Андрій')]:
        make_user(uid, name)
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_count=3)
    queues.join_queue(conn, 2, sid)
    complete(conn, 1, sid, 1, queues.get_entries(conn, sid)[0])
    queues.leave_queue(conn, 1, sid)
    assert queues.get_history(conn, sid)[0]['labs_passed'] == 1
    assert queues.get_entries(conn, sid)[0]['labs_passed'] == 0
