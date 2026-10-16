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


def test_coursework_grouping_menu(conn, make_user):
    from handlers.common import get_queues_reply_menu, get_coursework_queues_menu
    from subjects import load_subjects
    import config

    subjects = load_subjects(config.SUBJECTS_PATH)
    make_user(100, "Адмін Тестовий")

    # Створюємо звичайну чергу та декілька черг на курсову
    s_reg = queues.create_session(conn, 100, 1, 1)  # Програмування в Інтернет
    s_cw1 = queues.create_session(conn, 100, 7, 6)  # Курсова - Атаман
    s_cw2 = queues.create_session(conn, 100, 7, 1)  # Курсова - Когут

    sessions = queues.list_sessions(conn, ("open", "closed"))

    # Згруповане меню: містить звичайну чергу та 1 кнопку групи курсової
    menu_grouped = get_queues_reply_menu(sessions, subjects, group_coursework=True)
    button_texts = [btn.text for row in menu_grouped.keyboard for btn in row]
    assert any("Програмування в Інтернет" in t for t in button_texts)
    assert any("📑 Курсова робота з КПЗ" in t for t in button_texts)
    # Окремі кнопки вчителів курсової не повинні виводитись у згрупованому списку
    assert not any("Курсова робота з КПЗ — Атаман" in t for t in button_texts)

    # Незгруповане меню (для "Мої черги")
    menu_ungrouped = get_queues_reply_menu(sessions, subjects, group_coursework=False)
    button_texts_ungrouped = [btn.text for row in menu_ungrouped.keyboard for btn in row]
    assert any("Курсова робота з КПЗ — Атаман" in t for t in button_texts_ungrouped)

    # Меню всередині курсової: містить саме черги викладачів курсової
    cw_menu = get_coursework_queues_menu(sessions, subjects, "Курсова робота з КПЗ")
    cw_button_texts = [btn.text for row in cw_menu.keyboard for btn in row]
    assert any("Курсова робота з КПЗ — Атаман" in t for t in cw_button_texts)
    assert any("Курсова робота з КПЗ — Когут" in t for t in cw_button_texts)


def test_consultation_queue_support(conn, make_user):
    from handlers.common import find_session_by_label, queue_label
    from subjects import load_subjects
    import config

    subjects = load_subjects(config.SUBJECTS_PATH)
    make_user(100, "Адмін Тестовий")

    # Створюємо чергу на пару та чергу на консультацію для одного й того ж викладача
    s_lesson = queues.create_session(conn, 100, 1, 1, queue_type="lesson")
    s_consult = queues.create_session(conn, 100, 1, 1, queue_type="consultation")

    assert s_lesson != s_consult

    # Перевірка пошуку за типом
    lesson_session = queues.find_active_session_by_subject_teacher(conn, 1, 1, queue_type="lesson")
    consult_session = queues.find_active_session_by_subject_teacher(conn, 1, 1, queue_type="consultation")

    assert lesson_session["id"] == s_lesson
    assert consult_session["id"] == s_consult

    # Повторне створення такого ж типу викликає помилку
    with pytest.raises(BotError) as exc_info:
        queues.create_session(conn, 100, 1, 1, queue_type="consultation")
    assert "консультацію" in str(exc_info.value)

    # Перевірка міток та розпізнавання у find_session_by_label
    lbl_lesson = queue_label(subjects, lesson_session)
    lbl_consult = queue_label(subjects, consult_session)

    assert "Консультація" not in lbl_lesson
    assert "Консультація" in lbl_consult

    assert find_session_by_label(conn, subjects, lbl_lesson)["id"] == s_lesson
    assert find_session_by_label(conn, subjects, lbl_consult)["id"] == s_consult


def test_ready_to_swap_toggle_and_format(conn, filled):
    from handlers.common import format_entry, get_queue_actions_reply_menu

    # Початковий стан — False
    assert not queues.is_ready_to_swap(conn, 1, filled)

    # Перемикання в True
    new_st = queues.toggle_ready_to_swap(conn, 1, filled)
    assert new_st is True
    assert queues.is_ready_to_swap(conn, 1, filled) is True

    # Перевірка get_entries та format_entry
    entries = queues.get_entries(conn, filled)
    e1 = next(e for e in entries if e["telegram_id"] == 1)
    assert bool(e1["ready_to_swap"]) is True
    formatted = format_entry(e1, my_telegram_id=2)
    assert "🔄 [готовий до обміну]" in formatted

    # Кнопки в меню Дії
    menu_ready = get_queue_actions_reply_menu(is_in_queue=True, ready_to_swap=True)
    flat_btns_ready = [btn.text for row in menu_ready.keyboard for btn in row]
    assert "⏸ Не готовий до обміну" in flat_btns_ready

    # Перемикання назад у False
    new_st2 = queues.toggle_ready_to_swap(conn, 1, filled)
    assert new_st2 is False
    assert queues.is_ready_to_swap(conn, 1, filled) is False

    menu_not_ready = get_queue_actions_reply_menu(is_in_queue=True, ready_to_swap=False)
    flat_btns_not_ready = [btn.text for row in menu_not_ready.keyboard for btn in row]
    assert "🔄 Готовий до обміну" in flat_btns_not_ready


def test_ready_to_swap_cleared_on_swap_and_leave(conn, filled):
    # Вмикаємо статус готовності для студента 1
    queues.toggle_ready_to_swap(conn, 1, filled)
    assert queues.is_ready_to_swap(conn, 1, filled) is True

    # Знаходимо entry_id для 1 та 2
    entries = queues.get_entries(conn, filled)
    e1 = next(e for e in entries if e["telegram_id"] == 1)
    e2 = next(e for e in entries if e["telegram_id"] == 2)

    # Обмін місцями скидає ready_to_swap
    queues.swap_entries(conn, e1["id"], e2["id"])
    assert queues.is_ready_to_swap(conn, 1, filled) is False
    assert queues.is_ready_to_swap(conn, 2, filled) is False

    # Вмикаємо знову та перевіряємо скидання при виході
    queues.toggle_ready_to_swap(conn, 1, filled)
    assert queues.is_ready_to_swap(conn, 1, filled) is True
    queues.leave_queue(conn, 1, filled)
    assert queues.is_ready_to_swap(conn, 1, filled) is False




