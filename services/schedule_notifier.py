import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot

import config
from services import schedule, updates

log = logging.getLogger(__name__)
UK_TZ = ZoneInfo(config.TIMEZONE)


def has_sent_today(conn, today_key: str) -> bool:
    try:
        row = conn.execute("SELECT value FROM bot_state WHERE key = 'last_schedule_notification'").fetchone()
        if row and row["value"] == today_key:
            return True
    except Exception as e:
        log.warning(f"Error checking schedule notification state: {e}")
    return False


def mark_sent_today(conn, today_key: str) -> None:
    try:
        conn.execute(
            """INSERT INTO bot_state (key, value, updated_at)
               VALUES ('last_schedule_notification', ?, CURRENT_TIMESTAMP)
               ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            (today_key,),
        )
    except Exception as e:
        log.warning(f"Error saving schedule notification state: {e}")


async def start_schedule_notifier(bot: Bot, conn, interval_sec: int = 60) -> None:
    """Фонова задача для щоденної ранкової розсилки актуального розкладу."""
    log.info(f"Schedule daily notifier started (target time: {config.NOTIFICATION_TIME} {config.TIMEZONE})")
    target_hour, target_minute = map(int, config.NOTIFICATION_TIME.split(":"))

    while True:
        try:
            now = datetime.now(UK_TZ)
            today_key = now.strftime("%Y-%m-%d")

            # Перевіряємо, чи настав час розсилки і чи не надсилали сьогодні
            if now.hour == target_hour and now.minute >= target_minute and not has_sent_today(conn, today_key):
                sch_data = schedule.load_schedule_data(config.SCHEDULE_PATH)
                today_dt = now.date()
                lessons, reps = schedule.get_actual_schedule(conn, sch_data, today_dt, config.STUDENT_GROUP)

                if lessons:
                    text = (
                        f"🌅 <b>Доброго ранку! Розклад на сьогодні:</b>\n\n"
                        f"{schedule.format_schedule_for_date(conn, sch_data, today_dt, config.STUDENT_GROUP)}"
                    )
                    uids = updates.get_active_user_ids(conn)
                    log.info(f"Надсилаю ранковий розклад для {len(uids)} користувачів...")
                    await updates.broadcast_message_to_users(bot, uids, text)

                mark_sent_today(conn, today_key)

        except Exception as e:
            log.exception(f"Error in schedule notifier loop: {e}")

        await asyncio.sleep(interval_sec)
