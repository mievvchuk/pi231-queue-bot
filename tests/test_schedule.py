import datetime
import pytest

from services import replacements, schedule


def test_week_type_parity():
    # 2026-10-15: Thursday of week 42 (even week -> denominator)
    d_even = datetime.date(2026, 10, 15)
    assert d_even.isocalendar()[1] == 42
    assert schedule.get_week_type(d_even) == "denominator"

    # 2026-10-22: Thursday of week 43 (odd week -> numerator)
    d_odd = datetime.date(2026, 10, 22)
    assert d_odd.isocalendar()[1] == 43
    assert schedule.get_week_type(d_odd) == "numerator"


def test_base_schedule_loading_and_fallback():
    data = {
        "thursday": {
            "regular": [{"number": 1, "subject": "Базовий предмет", "teacher": "Викл 1", "room": "101", "startTime": "08:30", "endTime": "09:50"}],
            "numerator": [{"number": 2, "subject": "Чисельник пара", "teacher": "Викл 2", "room": "102", "startTime": "10:05", "endTime": "11:25"}],
            "denominator": [{"number": 2, "subject": "Знаменник пара", "teacher": "Викл 3", "room": "103", "startTime": "10:05", "endTime": "11:25"}],
        }
    }
    # For 2026-10-15 (denominator)
    d_den = datetime.date(2026, 10, 15)
    lessons = schedule.get_base_schedule(data, d_den)
    assert len(lessons) == 1
    assert lessons[0]["subject"] == "Знаменник пара"

    # For empty day
    d_sun = datetime.date(2026, 10, 18)
    assert schedule.get_base_schedule(data, d_sun) == []


def test_replacements_crud(conn):
    items = replacements.get_replacements(conn, group_name="ПІ-231")
    assert len(items) == 0

    item1 = {
        "date": "2026-10-15",
        "group_name": "ПІ-231",
        "lesson_number": 2,
        "old_subject": "Людино-машинна взаємодія",
        "new_subject": "Машинне навчання (Заміна)",
        "teacher": "Барабаш Д.С.",
        "room": "305",
    }
    replacements.save_replacement(conn, item1)

    loaded = replacements.get_replacements(conn, date="2026-10-15", group_name="ПІ-231")
    assert len(loaded) == 1
    assert loaded[0]["new_subject"] == "Машинне навчання (Заміна)"
    assert loaded[0]["lesson_number"] == 2

    # Formatting
    formatted = replacements.format_replacement(loaded[0])
    assert "Машинне навчання (Заміна)" in formatted
    assert "2026-10-15" in formatted

    # Clear replacements
    cleared = replacements.clear_replacements(conn, group_name="ПІ-231")
    assert cleared == 1
    assert len(replacements.get_replacements(conn, group_name="ПІ-231")) == 0


def test_actual_schedule_with_replacements(conn):
    sch_data = {
        "thursday": [
            {"number": 1, "subject": "Предмет 1", "teacher": "Т1", "room": "101", "startTime": "08:30", "endTime": "09:50"},
            {"number": 2, "subject": "Предмет 2", "teacher": "Т2", "room": "102", "startTime": "10:05", "endTime": "11:25"},
        ]
    }
    test_dt = datetime.date(2026, 10, 15)

    # Before replacement
    actual, reps = schedule.get_actual_schedule(conn, sch_data, test_dt, "ПІ-231")
    assert len(actual) == 2
    assert actual[1]["subject"] == "Предмет 2"
    assert not actual[1]["is_replacement"]

    # Add replacement for lesson 2
    replacements.save_replacement(conn, {
        "date": "2026-10-15",
        "group_name": "ПІ-231",
        "lesson_number": 2,
        "old_subject": "Предмет 2",
        "new_subject": "Новий Предмет 2",
        "teacher": "Новий Т2",
        "room": "999",
    })

    # After replacement
    actual_after, reps_after = schedule.get_actual_schedule(conn, sch_data, test_dt, "ПІ-231")
    assert len(actual_after) == 2
    assert actual_after[1]["subject"] == "Новий Предмет 2"
    assert actual_after[1]["teacher"] == "Новий Т2"
    assert actual_after[1]["room"] == "999"
    assert actual_after[1]["is_replacement"]

    # Formatting contains [ЗАМІНА]
    text = schedule.format_schedule_for_date(conn, sch_data, test_dt, "ПІ-231")
    assert "ЗАМІНА" in text
    assert "Новий Предмет 2" in text


