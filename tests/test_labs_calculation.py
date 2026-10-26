import pytest

from services import queues
from services.errors import BotError
from handlers.common import format_entry, format_queue, get_queue_actions_reply_menu
import config
from subjects import load_subjects


def test_estimate_labs_time_and_format():
    assert queues.estimate_labs_time(1) == (7, 10)
    assert queues.estimate_labs_time(2) == (15, 20)
    assert queues.estimate_labs_time(3) == (20, 30)
    assert queues.estimate_labs_time(4) == (30, 40)
    assert queues.estimate_labs_time(5) == (35, 50)

    # Format check matches the poll: "Шевцов Ілюша (2 лаб.) орієнтовно 15-20хв"
    assert queues.format_lab_estimate(1) == "(1 лаб.) орієнтовно 7-10хв"
    assert queues.format_lab_estimate(2) == "(2 лаб.) орієнтовно 15-20хв"
    assert queues.format_lab_estimate(3) == "(3 лаб.) орієнтовно 20-30хв"


def test_join_queue_with_default_and_custom_labs(conn, make_user):
    make_user(1, "Шевцов Ілюша")
    make_user(2, "Олексюк Роман")

    s_id = queues.create_session(conn, 100, 3, 4)
    # Default is 1
    queues.join_queue(conn, 1, s_id)
    assert queues.get_user_labs_count(conn, 1, s_id) == 1

    # Custom labs_count = 2
    queues.join_queue(conn, 2, s_id, labs_count=2)
    assert queues.get_user_labs_count(conn, 2, s_id) == 2

    entries = queues.get_entries(conn, s_id)
    assert entries[0]["labs_count"] == 1
    assert entries[1]["labs_count"] == 2


def test_set_labs_count_for_student(conn, make_user):
    make_user(1, "Шевцов Ілюша")
    s_id = queues.create_session(conn, 100, 3, 4)
    queues.join_queue(conn, 1, s_id)

    # Change to 2
    queues.set_labs_count(conn, 1, s_id, 2)
    assert queues.get_user_labs_count(conn, 1, s_id) == 2

    # Validation
    with pytest.raises(BotError):
        queues.set_labs_count(conn, 1, s_id, 0)
    with pytest.raises(BotError):
        queues.set_labs_count(conn, 1, s_id, 11)


def test_admin_set_entry_labs_count(conn, make_user):
    make_user(1, "Шевцов Ілюша")
    s_id = queues.create_session(conn, 100, 3, 4)
    queues.join_queue(conn, 1, s_id)
    entry = queues.get_entries(conn, s_id)[0]

    # Admin (100) sets labs count to 3
    queues.set_entry_labs_count(conn, 100, entry["id"], 3)
    assert queues.get_user_labs_count(conn, 1, s_id) == 3

    # Non-admin cannot set
    with pytest.raises(BotError):
        queues.set_entry_labs_count(conn, 1, entry["id"], 4)


def test_queue_estimate_and_formatting(conn, make_user):
    subjects = load_subjects(config.SUBJECTS_PATH)
    make_user(1, "Шевцов Ілюша")
    make_user(2, "Олексюк Роман")

    s_id = queues.create_session(conn, 100, 3, 4)
    queues.join_queue(conn, 1, s_id, labs_count=2)
    queues.join_queue(conn, 2, s_id, labs_count=1)

    entries = queues.get_entries(conn, s_id)
    summary = queues.calculate_queue_estimate(entries)
    assert summary["total_labs"] == 3
    assert summary["min_minutes"] == 20
    assert summary["max_minutes"] == 30

    session = queues.get_session(conn, s_id)
    text = format_queue(subjects, session, entries, my_telegram_id=1)

    # Check that individual student has "(2 лаб.) орієнтовно 15-20хв"
    assert "1. Шевцов Ілюша (2 лаб.) орієнтовно 15-20хв" in text
    assert "2. Олексюк Роман (1 лаб.) орієнтовно 7-10хв" in text
    # Check total queue duration
    assert "⏱ <b>Орієнтовна тривалість черги:</b> ~20-30 хв (усього 3 лаб.)" in text


def test_free_slot_does_not_have_lab_estimate(conn, make_user):
    subjects = load_subjects(config.SUBJECTS_PATH)
    make_user(1, "Шевцов Ілюша")
    s_id = queues.create_session(conn, 100, 3, 4)
    queues.join_queue(conn, 1, s_id, position=2, labs_count=2)

    entries = queues.get_entries(conn, s_id)
    assert len(entries) == 2
    # Position 1 is free slot
    assert entries[0]["user_id"] is None
    e1_text = format_entry(entries[0])
    assert "Вільне місце" in e1_text
    assert "лаб." not in e1_text

    # Position 2 is real student
    e2_text = format_entry(entries[1])
    assert "(2 лаб.) орієнтовно 15-20хв" in e2_text


def test_carry_preserves_labs_count(conn, make_user):
    make_user(1, "Шевцов Ілюша")
    s_id = queues.create_session(conn, 100, 3, 4)
    queues.join_queue(conn, 1, s_id, labs_count=3)
    e1 = queues.get_entries(conn, s_id)[0]

    # Carry to next session
    new_s_id = queues.finish_session(conn, 100, s_id, create_new=True, carry={e1["id"]: 1})
    new_entries = queues.get_entries(conn, new_s_id)
    assert len(new_entries) == 1
    assert new_entries[0]["labs_count"] == 3


def test_actions_menu_contains_labs_button():
    menu = get_queue_actions_reply_menu(is_in_queue=True, labs_count=2)
    flat_btns = [btn.text for row in menu.keyboard for btn in row]
    assert "🧪 Кількість лаб (2)" in flat_btns
