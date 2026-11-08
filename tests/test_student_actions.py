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
    menu_ready = get_queue_actions_reply_menu(is_in_queue=True, swap_wish=None)
    flat_btns_ready = [btn.text for row in menu_ready.keyboard for btn in row]
    assert "⬆️ Хочу вище" in flat_btns_ready
    assert "⬇️ Хочу нижче" in flat_btns_ready

    # Перемикання назад у False
    new_st2 = queues.toggle_ready_to_swap(conn, 1, filled)
    assert new_st2 is False
    assert queues.is_ready_to_swap(conn, 1, filled) is False


def test_swap_wish_higher_and_lower(conn, filled):
    from handlers.common import format_entry, get_queue_actions_reply_menu

    # 1. Встановлення "higher"
    assert queues.get_swap_wish(conn, 2, filled) is None
    res = queues.set_swap_wish(conn, 2, filled, "higher")
    assert res == "higher"
    assert queues.get_swap_wish(conn, 2, filled) == "higher"
    assert queues.is_ready_to_swap(conn, 2, filled) is True

    # Форматування запису
    entries = queues.get_entries(conn, filled)
    e2 = next(e for e in entries if e["telegram_id"] == 2)
    assert "⬆️ [хочу вище]" in format_entry(e2)

    # Меню дій: має бути кнопка для скасування
    menu = get_queue_actions_reply_menu(is_in_queue=True, swap_wish="higher")
    flat_btns = [btn.text for row in menu.keyboard for btn in row]
    assert "⏸ Зняти «Хочу вище»" in flat_btns
    assert "⬇️ Хочу нижче" in flat_btns

    # 2. Зміна на "lower"
    res2 = queues.set_swap_wish(conn, 2, filled, "lower")
    assert res2 == "lower"
    assert queues.get_swap_wish(conn, 2, filled) == "lower"
    entries = queues.get_entries(conn, filled)
    e2 = next(e for e in entries if e["telegram_id"] == 2)
    assert "⬇️ [хочу нижче]" in format_entry(e2)

    menu_lower = get_queue_actions_reply_menu(is_in_queue=True, swap_wish="lower")
    flat_btns_l = [btn.text for row in menu_lower.keyboard for btn in row]
    assert "⬆️ Хочу вище" in flat_btns_l
    assert "⏸ Зняти «Хочу нижче»" in flat_btns_l

    # 3. Повторне натискання "lower" вимикає його
    res3 = queues.set_swap_wish(conn, 2, filled, "lower")
    assert res3 is None
    assert queues.get_swap_wish(conn, 2, filled) is None
    entries = queues.get_entries(conn, filled)
    e2 = next(e for e in entries if e["telegram_id"] == 2)
    assert "хочу" not in format_entry(e2)


def test_ready_to_swap_cleared_on_swap_and_leave(conn, filled):
    # Вмикаємо статус для студента 1
    queues.set_swap_wish(conn, 1, filled, "lower")
    assert queues.get_swap_wish(conn, 1, filled) == "lower"

    # Знаходимо entry_id для 1 та 2
    entries = queues.get_entries(conn, filled)
    e1 = next(e for e in entries if e["telegram_id"] == 1)
    e2 = next(e for e in entries if e["telegram_id"] == 2)

    # Обмін місцями скидає swap_wish
    queues.swap_entries(conn, e1["id"], e2["id"])
    assert queues.get_swap_wish(conn, 1, filled) is None
    assert queues.get_swap_wish(conn, 2, filled) is None

    # Вмикаємо знову та перевіряємо скидання при виході
    queues.set_swap_wish(conn, 1, filled, "higher")
    assert queues.get_swap_wish(conn, 1, filled) == "higher"
    queues.leave_queue(conn, 1, filled)
    assert queues.get_swap_wish(conn, 1, filled) is None


@pytest.mark.asyncio
async def test_notify_swap_wishes_and_smart_match(conn, filled):
    from unittest.mock import AsyncMock, MagicMock
    from handlers.common import clear_swap_wish_cooldowns, notify_swap_wishes

    sample_subjects = [{"id": "sub1", "name": "КПЗ", "teachers": [{"id": "teach1", "name": "Кондратюк Є.С."}]}]
    clear_swap_wish_cooldowns()
    bot = MagicMock()
    bot.send_message = AsyncMock()

    # Студент 2 (позиція 2) ставить "higher" -> студент 1 (позиція 1) отримує сповіщення
    queues.set_swap_wish(conn, 2, filled, "higher")
    matches = await notify_swap_wishes(bot, conn, sample_subjects, filled, 2, "higher")
    assert matches == []
    assert bot.send_message.call_count == 1
    call_args = bot.send_message.call_args
    # Сповіщення надіслано студенту 1
    assert call_args[0][0] == 1
    assert "Студент за вами готовий піти здавати раніше" in call_args[0][1]
    # Перевіряємо наявність інлайн-кнопки sw_req
    kb = call_args[1]["reply_markup"]
    btn = kb.inline_keyboard[0][0]
    assert btn.callback_data.startswith("sw_req:")

    # Перевірка захисту від спаму (cooldown): повторний виклик не шле сповіщення знову
    bot.send_message.reset_mock()
    await notify_swap_wishes(bot, conn, sample_subjects, filled, 2, "higher")
    assert bot.send_message.call_count == 0

    # Тепер студент 1 (позиція 1) вмикає "lower"
    # Оскільки студент 2 вже має "higher", для студента 2 спрацьовує Smart Match!
    clear_swap_wish_cooldowns()
    queues.set_swap_wish(conn, 1, filled, "lower")
    matches2 = await notify_swap_wishes(bot, conn, sample_subjects, filled, 1, "lower")
    assert len(matches2) == 1
    assert matches2[0]["telegram_id"] == 2
    assert bot.send_message.call_count == 1
    call_args2 = bot.send_message.call_args
    assert call_args2[0][0] == 2
    assert "Знайдено можливість для обміну місцями" in call_args2[0][1]


