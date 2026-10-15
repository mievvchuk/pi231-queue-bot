import threading

import pytest

import db
from services import admins, queues, users
from services.errors import BotError, PermissionDenied

ADMIN = 100


def names(conn, session_id):
    return [row["full_name"] for row in queues.get_entries(conn, session_id)]


def positions(conn, session_id):
    return [row["position"] for row in queues.get_entries(conn, session_id)]


@pytest.fixture
def session(conn):
    return queues.create_session(conn, ADMIN, 3, 4, "Лаб 1")


@pytest.fixture
def filled(conn, make_user, session):
    """Черга з 5 студентів: Петренко, Коваль, Шевченко, Бондар, Мельник."""
    for number, name in enumerate(
        ["Петренко Іван", "Коваль Андрій", "Шевченко Максим", "Бондар Дмитро", "Мельник Олег"], 1
    ):
        make_user(number, name)
        queues.join_queue(conn, number, session)
    return session


def entry_id(conn, session_id, position):
    return queues.get_entries(conn, session_id)[position - 1]["id"]


# 1. Реєстрація
def test_register_user(conn):
    user = users.register_user(conn, 1, "петренко іван")
    assert user["full_name"] == "Петренко Іван"
    assert users.get_user(conn, 1) is not None
    with pytest.raises(BotError):
        users.register_user(conn, 1, "Петренко Іван")


@pytest.mark.parametrize("bad", ["Іван", "Іван123 Петренко", "А Б", "Один Два Три"])
def test_register_rejects_bad_names(conn, bad):
    with pytest.raises(BotError):
        users.register_user(conn, 1, bad)


# 2-3. Запис, захист від дубля
def test_join_open_queue(conn, make_user, session):
    make_user(1, "Петренко Іван")
    assert queues.join_queue(conn, 1, session) == (1, 1)
    make_user(2, "Коваль Андрій")
    assert queues.join_queue(conn, 2, session) == (2, 2)


def test_duplicate_join_rejected(conn, make_user, session):
    make_user(1, "Петренко Іван")
    queues.join_queue(conn, 1, session)
    with pytest.raises(BotError):
        queues.join_queue(conn, 1, session)
    assert len(queues.get_entries(conn, session)) == 1


def test_same_user_in_different_queues(conn, make_user, session):
    make_user(1, "Петренко Іван")
    other = queues.create_session(conn, ADMIN, 1, 1)
    queues.join_queue(conn, 1, session)
    queues.join_queue(conn, 1, other)
    assert len(queues.list_user_sessions(conn, 1)) == 2


def test_rejoin_after_leaving(conn, make_user, session):
    make_user(1, "Петренко Іван")
    make_user(2, "Коваль Андрій")
    queues.join_queue(conn, 2, session)
    queues.join_queue(conn, 1, session)
    queues.leave_queue(conn, 1, session)
    assert queues.join_queue(conn, 1, session) == (2, 2)


# 4. Закрита черга
def test_closed_queue_rejects_join(conn, make_user, session):
    make_user(1, "Петренко Іван")
    queues.set_signup_open(conn, ADMIN, session, False)
    with pytest.raises(BotError, match="закрито"):
        queues.join_queue(conn, 1, session)


def test_one_unfinished_queue_per_teacher(conn, session):
    with pytest.raises(BotError):
        queues.create_session(conn, ADMIN, 3, 4)


# 5-7. Переміщення
def test_move_up(conn, filled):
    queues.move_up(conn, ADMIN, entry_id(conn, filled, 3))
    assert names(conn, filled)[1] == "Шевченко Максим"
    assert positions(conn, filled) == [1, 2, 3, 4, 5]


def test_move_down(conn, filled):
    queues.move_down(conn, ADMIN, entry_id(conn, filled, 3))
    assert names(conn, filled)[3] == "Шевченко Максим"
    assert positions(conn, filled) == [1, 2, 3, 4, 5]


def test_move_to_position(conn, filled):
    queues.move_entry(conn, ADMIN, entry_id(conn, filled, 3), 5)
    assert names(conn, filled) == [
        "Петренко Іван", "Коваль Андрій", "Бондар Дмитро", "Мельник Олег", "Шевченко Максим"
    ]
    with pytest.raises(BotError):
        queues.move_entry(conn, ADMIN, entry_id(conn, filled, 1), 101)


# 8. Видалення
def test_remove_entry(conn, filled):
    queues.remove_entry(conn, ADMIN, entry_id(conn, filled, 2))
    assert len(names(conn, filled)) == 4
    assert positions(conn, filled) == [1, 2, 3, 4]


def test_leave_queue_renumbers(conn, filled):
    queues.leave_queue(conn, 1, filled)
    assert positions(conn, filled) == [1, 2, 3, 4]
    assert queues.get_user_position(conn, 2, filled) == 1


# 9. Здача
def test_mark_done_keeps_history(conn, filled):
    queues.mark_done(conn, ADMIN, entry_id(conn, filled, 1))
    assert positions(conn, filled) == [1, 2, 3, 4]
    history = queues.get_history(conn, filled)
    assert history[0]["status"] == "completed"
    assert history[0]["full_name"] == "Петренко Іван"


