from unittest.mock import AsyncMock, MagicMock

import pytest

import db
from services import queues, users
from services.errors import BotError
from services.subgroups import prompt_missing_subgroups


@pytest.mark.parametrize("status", ["open", "closed", "archived"])
def test_delete_queue_removes_all_entries_only_for_that_queue(conn, make_user, status):
    make_user(1, "Петренко Іван")
    first = queues.create_session(conn, 100, 1, 1)
    other = queues.create_session(conn, 100, 2, 2)
    queues.join_queue(conn, 1, first)
    queues.join_queue(conn, 1, other)
    conn.execute("UPDATE queue_sessions SET status = ? WHERE id = ?", (status, first))
    conn.execute("UPDATE queue_entries SET status = 'completed' WHERE session_id = ?", (first,))
    queues.delete_session(conn, 100, first)
    assert queues.get_session(conn, first) is None
    assert not conn.execute("SELECT * FROM queue_entries WHERE session_id = ?", (first,)).fetchall()
    assert queues.get_user_position(conn, 1, other) == 1
    assert users.get_user(conn, 1)
    assert conn.execute("SELECT * FROM admin_log WHERE action = 'delete_session'").fetchone()


def test_delete_queue_requires_admin_and_rolls_back_on_audit_failure(conn, make_user):
    make_user(1, "Петренко Іван")
    session = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, session)
    with pytest.raises(BotError):
        queues.delete_session(conn, 1, session)
    conn.execute("CREATE TRIGGER fail_delete_log BEFORE INSERT ON admin_log "
                 "WHEN NEW.action = 'delete_session' BEGIN SELECT RAISE(ABORT, 'audit failure'); END")
    with pytest.raises(Exception, match="audit failure"):
        queues.delete_session(conn, 100, session)
    assert queues.get_session(conn, session)
    assert queues.get_user_position(conn, 1, session) == 1


def test_subgroup_persists_and_validates(conn, db_path, make_user):
    make_user(1, "Петренко Іван")
    assert users.get_user(conn, 1)["subgroup"] is None
    for invalid in [0, 3, "1", True, None]:
        with pytest.raises(BotError):
            users.set_subgroup(conn, 1, invalid)
    users.set_subgroup(conn, 1, 2)
    reopened = db.connect(db_path)
    assert users.get_user(reopened, 1)["subgroup"] == 2
    reopened.close()
    users.set_subgroup(conn, 1, 1)
    assert users.get_user(conn, 1)["subgroup"] == 1
    with pytest.raises(BotError):
        users.set_subgroup(conn, 999, 1)
    users.toggle_block_user(conn, users.get_user(conn, 1)["id"])
    with pytest.raises(BotError):
        users.set_subgroup(conn, 1, 2)


@pytest.mark.asyncio
async def test_startup_prompts_only_missing_subgroups_once(conn, make_user):
    for tid, name in [(1, "Петренко Іван"), (2, "Коваль Андрій"), (3, "Бойко Олег")]:
        make_user(tid, name)
    users.set_subgroup(conn, 2, 1)
    users.toggle_block_user(conn, users.get_user(conn, 3)["id"])
    users.get_or_create_user_by_name(conn, "Мельник Олена")
    bot = AsyncMock()
    await prompt_missing_subgroups(bot, conn)
    await prompt_missing_subgroups(bot, conn)
    bot.send_message.assert_awaited_once()
    assert bot.send_message.call_args.args[0] == 1


@pytest.mark.asyncio
async def test_guard_requires_subgroup_on_old_buttons_and_allows_choice(conn, make_user):
    from aiogram.types import CallbackQuery, Chat, Message, User
    from datetime import datetime
    from handlers.common import Guard

    make_user(1, "Петренко Іван")
    message = Message(message_id=1, date=datetime.now(), chat=Chat(id=1, type="private"),
                      from_user=User(id=1, is_bot=False, first_name="Іван"), text="📋 Усі черги")
    bot = AsyncMock()
    message = message.as_(bot)
    handler = AsyncMock()
    await Guard()(handler, message, {"conn": conn})
    handler.assert_not_awaited()
    assert "підгрупу" in bot.send_message.call_args.args[1]
    callback = CallbackQuery(id="test", from_user=message.from_user, chat_instance="1",
                             message=message, data="subgroup:2").as_(bot)
    await Guard()(handler, callback, {"conn": conn})
    handler.assert_awaited_once()
    handler.reset_mock()
    users.set_subgroup(conn, 1, 2)
    await Guard()(handler, message, {"conn": conn})
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_confirmation_clears_state(conn, make_user):
    from handlers.admin import confirm_delete_queue

    session = queues.create_session(conn, 100, 1, 1)
    message = MagicMock()
    message.from_user.id = 100
    message.answer = AsyncMock()
    state = AsyncMock()
    state.get_data.return_value = {"session_id": session}
    await confirm_delete_queue(message, state, conn)
    assert queues.get_session(conn, session) is None
    state.clear.assert_awaited_once()


def test_migration_preserves_existing_users_and_queues(tmp_path):
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "legacy.db"), isolation_level=None)
    conn.row_factory = sqlite3.Row
    for script in db.MIGRATIONS[:-1]:
        conn.executescript(script)
    conn.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) - 1}")
    conn.execute("INSERT INTO users (telegram_id, full_name) VALUES (1, 'Петренко Іван')")
    conn.execute("INSERT INTO queue_sessions (subject_id, teacher_id) VALUES (1, 1)")
    db.migrate_sqlite(conn)
    db.migrate_sqlite(conn)
    assert users.get_user(conn, 1)["full_name"] == "Петренко Іван"
    assert users.get_user(conn, 1)["subgroup"] is None
    assert queues.get_session(conn, 1)
    conn.close()


@pytest.mark.asyncio
async def test_failed_prompt_delivery_can_retry(conn, make_user):
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import SendMessage

    make_user(1, "Петренко Іван")
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramNetworkError(
        method=SendMessage(chat_id=1, text="test"), message="offline")
    await prompt_missing_subgroups(bot, conn)
    assert users.get_user(conn, 1)["subgroup_prompted"] == 0
    bot.send_message.side_effect = None
    await prompt_missing_subgroups(bot, conn)
    assert users.get_user(conn, 1)["subgroup_prompted"] == 1