@pytest.mark.asyncio
async def test_swap_request_from_notification_callback(conn, filled):
    from unittest.mock import AsyncMock, MagicMock
    from handlers.student import swap_request_from_notification

    sample_subjects = [{"id": "sub1", "name": "КПЗ", "teachers": [{"id": "teach1", "name": "Кондратюк Є.С."}]}]

    entries = queues.get_entries(conn, filled)
    e1 = next(e for e in entries if e["telegram_id"] == 1)
    e2 = next(e for e in entries if e["telegram_id"] == 2)

    from aiogram.types import Message
    callback = MagicMock()
    callback.from_user.id = 1
    callback.data = f"sw_req:{e1['id']}:{e2['id']}"
    callback.bot.send_message = AsyncMock()
    callback.message = MagicMock(spec=Message)
    callback.message.edit_text = AsyncMock()

    await swap_request_from_notification(callback, conn, sample_subjects)

    # Перевіряємо, що студенту 2 надійшла пропозиція обміну
    assert callback.bot.send_message.call_count == 1
    sent_args = callback.bot.send_message.call_args
    assert sent_args[0][0] == 2
    assert "Пропозиція обміну місцями" in sent_args[0][1]
    # Клавіатура містить sw_yes та sw_no
    kb = sent_args[1]["reply_markup"]
    btn_yes = kb.inline_keyboard[0][0]
    btn_no = kb.inline_keyboard[0][1]
    assert btn_yes.callback_data == f"sw_yes:{e1['id']}:{e2['id']}:{e1['user_id']}"
    assert btn_no.callback_data == f"sw_no:{e1['id']}:{e2['id']}:{e1['user_id']}"


def test_format_queues_overview_and_filtering(conn, filled):
    from handlers.common import format_queues_overview, format_my_queues_overview
    from subjects import load_subjects
    import config

    subjects = load_subjects(config.SUBJECTS_PATH)
    sessions = queues.list_sessions(conn, ("open", "closed"))

    # 1. Перевірка форматування огляду черг
    overview = format_queues_overview(conn, sessions, subjects)
    assert "Активні черги групи ПІ-231" in overview
    assert "Звичайні пари:" in overview
    assert "у черзі:" in overview

    # 2. Перевірка огляду моїх черг
    my_overview = format_my_queues_overview(conn, sessions, subjects, telegram_id=1)
    assert "Ваші черги" in my_overview
    assert "Ваше місце:" in my_overview

    # 3. Перевірка фільтрації курсової з пар
    lesson_subjects = [s for s in subjects if "курсов" not in s["name"].lower()]
    assert not any("курсов" in s["name"].lower() for s in lesson_subjects)


def test_queue_categories_option_1(conn, make_user):
    from handlers.common import (
        categorize_sessions,
        format_category_queues_overview,
        format_queue_categories_overview,
        get_category_queues_reply_menu,
        get_queue_categories_reply_menu,
    )
    from handlers.student import is_category_button
    from subjects import load_subjects
    import config
    from aiogram.types import Message

    subjects = load_subjects(config.SUBJECTS_PATH)
    make_user(100, "Адмін Тестовий")

    # Створюємо 1 звичайну пару, 1 консультацію та 1 чергу на курсову
    s_lesson = queues.create_session(conn, 100, 1, 1, queue_type="lesson")
    s_consult = queues.create_session(conn, 100, 2, 2, queue_type="consultation")
    s_cw = queues.create_session(conn, 100, 7, 6, queue_type="consultation")  # Курсова

    sessions = queues.list_sessions(conn, ("open", "closed"))
    lessons, consults, coursework = categorize_sessions(sessions, subjects)

    assert len(lessons) == 1
    assert len(consults) == 1
    assert len(coursework) == 1

    # 1. Хаб категорій
    cat_menu = get_queue_categories_reply_menu(len(lessons), len(consults), len(coursework))
    btn_texts = [btn.text for row in cat_menu.keyboard for btn in row]
    assert "📖 Черги на пари (1)" in btn_texts
    assert "💬 Консультації (1)" in btn_texts
    assert "📑 Курсова робота (1)" in btn_texts
    assert "🔄 Оновити" in btn_texts
    assert "🏠 Головне меню" in btn_texts

    cat_overview = format_queue_categories_overview(len(lessons), len(consults), len(coursework))
    assert "Активні черги групи ПІ-231" in cat_overview
    assert "Усього активних черг: <b>3</b>" in cat_overview
    assert "📖 Черги на пари: <b>1</b>" in cat_overview

    # 2. Меню всередині категорії
    lesson_menu = get_category_queues_reply_menu(lessons, subjects, page=1)
    lesson_btn_texts = [btn.text for row in lesson_menu.keyboard for btn in row]
    assert any("📋 " in b for b in lesson_btn_texts)
    assert "🔙 До категорій" in lesson_btn_texts

    cw_overview = format_category_queues_overview(conn, coursework, subjects, "coursework")
    assert "Черги на курсову роботу" in cw_overview
    assert "Список черг за керівниками:" in cw_overview

    # 3. Розпізнавання кнопок категорій
    class DummyMsg:
        def __init__(self, text):
            self.text = text

    assert is_category_button(DummyMsg("📖 Черги на пари (2)"))
    assert is_category_button(DummyMsg("💬 Консультації (1)"))
    assert is_category_button(DummyMsg("📑 Курсова робота (3)"))
    assert not is_category_button(DummyMsg("📋 Комп'ютерні мережі"))
    assert not is_category_button(DummyMsg("🏠 Головне меню"))







