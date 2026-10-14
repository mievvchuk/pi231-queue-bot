import pytest

from services import queues, users
from services.errors import BotError
from tests.test_services import ADMIN, entry_id, filled, names, positions, session  # noqa: F401


def test_join_at_chosen_position_shifts_others(conn, make_user, filled):
    make_user(10, "Новий Студент")
    assert queues.join_queue(conn, 10, filled, position=2) == (2, 6)
    assert names(conn, filled)[:3] == ["Петренко Іван", "Новий Студент", "Коваль Андрій"]
    assert positions(conn, filled) == [1, 2, 3, 4, 5, 6]


def test_join_at_end_position_and_invalid_position(conn, make_user, filled):
    make_user(10, "Новий Студент")
    with pytest.raises(BotError):
        queues.join_queue(conn, 10, filled, position=0)
    with pytest.raises(BotError):
        queues.join_queue(conn, 10, filled, position=7)
    assert queues.join_queue(conn, 10, filled, position=6) == (6, 6)


def test_join_at_reserved_slot_does_not_shift(conn, make_user, filled):
    new_id = queues.finish_session(conn, ADMIN, filled, create_new=True,
                                   carry={entry_id(conn, filled, 3): 2})
    make_user(10, "Новий Студент")
    assert queues.join_queue(conn, 10, new_id, position=1) == (1, 2)
    assert names(conn, new_id) == ["Новий Студент", "Шевченко Максим"]


def test_student_marks_passed(conn, filled):
    assert queues.finish_own_entry(conn, 2, filled, passed=True) is None
    assert queues.get_user_position(conn, 2, filled) is None
    assert positions(conn, filled) == [1, 2, 3, 4]
    assert queues.get_history(conn, filled)[0]["status"] == "completed"


def test_student_marks_not_passed_moves_to_end(conn, filled):
    assert queues.finish_own_entry(conn, 1, filled, passed=False) == 5
    assert names(conn, filled)[-1] == "Петренко Іван"
    assert positions(conn, filled) == [1, 2, 3, 4, 5]


def test_student_can_finish_in_closed_queue_but_only_own_entry(conn, make_user, filled):
    queues.set_signup_open(conn, ADMIN, filled, False)
    queues.finish_own_entry(conn, 3, filled, passed=True)
    make_user(10, "Сторонній Студент")
    with pytest.raises(BotError):
        queues.finish_own_entry(conn, 10, filled, passed=True)


def test_student_can_open_queue(conn, make_user):
    make_user(1, "Петренко Іван")
    session_id = queues.create_queue(conn, 1, 3, 4, "Лаб 2")
    assert queues.get_session(conn, session_id)["status"] == "open"
    with pytest.raises(BotError):  # друга незавершена черга для того ж викладача
        queues.create_queue(conn, 1, 3, 4)


def test_unregistered_user_cannot_open_queue(conn):
    with pytest.raises(BotError):
        queues.create_queue(conn, 999, 3, 4)


def test_empty_queue_is_archived_so_next_one_can_be_opened(conn, make_user):
    make_user(1, "Петренко Іван")
    first = queues.create_queue(conn, 1, 3, 4)
    queues.join_queue(conn, 1, first)
    queues.finish_own_entry(conn, 1, first, passed=True)
    assert queues.get_session(conn, first)["status"] == "archived"
    assert queues.create_queue(conn, 1, 3, 4) != first
