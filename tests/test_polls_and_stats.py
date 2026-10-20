from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

import config
from handlers.common import (
    get_admin_polls_menu,
    get_admin_reply_menu,
    get_admin_reports_menu,
    get_reply_menu,
)
from services import polls, queues
from services.errors import BotError
from subjects import load_subjects

UK_TZ = ZoneInfo(config.TIMEZONE)


def test_create_poll_validation(conn):
    with pytest.raises(BotError, match="Запитання опитування не може бути порожнім"):
        polls.create_poll(conn, actor_id=100, question="   ", options=["Так", "Ні"])

    with pytest.raises(BotError, match="щонайменше 2 варіанти"):
        polls.create_poll(conn, actor_id=100, question="Чи буде пара?", options=["Так"])

    with pytest.raises(BotError, match="Максимальна кількість варіантів"):
        polls.create_poll(
            conn,
            actor_id=100,
            question="Оберіть варіант",
            options=[f"Варіант {i}" for i in range(11)],
        )


def test_public_poll_voting_and_results(conn, make_user):
    make_user(101, "Іванов Іван")
    make_user(102, "Петренко Петро")

    closes_at = (datetime.now(UK_TZ) + timedelta(minutes=60)).isoformat()
    poll_id = polls.create_poll(
        conn,
        actor_id=100,
        question="Чи переносимо консультацію на п'ятницю?",
        options=["✅ Так", "❌ Ні", "🤔 Утримаюсь"],
        is_anonymous=False,
        allows_multiple=False,
        target="private",
        closes_at=closes_at,
    )

    polls.register_poll_instance(conn, poll_id, chat_id=101, message_id=5001, tg_poll_id="tg_poll_101")
    polls.register_poll_instance(conn, poll_id, chat_id=102, message_id=5002, tg_poll_id="tg_poll_102")

    # User 101 votes for option 0
    found_id = polls.record_poll_answer(conn, "tg_poll_101", 101, "Іван", [0])
    assert found_id == poll_id

    # User 102 votes for option 1
    polls.record_poll_answer(conn, "tg_poll_102", 102, "Петро", [1])

    results = polls.get_poll_results(conn, poll_id)
    assert results["total_voters"] == 2
    assert results["options"][0]["count"] == 1
    assert results["options"][0]["percent"] == 50
    assert "Іванов Іван" in results["options"][0]["voters"]
    assert results["options"][1]["count"] == 1
    assert "Петренко Петро" in results["options"][1]["voters"]

    # User 102 changes vote to option 0
    polls.record_poll_answer(conn, "tg_poll_102", 102, "Петро", [0])
    results2 = polls.get_poll_results(conn, poll_id)
    assert results2["total_voters"] == 2
    assert results2["options"][0]["count"] == 2
    assert results2["options"][0]["percent"] == 100
    assert results2["options"][1]["count"] == 0

    text = polls.format_poll_results(conn, poll_id)
    assert "Чи переносимо консультацію на" in text
    assert "Іванов Іван" in text
    assert "Петренко Петро" in text
    assert "👀 Публічне" in text


def test_anonymous_poll_state_aggregation(conn):
    poll_id = polls.create_poll(
        conn,
        actor_id=100,
        question="Анонімне опитування щодо формату здачі",
        options=["Онлайн", "Офлайн"],
        is_anonymous=True,
        allows_multiple=False,
        target="private",
    )

    polls.register_poll_instance(conn, poll_id, chat_id=101, message_id=6001, tg_poll_id="anon_poll_1")
    polls.register_poll_instance(conn, poll_id, chat_id=102, message_id=6002, tg_poll_id="anon_poll_2")
    polls.register_poll_instance(conn, poll_id, chat_id=103, message_id=6003, tg_poll_id="anon_poll_3")

    # Simulate Telegram Poll update on 3 private chat instances
    polls.record_poll_state_update(conn, "anon_poll_1", [1, 0])
    polls.record_poll_state_update(conn, "anon_poll_2", [1, 0])
    polls.record_poll_state_update(conn, "anon_poll_3", [0, 1])

    results = polls.get_poll_results(conn, poll_id)
    assert results["total_voters"] == 3
    assert results["options"][0]["count"] == 2
    assert results["options"][1]["count"] == 1


@pytest.mark.asyncio
async def test_expired_poll_auto_closes(conn, make_user):
    make_user(101, "Іванов Іван")
    past_str = (datetime.now(UK_TZ) - timedelta(minutes=5)).isoformat()
    poll_id = polls.create_poll(
        conn,
        actor_id=100,
        question="Швидке опитування з таймером",
        options=["Варіант А", "Варіант Б"],
        is_anonymous=False,
        target="private",
        closes_at=past_str,
    )

    bot = MagicMock()
    bot.stop_poll = AsyncMock(return_value=None)
    bot.send_message = AsyncMock()

    closed_count = await polls.check_expired_polls(bot, conn)
    assert closed_count == 1

    poll = polls.get_poll(conn, poll_id)
    assert poll["status"] == "closed"
    assert bot.send_message.called


def test_global_statistics_and_full_csv(conn, make_user):
    subjects = load_subjects(config.SUBJECTS_PATH)
    make_user(201, "Коваленко Марія")
    make_user(202, "Бондаренко Олег")

    s1 = queues.create_session(conn, 100, 0, 0)
    queues.join_queue(conn, 201, s1)
    queues.join_queue(conn, 202, s1)
    queues.finish_own_entry(conn, 201, s1, passed=True)
    queues.finish_own_entry(conn, 202, s1, passed=False)
    queues.finish_own_entry(conn, 202, s1, passed=True)

    stats = queues.get_global_statistics(conn, subjects)
    assert stats["registered_users"] == 2
    assert stats["total_sessions"] == 1
    assert stats["total_passed"] == 2
    assert stats["pass_rate"] == 100
    assert len(stats["top_students"]) == 2

    csv_bytes = queues.export_global_statistics_csv(conn, subjects)
    csv_text = csv_bytes.decode("utf-8-sig")
    lines = [line for line in csv_text.splitlines() if line.strip()]
    assert "Прізвище та ім'я;Усього здано" in lines[0]
    assert len(lines) == 3  # header + 2 students


def test_new_grouped_keyboards(conn):
    student_kb = get_reply_menu(conn, 999)
    student_texts = [btn.text for row in student_kb.keyboard for btn in row]
    assert "🗳 Опитування" in student_texts
    assert "📋 Усі черги" in student_texts
    assert "❓ Допомога" in student_texts

    admin_kb = get_admin_reply_menu()
    admin_texts = [btn.text for row in admin_kb.keyboard for btn in row]
    assert "📋 Черги" in admin_texts
    assert "🎓 Студенти в чергах" in admin_texts
    assert "🗳 Опитування" in admin_texts
    assert "📊 Звіти та статистика" in admin_texts
    assert "📅 Розклад і заміни" in admin_texts
    assert "👥 База користувачів" in admin_texts
    assert "⚙️ Налаштування" in admin_texts

    polls_kb = get_admin_polls_menu()
    polls_texts = [btn.text for row in polls_kb.keyboard for btn in row]
    assert "➕ Створити опитування" in polls_texts
    assert "📡 Активні опитування" in polls_texts
    assert "📦 Історія опитувань" in polls_texts

    reports_kb = get_admin_reports_menu()
    reports_texts = [btn.text for row in reports_kb.keyboard for btn in row]
    assert "📈 Загальна статистика" in reports_texts
    assert "📚 Звіти за предметами" in reports_texts
    assert "📥 Експорт зведеної таблиці (CSV)" in reports_texts
