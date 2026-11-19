import datetime
import pytest

from queue_bot.services import replacements, schedule


def test_committed_schedule_has_no_fifth_pair_and_monday_starts_with_hci():
    from pathlib import Path
    data = schedule.load_schedule_data(str(Path(__file__).resolve().parents[1] / 'data/schedule.json'))
    for parity in ('numerator', 'denominator'):
        for day in schedule.DAY_KEYS:
            assert all(item['number'] != 5 for item in schedule.get_base_schedule_for_day(data, day, parity))
        first = schedule.get_base_schedule_for_day(data, 'monday', parity)[0]
        assert (first['number'], first['subject']) == (2, 'Людино-машинна взаємодія')


def test_five_slots_preserve_gaps_and_have_exact_bell_times(conn):
    data = {'monday': [{'pair': 2, 'subject': 'МН', 'startTime': '10:05', 'endTime': '11:25'}]}
    actual, _ = schedule.get_actual_schedule(conn, data, datetime.date(2026, 11, 2))
    slots = schedule.get_day_slots(actual)
    assert len(actual) == 1
    assert [slot['number'] for slot in slots] == [1, 2, 3, 4, 5]
    assert [(s['startTime'], s['endTime']) for s in slots] == [
        ('08:30', '09:50'), ('10:00', '11:20'), ('11:50', '13:10'),
        ('13:20', '14:40'), ('14:50', '16:10')]
    assert slots[0]['lessons'] == [] and slots[4]['lessons'] == []
    assert slots[1]['lessons'][0]['subject'] == 'МН'
    assert (actual[0]['startTime'], actual[0]['endTime']) == ('10:00', '11:20')
    assert '10:00–11:20' in schedule.format_schedule_for_date(conn, data, datetime.date(2026, 11, 2))


def test_fifth_pair_replacement_has_time_even_without_a_base_lesson(conn):
    replacements.save_replacement(conn, {'date': '2026-11-03', 'group_name': 'ПІ-231',
        'lesson_number': 5, 'new_subject': 'Додаткова пара', 'teacher': 'Викладач', 'room': '231'})
    actual, _ = schedule.get_actual_schedule(conn, {}, datetime.date(2026, 11, 3))
    fifth = schedule.get_day_slots(actual)[4]
    assert fifth['lessons'][0]['is_replacement']
    assert (actual[0]['startTime'], actual[0]['endTime']) == ('14:50', '16:10')


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
    from queue_bot.services.recognition import expand_subject, expand_teacher, normalize_lesson_pair

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
    from queue_bot.handlers.common import get_admin_reply_menu, get_admin_schedule_menu, get_schedule_reply_menu

    student_sch_menu = get_schedule_reply_menu()
    student_sch_texts = [btn.text for row in student_sch_menu.keyboard for btn in row]
    assert "📅 Сьогодні" in student_sch_texts
    assert "➡️ Завтра" in student_sch_texts
    assert "🗓 Весь тиждень" in student_sch_texts
    assert "🔄 Заміни" in student_sch_texts
    assert "🏠 Головне меню" in student_sch_texts

    admin_menu = get_admin_reply_menu()
    button_texts = [btn.text for row in admin_menu.keyboard for btn in row]
    assert "📅 Розклад і заміни" in button_texts

    sch_menu = get_admin_schedule_menu()
    sch_button_texts = [btn.text for row in sch_menu.keyboard for btn in row]
    assert "✏️ Редагувати розклад" in sch_button_texts
    assert "🏢 Робоча субота" in sch_button_texts
    assert "🗑 Очистити всі заміни" not in sch_button_texts
    assert "🟢 Статус системи" not in sch_button_texts
    assert "🔙 В адмін-панель" in sch_button_texts


