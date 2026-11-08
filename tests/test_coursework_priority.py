from datetime import date

import pytest

from queue_bot.services import coursework_priority, queues

ADMIN = 100
KONDRATIUK = 2  # teacher_id з data/subjects.json
DATA = {
    "from": "2026-11-09",
    "to": "2026-11-13",
    "skip_subject_ids": [7],
    "supervisors": [{"teacher_id": KONDRATIUK, "students": ["Петренко Іван", "Шевчук Марія"]}],
}


@pytest.fixture
def week(monkeypatch):
    monkeypatch.setattr(coursework_priority, "load", lambda path=None: DATA)
    real = coursework_priority.applies
    monkeypatch.setattr(coursework_priority, "applies",
                        lambda conn, session, user_id, **kw: real(conn, session, user_id, today=date(2026, 11, 10), **kw))


def names(conn, session_id):
    return [(e["full_name"], bool(e["coursework_priority"])) for e in queues.get_entries(conn, session_id)]


def test_supervised_students_go_before_labs_in_order_of_signup(conn, make_user, week):
    for t, name in ((1, "Коваль Олена"), (2, "Бондар Олег"), (3, "Петренко Іван"), (4, "Шевчук Марія")):
        make_user(t, name)
    s = queues.create_session(conn, ADMIN, 2, KONDRATIUK, "ЛМВ")
    for t in (1, 2, 3, 4):
        queues.join_queue(conn, t, s)

    assert names(conn, s) == [
        ("Петренко Іван", True), ("Шевчук Марія", True), ("Коваль Олена", False), ("Бондар Олег", False),
    ]
    assert [e["position"] for e in queues.get_entries(conn, s)] == [1, 2, 3, 4]


def test_other_teacher_queue_is_unchanged(conn, make_user, week):
    make_user(1, "Коваль Олена")
    make_user(3, "Петренко Іван")
    s = queues.create_session(conn, ADMIN, 2, 3, "ЛМВ")  # Щуцький, не керівник Петренка
    queues.join_queue(conn, 1, s)
    queues.join_queue(conn, 3, s)

    assert names(conn, s) == [("Коваль Олена", False), ("Петренко Іван", False)]


def test_outside_dates_and_skipped_subject_do_not_apply(conn, make_user, monkeypatch):
    monkeypatch.setattr(coursework_priority, "load", lambda path=None: DATA)
    make_user(3, "Петренко Іван")
    lesson = queues.create_session(conn, ADMIN, 2, KONDRATIUK, "ЛМВ")
    coursework = queues.create_session(conn, ADMIN, 7, KONDRATIUK, "Курсова")
    user_id = conn.execute("SELECT id FROM users WHERE telegram_id = 3").fetchone()["id"]

    assert coursework_priority.applies(conn, queues.get_session(conn, lesson), user_id, today=date(2026, 11, 10))
    assert not coursework_priority.applies(conn, queues.get_session(conn, lesson), user_id, today=date(2026, 11, 8))
    assert not coursework_priority.applies(conn, queues.get_session(conn, lesson), user_id, today=date(2026, 11, 16))
    assert not coursework_priority.applies(conn, queues.get_session(conn, coursework), user_id, today=date(2026, 11, 10))


def test_missing_file_disables_feature(conn, make_user, tmp_path):
    make_user(3, "Петренко Іван")
    s = queues.create_session(conn, ADMIN, 2, KONDRATIUK, "ЛМВ")
    assert coursework_priority.load(str(tmp_path / "nope.json")) is None
    assert not coursework_priority.applies(conn, queues.get_session(conn, s), 1, data={})


def test_shipped_data_file_is_valid():
    data = coursework_priority.load()
    date.fromisoformat(data["from"]), date.fromisoformat(data["to"])
    assert all(isinstance(t["teacher_id"], int) and isinstance(t["students"], list) for t in data["supervisors"])


def test_apostrophe_variants_match(conn, make_user):
    make_user(5, "Дар’єв Даниїл")
    s = queues.create_session(conn, ADMIN, 2, KONDRATIUK, "ЛМВ")
    user_id = conn.execute("SELECT id FROM users WHERE telegram_id = 5").fetchone()["id"]
    data = dict(DATA, supervisors=[{"teacher_id": KONDRATIUK, "students": ["Дар`єв Даниїл"]}])
    assert coursework_priority.applies(conn, queues.get_session(conn, s), user_id, today=date(2026, 11, 10), data=data)
