import pytest
import sqlite3

from queue_bot.services import admins, queues, users
from queue_bot.services.errors import BotError, PermissionDenied

ADMIN_ID = 100
NON_ADMIN_ID = 999


def test_alphabetical_sorting_returns_the_complete_roster(conn):
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

    result = users.list_users(conn)
    assert len(result) == len(names)
    assert [u['full_name'] for u in result] == sorted([users.validate_full_name(name) for name in names], key=str.casefold)


def test_ukrainian_special_letters_have_their_alphabetical_positions(conn):
    expected = ['Гнатюк Іван', 'Ґалаган Іван', 'Денисюк Іван', 'Емець Іван',
                'Євчук Андрій', 'Євчук Михайло', 'Жук Іван', 'Зінченко Іван',
                'Иваненко Іван', 'Іваненко Іван', 'Їжакевич Іван', 'Йовенко Іван',
                'Коваль Іван', 'Яремчук Іван']
    for name in reversed(expected):
        users.admin_create_placeholder(conn, ADMIN_ID, name)
    assert [u['full_name'] for u in users.list_users(conn)] == expected
    assert [u['full_name'] for u in users.search_users(conn, 'Іван')] == [name for name in expected if 'Іван' in name]
    conn.execute('UPDATE users SET is_blocked = TRUE')
    assert [u['full_name'] for u in users.list_blocked_users(conn)] == expected


def test_ukrainian_sort_is_case_and_apostrophe_independent():
    assert users.ukrainian_name_key("Лук'яненко Іван") == users.ukrainian_name_key('ЛУК’ЯНЕНКО ІВАН')
    assert users.ukrainian_name_key('Йовенко Іван') == users.ukrainian_name_key('И\u0306овенко Іван')


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
