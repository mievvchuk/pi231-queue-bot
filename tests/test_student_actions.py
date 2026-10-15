import pytest

from services import queues, users
from services.errors import BotError
from tests.test_services import ADMIN, entry_id, filled, names, positions, session  # noqa: F401


def test_join_at_occupied_position_is_forbidden(conn, make_user, filled):
    make_user(10, "Новий Студент")
    with pytest.raises(BotError) as exc_info:
        queues.join_queue(conn, 10, filled, position=2)
    assert "зайняте" in str(exc_info.value)


def test_join_at_end_position_and_invalid_position(conn, make_user, filled):
    make_user(10, "Новий Студент")
    with pytest.raises(BotError):
        queues.join_queue(conn, 10, filled, position=0)
    with pytest.raises(BotError):
        queues.join_queue(conn, 10, filled, position=11)
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


def test_empty_queue_stays_open_so_students_can_still_join(conn, make_user):
    make_user(1, "Петренко Іван")
    first = queues.create_queue(conn, 1, 3, 4)
    queues.join_queue(conn, 1, first)
    queues.finish_own_entry(conn, 1, first, passed=True)
    assert queues.get_session(conn, first)["status"] == "open"


def test_first_10_positions_and_overflow_flow(conn, make_user):
    # 1. Порожня черга
    make_user(1, "Тест Перший")
    s_id = queues.create_queue(conn, 1, 3, 4)
    assert not queues.are_first_10_occupied(conn, s_id)

    # 2. Запис під номером 3
    queues.join_queue(conn, 1, s_id, position=3)
    assert queues.get_user_position(conn, 1, s_id) == 3

    # 3. Запис під номером 1 (вільне зарезервоване)
    make_user(2, "Тест Другий")
    queues.join_queue(conn, 2, s_id, position=1)
    assert queues.get_user_position(conn, 2, s_id) == 1

    # 4. Спроба записатися під вже зайнятим номером 3 повинна видати помилку
    make_user(3, "Тест Третій")
    with pytest.raises(BotError) as exc_info:
        queues.join_queue(conn, 3, s_id, position=3)
    assert "зайняте" in str(exc_info.value)

    # 5. Заповнюємо решту місць до 10
    # Зараз зайняті 1 і 3. Потрібно заповнити 2, 4, 5, 6, 7, 8, 9, 10
    free_positions = [2, 4, 5, 6, 7, 8, 9, 10]
    names_list = ["Четвертий", "П'ятий", "Шостий", "Сьомий", "Восьмий", "Дев'ятий", "Десятий", "Одинадцятий"]
    for idx, pos in enumerate(free_positions):
        uid = 100 + idx
        make_user(uid, f"Тест {names_list[idx]}")
        queues.join_queue(conn, uid, s_id, position=pos)

    assert queues.are_first_10_occupied(conn, s_id)

    # 6. Коли перші 10 зайняті, вказувати номер заборонено
    make_user(20, "Тест Двадцятий")
    with pytest.raises(BotError) as exc_info:
        queues.join_queue(conn, 20, s_id, position=5)
    assert "Перші 10 місць уже зайняті" in str(exc_info.value)

    # 7. Запис без вказання номера ставить у кінець (на 11-те місце)
    pos, total = queues.join_queue(conn, 20, s_id)
    assert pos == 11
    assert total == 11

    # Наступний стає на 12-те місце
    make_user(21, "Тест Двадцятьперший")
    pos, total = queues.join_queue(conn, 21, s_id)
    assert pos == 12
    assert total == 12


def test_create_duplicate_queue_validation(conn, make_user):
    make_user(1, "Тест Студент")
    s_id = queues.create_queue(conn, 1, 1, 1)
    assert s_id > 0

    existing = queues.find_active_session_by_subject_teacher(conn, 1, 1)
    assert existing is not None
    assert existing["id"] == s_id

    with pytest.raises(BotError) as exc_info:
        queues.create_queue(conn, 1, 1, 1)
    assert "вже створена" in str(exc_info.value)

