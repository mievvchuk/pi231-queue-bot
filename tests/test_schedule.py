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

