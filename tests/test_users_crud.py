import pytest
import sqlite3

from queue_bot.services import admins, queues, users
from queue_bot.services.errors import BotError, PermissionDenied

ADMIN_ID = 100
NON_ADMIN_ID = 999


def test_alphabetical_sorting_and_pagination(conn):
    names = [
        "Якименко Петро",
        "Бондар Дмитро",
        "антоненко Іван",
        "Коваль Андрій",
        "Мельник Олег",
        "богданенко Василь",
        "Гриценко Сергій",
        "денисюк Роман",
        "Шевченко Тарас",
    ]
    for n in names:
        users.admin_create_placeholder(conn, ADMIN_ID, n)

    p1_users, p1_cur, p1_total = users.list_users(conn, page=1, per_page=8)
    assert p1_cur == 1
    assert p1_total == 2
    assert len(p1_users) == 8

    p1_names = [u["full_name"] for u in p1_users]
    sorted_p1 = sorted(p1_names, key=lambda x: (x.lower(),))
    assert p1_names == sorted_p1

    p2_users, p2_cur, p2_total = users.list_users(conn, page=2, per_page=8)
    assert p2_cur == 2
    assert p2_total == 2
    assert len(p2_users) == 1
    assert p2_users[0]["full_name"] == "Якименко Петро"


def test_case_insensitive_search(conn):
    users.admin_create_placeholder(conn, ADMIN_ID, "Коваль Андрій")
    users.admin_create_placeholder(conn, ADMIN_ID, "Коваленко Іван")
    users.admin_create_placeholder(conn, ADMIN_ID, "Мельник Андрій")

    res1 = users.search_users(conn, "ковал")
    assert len(res1) == 2
    assert {u["full_name"] for u in res1} == {"Коваль Андрій", "Коваленко Іван"}
    # Алфавітний порядок: "Коваленко" раніше ніж "Коваль" ('е' < 'ь')
    assert res1[0]["full_name"] == "Коваленко Іван"
    assert res1[1]["full_name"] == "Коваль Андрій"

    res2 = users.search_users(conn, "АНДРІЙ")
    assert len(res2) == 2
    assert {u["full_name"] for u in res2} == {"Коваль Андрій", "Мельник Андрій"}

    assert users.search_users(conn, "   ") == []


def test_create_placeholder_and_claim(conn):
    placeholder = users.admin_create_placeholder(conn, ADMIN_ID, "Сидоренко Василь")
    assert placeholder["full_name"] == "Сидоренко Василь"
    assert placeholder["telegram_id"] < 0

    claimed = users.register_user(conn, 555777, "сидоренко василь")
    assert claimed["id"] == placeholder["id"]
    assert claimed["telegram_id"] == 555777


def test_duplicate_name_prohibited(conn):
    users.admin_create_placeholder(conn, ADMIN_ID, "Петренко Іван")

    with pytest.raises(BotError) as exc:
        users.admin_create_placeholder(conn, ADMIN_ID, "Петренко Іван")
    assert "уже існує" in str(exc.value)

    with pytest.raises(BotError) as exc:
        users.admin_create_placeholder(conn, ADMIN_ID, "петренко іван")
    assert "уже існує" in str(exc.value)


def test_edit_user_full_name(conn):
    user = users.admin_create_placeholder(conn, ADMIN_ID, "Данилюк Ігор")
    user_id = user["id"]
    original_tg_id = user["telegram_id"]

    updated = users.admin_update_full_name(conn, ADMIN_ID, user_id, "Данилюк Богдан")
    assert updated["full_name"] == "Данилюк Богдан"
    assert updated["telegram_id"] == original_tg_id

    users.admin_create_placeholder(conn, ADMIN_ID, "Кравчук Сергій")
    with pytest.raises(BotError) as exc:
        users.admin_update_full_name(conn, ADMIN_ID, user_id, "Кравчук Сергій")
    assert "уже існує" in str(exc.value)


def test_crud_prohibited_for_non_admin(conn):
    with pytest.raises(PermissionDenied):
        users.admin_create_placeholder(conn, NON_ADMIN_ID, "Іванов Петро")

    user = users.admin_create_placeholder(conn, ADMIN_ID, "Іванов Петро")

    with pytest.raises(PermissionDenied):
        users.admin_update_full_name(conn, NON_ADMIN_ID, user["id"], "Іванов Сергій")

    with pytest.raises(PermissionDenied):
        users.admin_toggle_block_user(conn, NON_ADMIN_ID, user["id"])

    with pytest.raises(PermissionDenied):
        users.admin_delete_user(conn, NON_ADMIN_ID, user["id"])


def test_delete_admin_prohibited(conn):
    user = users.register_user(conn, 222333, "Адміненко Петро")
    admins.add_admin(conn, ADMIN_ID, 222333)

    with pytest.raises(BotError) as exc:
        users.admin_delete_user(conn, ADMIN_ID, user["id"])
    assert "адміністратора" in str(exc.value)


def test_delete_user_with_queue_entries_prohibited(conn):
    users.register_user(conn, ADMIN_ID, "Адмін Адміністратор")
    user = users.admin_create_placeholder(conn, ADMIN_ID, "Черговий Студент")
    session_id = queues.create_queue(conn, ADMIN_ID, subject_id=1, teacher_id=1)
    queues.add_student_by_admin(conn, ADMIN_ID, session_id, user["id"])

    with pytest.raises(BotError) as exc:
        users.admin_delete_user(conn, ADMIN_ID, user["id"])
    assert "активні або історичні записи" in str(exc.value)


def test_delete_unused_placeholder(conn):
    user = users.admin_create_placeholder(conn, ADMIN_ID, "Тимчасовий Запис")
    user_id = user["id"]

    users.admin_delete_user(conn, ADMIN_ID, user_id)
    assert users.get_user_by_id(conn, user_id) is None