def test_lesson_number_and_expansions():
    from services.recognition import expand_subject, expand_teacher, normalize_lesson_pair

    # 1-2 уроки -> 1 пара
    assert normalize_lesson_pair(1, "1-2") == 1
    # 3-4 уроки -> 2 пара
    assert normalize_lesson_pair(2, "3-4") == 2
    # 5-6 уроки -> 3 пара!
    assert normalize_lesson_pair(3, "5-6") == 3
    assert normalize_lesson_pair("5-6") == 3
    # 7-8 уроки -> 4 пара!
    assert normalize_lesson_pair(4, "7-8") == 4
    assert normalize_lesson_pair("7-8") == 4

    # Subject abbreviations expansion
    assert "Людино-машинна взаємодія" in expand_subject("ЛМВ")
    assert "Конструювання програмного забезпечення" in expand_subject("КПЗ")
    assert "Машинне навчання" in expand_subject("МН")

    # Teacher expansion
    assert expand_teacher("Кондратюк") == "Кондратюк Є.С."
    assert expand_teacher("Когут") == "Когут В.С."


def test_admin_schedule_keyboards():
    from handlers.common import get_admin_reply_menu, get_admin_schedule_menu

    admin_menu = get_admin_reply_menu()
    button_texts = [btn.text for row in admin_menu.keyboard for btn in row]
    assert "📅 Розклад і заміни" in button_texts

    sch_menu = get_admin_schedule_menu()
    sch_button_texts = [btn.text for row in sch_menu.keyboard for btn in row]
    assert "📥 Отримати schedule.json" in sch_button_texts
    assert "📤 Завантажити розклад" in sch_button_texts
    assert "🗑 Очистити всі заміни" in sch_button_texts
    assert "🟢 Статус системи" in sch_button_texts
    assert "🔙 В адмін-панель" in sch_button_texts


def test_replacement_correction_menus_and_helpers():
    from handlers.schedule import (
        normalize_date_input,
        render_corrections_menu,
        render_item_edit_menu,
        render_main_preview,
    )

    # Date normalization
    assert normalize_date_input("2026-10-16") == "2026-10-16"
    assert normalize_date_input("16.10.2026") == "2026-10-16"
    assert normalize_date_input("05.02.2026") == "2026-02-05"
    assert normalize_date_input("16.10").endswith("-10-16")

    token = "testtok1"
    sample_reps = [
        {
            "date": "2026-10-16",
            "group_name": "ПІ-231",
            "lesson_number": 3,
            "new_subject": "Людино-машинна взаємодія (ЛМВ)",
            "teacher": "Барабаш Д.С.",
            "room": "305",
            "old_subject": "Фізичне виховання",
        },
        {
            "date": "2026-10-16",
            "group_name": "ПІ-231",
            "lesson_number": 4,
            "new_subject": "Конструювання ПЗ",
            "teacher": "Когут В.С.",
            "room": "402",
            "old_subject": "",
        },
    ]

    # Main preview
    text, kb = render_main_preview(token, sample_reps, conf_pct=92)
    assert "Точність: ~92%" in text
    assert "Людино-машинна взаємодія" in text
    all_btns = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "✅ Підтвердити заміни" in all_btns
    assert "❌ Відхилити" in all_btns
    assert "✏️ Внести корективи" in all_btns

    # Corrections menu
    c_text, c_kb = render_corrections_menu(token, sample_reps)
    assert "Внесення коректив" in c_text
    c_btns = [btn.text for row in c_kb.inline_keyboard for btn in row]
    assert any("Пара 3" in b for b in c_btns)
    assert any("Пара 4" in b for b in c_btns)
    assert "➕ Додати заміну вручну" in c_btns
    assert "🔙 Готово (до підтвердження)" in c_btns

    # Single item editor
    i_text, i_kb = render_item_edit_menu(token, 0, sample_reps[0])
    assert "Редагування заміни #1" in i_text
    i_btns = [btn.text for row in i_kb.inline_keyboard for btn in row]
    assert "⏰ Номер пари" in i_btns
    assert "📚 Новий предмет" in i_btns
    assert "👨‍🏫 Викладач" in i_btns
    assert "🚪 Аудиторія" in i_btns
    assert "📅 Дата" in i_btns
    assert "❌ Було (що знято)" in i_btns
    assert "🗑 Видалити цю заміну" in i_btns
    assert "🔙 До списку замін" in i_btns
    assert "✅ Готово (до збереження)" in i_btns