def test_replacement_correction_menus_and_helpers():
    from queue_bot.handlers.schedule import (
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


def test_working_saturdays_flow(conn):
    from queue_bot.services import working_days

    sat_date = datetime.date(2026, 10, 17)
    sat_str = sat_date.isoformat()

    # Initially not working
    assert working_days.get_working_saturday(conn, sat_str) is None

    # Set Saturday as working for Monday
    working_days.set_working_saturday(conn, sat_str, "monday", week_type="denominator")
    ws = working_days.get_working_saturday(conn, sat_str)
    assert ws is not None
    assert ws["replaces_day"] == "monday"
    assert ws["week_type"] == "denominator"

    # Schedule integration
    sch_data = {
        "monday": [
            {"number": 1, "subject": "Математичний аналіз", "teacher": "Т1", "room": "101"},
            {"number": 2, "subject": "КПЗ", "teacher": "Т2", "room": "102"},
        ],
        "saturday": [],
    }

    # Before replacements: Saturday has Monday's lessons!
    actual, reps = schedule.get_actual_schedule(conn, sch_data, sat_date, "ПІ-231")
    assert len(actual) == 2
    assert actual[0]["subject"] == "Математичний аналіз"
    assert actual[1]["subject"] == "КПЗ"

    # Add a replacement specifically on that Saturday!
    replacements.save_replacement(conn, {
        "date": sat_str,
        "group_name": "ПІ-231",
        "lesson_number": 2,
        "old_subject": "КПЗ",
        "new_subject": "Машинне навчання (Заміна)",
        "teacher": "Барабаш Д.С.",
        "room": "305",
    })

    # After replacement: lesson 2 is replaced on Saturday!
    actual_after, reps_after = schedule.get_actual_schedule(conn, sch_data, sat_date, "ПІ-231")
    assert len(actual_after) == 2
    assert actual_after[1]["subject"] == "Машинне навчання (Заміна)"
    assert actual_after[1]["is_replacement"] is True

    # Formatting shows Saturday as working day with replaced day info
    text = schedule.format_schedule_for_date(conn, sch_data, sat_date, "ПІ-231")
    assert "Робоча субота за розкладом: Понеділок" in text
    assert "Машинне навчання (Заміна)" in text
    assert "ЗАМІНА" in text

    # Change day: Monday -> Tuesday
    sch_data["tuesday"] = [{"number": 1, "subject": "Бази даних", "teacher": "Т3", "room": "103"}]
    working_days.set_working_saturday(conn, sat_str, "tuesday")
    ws_updated = working_days.get_working_saturday(conn, sat_str)
    assert ws_updated["replaces_day"] == "tuesday"

    # Revert / Remove working Saturday
    working_days.remove_working_saturday(conn, sat_str)
    assert working_days.get_working_saturday(conn, sat_str) is None

    # After removal, Saturday is back to day off (replacements remain, but base lessons are gone)
    actual_rev, reps_rev = schedule.get_actual_schedule(conn, sch_data, sat_date, "ПІ-231")
    assert all(l["is_replacement"] for l in actual_rev)


def test_next_working_day_calculation(conn):
    from datetime import date
    from queue_bot.services.working_days import get_next_working_day, set_working_saturday, remove_working_saturday

    # 1. Понеділок (12.10.2026) -> Вівторок (13.10.2026)
    mon = date(2026, 10, 12)
    assert get_next_working_day(conn, mon) == date(2026, 10, 13)

    # 2. П'ятниця (16.10.2026) без робочої суботи -> Понеділок (19.10.2026)
    fri = date(2026, 10, 16)
    assert get_next_working_day(conn, fri) == date(2026, 10, 19)

    # 3. П'ятниця (16.10.2026) з робочою суботою (17.10.2026) -> Субота (17.10.2026)
    set_working_saturday(conn, "2026-10-17", "monday")
    try:
        assert get_next_working_day(conn, fri) == date(2026, 10, 17)
    finally:
        remove_working_saturday(conn, "2026-10-17")

    # 4. Неділя (18.10.2026) -> Понеділок (19.10.2026)
    sun = date(2026, 10, 18)
    assert get_next_working_day(conn, sun) == date(2026, 10, 19)


def test_auto_archive_previous_replacements(conn):
    from queue_bot.services import replacements
    from queue_bot.handlers.common import get_admin_schedule_menu

    # Архівування працює автоматично, без кнопки в меню
    menu = get_admin_schedule_menu()
    btn_texts = [btn.text for row in menu.keyboard for btn in row]
    assert "📦 Архів замін" not in btn_texts

    # 1. Додаємо заміну на четвер (2026-10-15)
    replacements.save_replacement(conn, {
        "date": "2026-10-15",
        "group_name": "ПІ-231",
        "lesson_number": 1,
        "new_subject": "Стара заміна чт",
    })
    active_thursday = replacements.get_replacements(conn, date="2026-10-15")
    assert len(active_thursday) == 1
    assert active_thursday[0]["new_subject"] == "Стара заміна чт"

    # 2. Додаємо нові заміни на п'ятницю (2026-10-16)
    replacements.save_replacement(conn, {
        "date": "2026-10-16",
        "group_name": "ПІ-231",
        "lesson_number": 1,
        "new_subject": "Нова заміна пт",
    })

    # Попередня заміна за четвер автоматично заархівована!
    active_now_thursday = replacements.get_replacements(conn, date="2026-10-15")
    assert len(active_now_thursday) == 0

    # А в архіві вона присутня
    archived = replacements.get_archived_replacements(conn)
    assert any(r["new_subject"] == "Стара заміна чт" for r in archived)

    # Активні заміни на п'ятницю доступні
    active_friday = replacements.get_replacements(conn, date="2026-10-16")
    assert len(active_friday) == 1
    assert active_friday[0]["new_subject"] == "Нова заміна пт"


def test_format_replacements_broadcast():
    items = [
        {
            "date": "2026-10-17",
            "lesson_number": 2,
            "old_subject": "Фізика",
            "new_subject": "КПЗ",
            "teacher": "Кондратюк Є.С.",
            "room": "231",
        },
        {
            "date": "2026-10-17",
            "lesson_number": 1,
            "old_subject": None,
            "new_subject": "Математика",
            "teacher": "Ковальчук С.О.",
            "room": None,
        },
    ]
    text = replacements.format_replacements_broadcast(items, "2026-10-17")
    assert "Опубліковано заміни пар" in text
    assert "2026-10-17" in text
    assert "1 пара:" in text
    assert "2 пара:" in text
    assert "КПЗ" in text
    assert "Кондратюк Є.С." in text
    assert "ауд. 231" in text
    assert "замість: Фізика" in text


def test_updated_help_sections():
    from queue_bot.handlers.start import HELP_SECTIONS
    assert "Хочу вище / ⬇️ Хочу нижче" in HELP_SECTIONS["⚙️ Дії в черзі"]
    assert "17:00" in HELP_SECTIONS["📅 Розклад та заміни"]
    assert "Миттєві сповіщення" in HELP_SECTIONS["📅 Розклад та заміни"]


@pytest.mark.asyncio
async def test_open_schedule_menu_time_of_day_switch(conn):
    from unittest.mock import AsyncMock, MagicMock, patch
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.fsm.storage.base import StorageKey
    from queue_bot.handlers.schedule import open_schedule_menu

    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=10, user_id=10))

    message = MagicMock()
    message.from_user.id = 10
    message.answer = AsyncMock()

    # 1. До 15:00 показує "на сьогодні"
    morning_dt = datetime.datetime(2026, 10, 21, 10, 30, tzinfo=datetime.timezone.utc)
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = morning_dt
        await open_schedule_menu(message, state, conn)
        text = message.answer.call_args[0][0]
        assert "на сьогодні" in text

    # 2. Після 15:00 показує "на завтра"
    evening_dt = datetime.datetime(2026, 10, 21, 15, 30, tzinfo=datetime.timezone.utc)
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = evening_dt
        await open_schedule_menu(message, state, conn)
        text = message.answer.call_args[0][0]
        assert "на завтра" in text







