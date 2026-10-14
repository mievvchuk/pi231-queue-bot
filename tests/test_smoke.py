from aiogram import Dispatcher

import config
from handlers import admin, start, student
from handlers.common import Guard, format_queue
from services import queues
from subjects import load_subjects


def test_subjects_json_is_valid():
    subjects = load_subjects(config.SUBJECTS_PATH)
    assert len(subjects) == 6
    by_name = {s["name"]: [t["name"] for t in s["teachers"]] for s in subjects}
    assert by_name["Машинне навчання"] == ["Барабаш Д.С."]
    assert len(by_name["Людино-машинна взаємодія"]) == 2
    assert not any("Фізичне" in name for name in by_name)


def test_routers_can_be_connected_without_token():
    dp = Dispatcher()
    dp.callback_query.outer_middleware(Guard())
    dp.include_routers(start.router, student.router, admin.router, start.fallback_router)


def test_queue_text_marks_current_user(conn, make_user):
    subjects = load_subjects(config.SUBJECTS_PATH)
    session = queues.create_session(conn, 100, 3, 4)
    make_user(1, "Петренко Іван")
    make_user(2, "Коваль Андрій")
    queues.join_queue(conn, 1, session)
    queues.join_queue(conn, 2, session)
    text = format_queue(subjects, queues.get_session(conn, session),
                        queues.get_entries(conn, session), my_telegram_id=2)
    assert "👉 2. Коваль Андрій (ви)" in text
    assert "1. Петренко Іван" in text and "👉 1." not in text
