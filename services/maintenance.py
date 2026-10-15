import logging
from contextlib import suppress

from aiogram import Bot
from aiogram.types import ReplyKeyboardRemove

log = logging.getLogger(__name__)


def ensure_state_table(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )"""
    )


def is_maintenance_active(conn) -> bool:
    if not conn:
        return False
    try:
        ensure_state_table(conn)
        row = conn.execute("SELECT value FROM bot_state WHERE key = 'maintenance_mode'").fetchone()
        if row is not None:
            return row["value"] == "1"
    except Exception as e:
        log.warning(f"Error checking maintenance mode: {e}")
    return False


def get_maintenance_end_time(conn) -> str:
    if not conn:
        return "20:15"
    try:
        ensure_state_table(conn)
        row = conn.execute("SELECT value FROM bot_state WHERE key = 'maintenance_end_time'").fetchone()
        if row and row["value"]:
            return row["value"]
    except Exception as e:
        log.warning(f"Error reading maintenance end time: {e}")
    return "20:15"


def set_maintenance_mode(conn, active: bool, end_time: str = "20:15") -> None:
    ensure_state_table(conn)
    val = "1" if active else "0"
    conn.execute(
        """INSERT INTO bot_state (key, value, updated_at)
           VALUES ('maintenance_mode', ?, CURRENT_TIMESTAMP)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
        (val,),
    )
    if end_time:
        conn.execute(
            """INSERT INTO bot_state (key, value, updated_at)
               VALUES ('maintenance_end_time', ?, CURRENT_TIMESTAMP)
               ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            (end_time,),
        )


def get_maintenance_message(end_time: str = "20:15") -> str:
    return (
        "🛠 <b>Технічні роботи</b>\n\n"
        "Шановні студенти! Бот тимчасово призупинено на проведення технічних робіт.\n\n"
        "⚠️ <b>Запис до черг та кнопки тимчасово деактивовано.</b>\n"
        "Всі ваші збережені місця в чергах залишаються без змін!\n\n"
        f"🕒 <b>Орієнтовний час завершення:</b> <b>{end_time}</b>\n\n"
        "Дякуємо за розуміння та терпіння! 🙏"
    )


async def broadcast_maintenance_notice(bot: Bot, conn, end_time: str = "20:15") -> tuple[int, int]:
    """Розсилає повідомлення про техроботи всім активним користувачам та деактивує їхні кнопки."""
    from services.updates import get_active_user_ids

    uids = get_active_user_ids(conn)
    text = get_maintenance_message(end_time)
    success = 0
    errors = 0

    for uid in uids:
        try:
            await bot.send_message(uid, text, reply_markup=ReplyKeyboardRemove())
            success += 1
        except Exception as e:
            log.warning(f"Не вдалося надіслати сповіщення про техроботи {uid}: {e}")
            errors += 1
    return success, errors