def test_lesson_note_from_schedule_json_reaches_text_and_mini_app(conn):
    from pathlib import Path
    data = schedule.load_schedule_data(str(Path(__file__).resolve().parents[1] / 'data/schedule.json'))
    thursday = datetime.date(2026, 11, 19)
    lessons, _ = schedule.get_actual_schedule(conn, data, thursday)
    web = next(item for item in lessons if item['subject'] == 'Програмування в Інтернет')
    assert web['note'] == 'Остання пара'
    assert '📝 <i>Остання пара</i>' in schedule.format_schedule_for_date(conn, data, thursday)
    assert all(not item['note'] for item in lessons if item is not web)

def test_schedule_import_preserves_legacy_edits_and_runs_once(tmp_path):
    import json

    from queue_bot.storage import database as db

    source = tmp_path / "edited_schedule.json"
    source.write_text(json.dumps({
        "wednesday": {
            "numerator": [{"pair": 3, "subject": "Чисельник", "teachers": [
                {"name": "Перший", "room": "101"}, {"name": "Другий", "room": None},
            ]}],
            "denominator": [{"number": 2, "subject": "Знаменник", "teacher": "Третій", "room": "102"}],
        },
    }), encoding="utf-8")
    database = str(tmp_path / "schedule.db")
    conn = db.connect(database)
    schedule.import_legacy_schedule(conn, str(source))
    data = schedule.read_schedule(conn)
    numerator = schedule.get_base_schedule_for_day(data, "wednesday", "numerator")
    assert numerator[0]["teachers_raw"] == [{"name": "Перший", "room": "101"}, {"name": "Другий", "room": None}]
    schedule.set_schedule_pair(conn, "wednesday", "numerator", 3, None)
    conn.close()

    source.unlink()
    conn = db.connect(database)
    try:
        schedule.import_legacy_schedule(conn, str(source))
        data = schedule.read_schedule(conn)
        assert schedule.get_base_schedule_for_day(data, "wednesday", "numerator") == []
        denominator = schedule.get_base_schedule_for_day(data, "wednesday", "denominator")
        assert [(lesson["number"], lesson["subject"], lesson["room"]) for lesson in denominator] == [
            (2, "Знаменник", "102"),
        ]
        assert conn.execute("SELECT COUNT(*) FROM schedule_teachers").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_edit_schedule_pair_from_telegram(conn):
    from unittest.mock import AsyncMock, MagicMock

    from queue_bot.handlers import schedule as handler

    state = AsyncMock()
    callback = MagicMock()
    callback.data = "base_pair:wednesday:numerator:2"
    callback.from_user.id = 100
    schedule.replace_schedule_from_backup(conn, {
        "wednesday": [{"pair": 2, "subject": "Попередній предмет", "note": "Примітка"}],
    })
    callback.answer = AsyncMock()
    callback.message.answer = AsyncMock()
    await handler.select_schedule_pair(callback, state, conn)
    state.set_state.assert_awaited_once_with(handler.EditScheduleState.waiting_for_lesson)
    state.get_data.return_value = state.set_data.await_args.args[0]
    message = MagicMock()
    message.from_user.id = 100
    message.text = "Новий предмет\nКогут В.С. | 235\nБарабаш Д.С."
    message.answer = AsyncMock()
    await handler.save_schedule_pair(message, state, conn)

    wednesday = datetime.date(2026, 11, 18)
    data = schedule.read_schedule(conn)
    text = schedule.format_schedule_for_date(conn, data, wednesday)
    assert "1 пара — немає заняття" in text
    assert "2 пара</b> (10:00–11:20)" in text
    assert "Новий предмет" in text
    assert "Примітка" in text
    assert "Когут В.С. (ауд. 235)" in text
    assert "Барабаш Д.С." in text
    assert "4 пара — немає заняття" in text
    assert "5 пара — немає заняття" in text

    message.text = "-"
    await handler.save_schedule_pair(message, state, conn)
    data = schedule.read_schedule(conn)
    text = schedule.format_schedule_for_date(conn, data, wednesday)
    assert "2 пара — немає заняття" in text
    assert 2 not in {lesson["number"] for lesson in schedule.get_base_schedule(data, wednesday)}
    assert 2 in {lesson["number"] for lesson in schedule.get_base_schedule(data, wednesday + datetime.timedelta(days=7))}
