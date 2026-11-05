import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
from services import recognition, replacements, schedule, working_days


@pytest.mark.parametrize("group, expected", [
    ("ПІ-231", True), ("ПI-231", True), ("PI-231", True),
    ("ПІ-231,232", True), ("ПІ-232,231", True),
    ("ПІ-231, ПІ-232", True), ("ПІ – 231", True),
    ("КІ-231,233", False), ("КІ-231", False), ("МЕ-231", False),
    ("ПІ-232", False), ("ПІ-1231", False),
])
def test_exact_group_membership(group, expected):
    assert recognition.group_matches(group, "ПІ-231") is expected


@pytest.mark.asyncio
async def test_table_with_merged_and_shared_group_rows(conn, monkeypatch):
    monkeypatch.setattr(config, "RECOGNITION_API_KEY", "test-key")
    monkeypatch.setattr(config, "RECOGNITION_PROVIDER", "openai")
    rows = [
        {"group": "КІ-231,233", "lessons": "7-10", "newSubject": "Чужий предмет"},
        {"group": "ПІ-231", "lessons": "1-2", "newSubject": "Проект інтерфейсів", "teacher": "Атаман", "room": "243а"},
        {"group": "", "lessons": "5-6", "newSubject": "ЛМВ", "teacher": "Кондратюк", "room": "231"},
        {"group": "ПІ-232", "lessons": "3-4", "newSubject": "ЛМВ"},
        {"group": "", "lessons": "5-6", "newSubject": "Проект інтерфейсів", "teacher": "Атаман", "room": "245"},
        {"group": "ПІ-231,232", "lessons": "7-10", "newSubject": "Основи національного спротиву", "teacher": "Овчарук", "room": "111"},
        {"group": "МЕ-231", "lessons": "5-6", "newSubject": "Інформаційні системи"},
    ]
    result = {"date": "2026-11-06", "confidence": 0.95, "replacements": rows}
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={"choices": [{"message": {"content": json.dumps(result)}}]})
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.post.return_value = request
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    monkeypatch.setattr(recognition.aiohttp, "ClientSession", lambda: context)

    result = await recognition.recognize_replacements_from_image(b"image", conn=conn)
    items = result["replacements"]
    assert [r["lesson_number"] for r in items] == [1, 3, 4, 5]
    assert items[0]["old_subject"] == "Конструювання програмного забезпечення"
    assert items[0]["teacher"] == "Атаман В.О."
    assert items[0]["room"] == "243а"
    assert items[1]["teacher"] == "Кондратюк Є.С."
    assert items[1]["room"] == "231"
    assert "додаткова пара" in items[3]["old_subject"]
    for item in items:
        replacements.save_replacement(conn, item)
    saved = replacements.get_replacements(conn, date="2026-11-06")
    assert saved[0]["old_subject"] == items[0]["old_subject"]
    assert "не визначено" not in replacements.format_replacement(saved[0])
    assert "Конструювання програмного забезпечення" in replacements.format_replacements_broadcast(saved, "2026-11-06")


def test_old_subject_uses_week_type_and_working_saturday(conn):
    data = {"monday": [
        {"pair": 1, "subject": "Чисельник", "week_type": "numerator"},
        {"pair": 1, "subject": "Знаменник", "week_type": "denominator"},
    ]}
    working_days.set_working_saturday(conn, "2026-11-07", "monday", week_type="denominator")
    item = {"date": "2026-11-07", "lesson_number": 1}
    replacements.fill_old_subject(conn, item, data)
    assert item["old_subject"] == "Знаменник"
    item["old_subject"] = "Уточнено адміністратором"
    replacements.fill_old_subject(conn, item, data)
    assert item["old_subject"] == "Уточнено адміністратором"
    replacements.fill_old_subject(conn, item, data, force=True)
    assert item["old_subject"] == "Знаменник"


def test_legacy_replacement_with_missing_old_subject(conn):
    conn.execute(
        "INSERT INTO replacements (date, group_name, lesson_number, new_subject) VALUES (?, ?, ?, ?)",
        ("2026-11-06", "ПІ-231", 1, "Проект інтерфейсів"),
    )
    items = replacements.get_replacements(conn, date="2026-11-06")
    assert items[0]["old_subject"] == "Конструювання програмного забезпечення"
    assert "не визначено" not in replacements.format_replacement(items[0])


@pytest.mark.asyncio
async def test_correction_refreshes_old_subject(conn, monkeypatch):
    from handlers import schedule as handler

    data = {"friday": [{"pair": 1, "subject": "Предмет п'ятниці"}],
            "monday": [{"pair": 1, "subject": "Предмет понеділка"}]}
    monkeypatch.setattr(schedule, "load_schedule_data", lambda path: data)
    item = {"date": "2026-11-06", "lesson_number": 3, "new_subject": "Заміна"}
    monkeypatch.setitem(handler._PENDING_RECOGNITIONS, "testcopy", [item])
    callback = MagicMock()
    callback.from_user.id = 100
    callback.answer = AsyncMock()
    callback.message.edit_text = AsyncMock()
    callback.data = "rec_sp:testcopy:0:1"
    await handler.set_pair_number_handler(callback, conn)
    assert item["old_subject"] == "Предмет п'ятниці"
    callback.data = "rec_sd:testcopy:0:2026-11-09"
    await handler.set_date_value_handler(callback, conn)
    assert item["old_subject"] == "Предмет понеділка"
