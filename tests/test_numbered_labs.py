import json

import pytest

from queue_bot.services import queues
from queue_bot.services.errors import BotError


def test_exact_catalogue_and_summed_limits():
    from queue_bot.services.lab_catalog import labs_for_session, selection_limit
    for subject, count, expected in [(1, 7, 280), (2, 10, 150), (3, 7, 210), (4, 3, 60), (5, 11, 165)]:
        session = {'subject_id': subject, 'queue_type': 'lesson'}
        assert len(labs_for_session(session)) == count
        assert selection_limit(session, list(range(1, count + 1))) == expected
    assert selection_limit({'subject_id': 5}, [1, 2]) == 35
    assert selection_limit({'subject_id': 1, 'queue_type': 'consultation'}, [1, 2]) == 80
    for session in [{'subject_id': 6}, {'subject_id': 7}, {'subject_id': 6, 'queue_type': 'consultation'}]:
        assert labs_for_session(session) == ()


@pytest.mark.parametrize('numbers,minutes', [([1, 2], 36), ([12], 5), ([1, 1], 5), ([], 5), ([True], 5), ([1], 4)])
def test_invalid_selection_and_over_limit_do_not_join(conn, make_user, numbers, minutes):
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 5, 1)
    with pytest.raises(BotError):
        queues.join_queue(conn, 1, sid, lab_numbers=numbers, planned_minutes=minutes)
    assert queues.get_entries(conn, sid) == []


def test_numbered_partial_completion_keeps_place_and_recalculates_cap(conn, make_user):
    make_user(1, 'Петренко Іван')
    make_user(2, 'Коваль Андрій')
    sid = queues.create_session(conn, 100, 5, 1)
    queues.join_queue(conn, 1, sid, lab_numbers=[1, 2], planned_minutes=35)
    queues.join_queue(conn, 2, sid)
    entry = queues.get_entries(conn, sid)[0]
    assert entry['lab_count'] == 2
    remaining = queues.record_own_passed_lab_numbers(conn, 1, sid, [1], entry_id=entry['id'], expected_revision=entry['entry_revision'])
    assert remaining == [2]
    current = queues.get_entries(conn, sid)[0]
    assert current['position'] == 1 and current['planned_minutes'] == 10
    assert json.loads(current['selected_labs']) == [2]
    assert json.loads(current['passed_labs']) == [1] and current['labs_passed'] == 1
    with pytest.raises(BotError):
        queues.record_own_passed_lab_numbers(conn, 1, sid, [2], entry_id=entry['id'], expected_revision=entry['entry_revision'])
    assert queues.record_own_passed_lab_numbers(conn, 1, sid, [2], entry_id=current['id'], expected_revision=current['entry_revision']) == []
    assert queues.get_user_position(conn, 1, sid) is None
    assert queues.get_user_position(conn, 2, sid) == 1
    assert json.loads(queues.get_history(conn, sid)[0]['passed_labs']) == [1, 2]


def test_numbered_edit_and_time_edit_enforce_cap_and_ownership(conn, make_user):
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 5, 1)
    queues.join_queue(conn, 1, sid, lab_numbers=[1], planned_minutes=20)
    queues.set_own_lab_selection(conn, 1, sid, [2, 3], planned_minutes=20)
    assert queues.get_entries(conn, sid)[0]['position'] == 1
    with pytest.raises(BotError):
        queues.set_own_planned_minutes(conn, 1, sid, 21)
    with pytest.raises(BotError):
        queues.set_own_lab_selection(conn, 999, sid, [1], planned_minutes=5)
    assert json.loads(queues.get_entries(conn, sid)[0]['selected_labs']) == [2, 3]


def test_same_slot_rejoin_invalidates_old_numbered_confirmation(conn, make_user):
    make_user(1, 'Петренко Іван')
    make_user(2, 'Коваль Андрій')
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_numbers=[1], planned_minutes=5)
    queues.join_queue(conn, 2, sid)
    old = queues.get_entries(conn, sid)[0]
    queues.leave_queue(conn, 1, sid)
    queues.join_queue(conn, 1, sid, 1, lab_numbers=[1], planned_minutes=5)
    new = queues.get_entries(conn, sid)[0]
    assert old['id'] == new['id'] and old['entry_revision'] != new['entry_revision']
    with pytest.raises(BotError):
        queues.record_own_passed_lab_numbers(conn, 1, sid, [1], entry_id=old['id'], expected_revision=old['entry_revision'])


def test_history_transfer_and_empty_slots_preserve_identities(conn, make_user):
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_numbers=[1, 3], planned_minutes=70)
    old = queues.get_entries(conn, sid)[0]
    queues.record_own_passed_lab_numbers(conn, 1, sid, [1], entry_id=old['id'], expected_revision=old['entry_revision'])
    new_sid = queues.finish_session(conn, 100, sid, create_new=True, carry={old['id']: 2})
    slot, student = queues.get_entries(conn, new_sid)
    assert slot['selected_labs'] is None and json.loads(slot['passed_labs']) == []
    assert json.loads(student['selected_labs']) == [3] and student['planned_minutes'] == 40
    assert json.loads(student['passed_labs']) == [] and student['labs_passed'] == 0
    assert json.loads(queues.get_entry(conn, old['id'])['passed_labs']) == [1]


def test_scalar_legacy_apis_cannot_desynchronize_numbered_entry(conn, make_user):
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_numbers=[1, 2], planned_minutes=20)
    entry = queues.get_entries(conn, sid)[0]
    with pytest.raises(BotError):
        queues.set_own_lab_count(conn, 1, sid, 3)
    with pytest.raises(BotError):
        queues.record_own_passed_labs(conn, 1, sid, 1, entry_id=entry['id'], expected_count=2)
    assert json.loads(queues.get_entries(conn, sid)[0]['selected_labs']) == [1, 2]