# 10-12. Завершення заняття
def test_finish_and_archive(conn, filled):
    assert queues.finish_session(conn, ADMIN, filled) is None
    assert queues.get_session(conn, filled)["status"] == "archived"
    assert queues.list_sessions(conn, ("open", "closed")) == []


def test_new_empty_queue(conn, filled):
    new_id = queues.finish_session(conn, ADMIN, filled, create_new=True)
    assert new_id != filled
    assert queues.get_entries(conn, new_id) == []
    assert queues.get_session(conn, new_id)["status"] == "open"


def test_carry_over_with_chosen_positions(conn, filled):
    shevchenko = entry_id(conn, filled, 3)
    melnyk = entry_id(conn, filled, 5)
    new_id = queues.finish_session(
        conn, ADMIN, filled, create_new=True, carry={shevchenko: 2, melnyk: 4}
    )
    rows = queues.get_entries(conn, new_id)
    assert [r["full_name"] for r in rows] == [None, "Шевченко Максим", None, "Мельник Олег"]
    assert positions(conn, new_id) == [1, 2, 3, 4]
    # Нові студенти займають вільні місця 1 і 3
    for number, name in [(10, "Новий Студент"), (11, "Другий Студент")]:
        users.register_user(conn, number, name)
    assert queues.join_queue(conn, 10, new_id) == (1, 4)
    assert queues.join_queue(conn, 11, new_id) == (3, 4)
    # Перенесеним не потрібна повторна реєстрація; наступний іде в кінець
    users.register_user(conn, 12, "Третій Студент")
    assert queues.join_queue(conn, 12, new_id) == (5, 5)


def test_finish_is_atomic(conn, filled):
    first = entry_id(conn, filled, 1)
    with pytest.raises(BotError):
        # Дві однакові позиції -> помилка, стара черга не архівується
        queues.finish_session(conn, ADMIN, filled, create_new=True,
                              carry={first: 1, entry_id(conn, filled, 2): 1})
    assert queues.get_session(conn, filled)["status"] == "open"
    assert len(queues.get_entries(conn, filled)) == 5


# 13-15. Адміни
def test_add_admin(conn):
    admins.add_admin(conn, ADMIN, 200)
    assert admins.is_admin(conn, 200)
    admins.add_admin(conn, 200, 300)  # усі рівноправні
    with pytest.raises(BotError):
        admins.add_admin(conn, ADMIN, 200)


def test_admin_operations_are_protected(conn, filled):
    with pytest.raises(PermissionDenied):
        queues.create_session(conn, 1, 1, 1)
    with pytest.raises(PermissionDenied):
        queues.move_entry(conn, 1, entry_id(conn, filled, 1), 2)
    with pytest.raises(PermissionDenied):
        queues.mark_done(conn, 1, entry_id(conn, filled, 1))
    with pytest.raises(PermissionDenied):
        queues.finish_session(conn, 1, filled)
    with pytest.raises(PermissionDenied):
        admins.add_admin(conn, 1, 2)
    assert positions(conn, filled) == [1, 2, 3, 4, 5]


def test_cannot_remove_last_admin(conn):
    with pytest.raises(BotError):
        admins.remove_admin(conn, ADMIN, ADMIN)
    admins.add_admin(conn, ADMIN, 200)
    admins.remove_admin(conn, 200, ADMIN)
    assert [a["telegram_id"] for a in admins.list_admins(conn)] == [200]


def test_initial_admins_only_when_empty(conn):
    admins.init_admins(conn, [555])
    assert not admins.is_admin(conn, 555)


# 16. Повторні й одночасні запити
def test_concurrent_joins_get_unique_positions(db_path, conn, make_user, session):
    for number in range(1, 9):
        make_user(number, f"Студент{'абвгдежз'[number - 1]} Тест")
    results, errors = [], []

    def join(telegram_id):
        own = db.connect(db_path)
        try:
            results.append(queues.join_queue(own, telegram_id, session)[0])
        except Exception as error:  # noqa: BLE001
            errors.append(error)
        finally:
            own.close()

    threads = [threading.Thread(target=join, args=(n,)) for n in range(1, 9)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert sorted(results) == list(range(1, 9))


def test_concurrent_double_click_joins_once(db_path, conn, make_user, session):
    make_user(1, "Петренко Іван")
    outcomes = []

    def join():
        own = db.connect(db_path)
        try:
            queues.join_queue(own, 1, session)
            outcomes.append("ok")
        except BotError:
            outcomes.append("rejected")
        finally:
            own.close()

    threads = [threading.Thread(target=join) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outcomes.count("ok") == 1
    assert len(queues.get_entries(conn, session)) == 1


def test_migration_keeps_data(db_path):
    first = db.connect(db_path)
    users.register_user(first, 1, "Петренко Іван")
    first.close()
    second = db.connect(db_path)  # повторне відкриття не чіпає дані
    assert users.get_user(second, 1) is not None
