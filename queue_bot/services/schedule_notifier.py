import asyncio
import html
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from queue_bot import config
from queue_bot.services import schedule, updates

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


async def notify_remaining_pairs(bot, conn, *, now=None) -> None:
    plans = schedule.get_remaining_pairs(conn, now=now, project_finish=False)
    due = []
    for key, plan in plans.items():
        count = plan["remaining"]
        if count in (1, 2):
            marker = f"subject_pairs_notice:{key}:{plan['saved_at']}:{count}"
            legacy_marker = f"subject_pairs_notice:{key}:{plan['end_date']}:{count}"
            if conn.execute("SELECT 1 FROM bot_state WHERE key IN (?, ?)", (marker, legacy_marker)).fetchone() is None:
                due.append((marker, plan))
    if not due:
        return

    recipients = updates.get_active_user_ids(conn)
    if not recipients:
        return

    lines = []
    for marker, plan in due:
        claimed = conn.execute(
            "INSERT INTO bot_state (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING RETURNING key",
            (marker, "sent"),
        ).fetchone()
        if claimed:
            count = plan["remaining"]
            name = html.escape(plan["subject_name"])
            lines.append(f"{name} — <b>{count} {'пара' if count == 1 else 'пари'}</b>")
    if lines:
        await updates.broadcast_message_to_users(
            bot, recipients, "📅 <b>До завершення предметів залишилось</b>\n" + "\n".join(lines),
        )


async def start_schedule_notifier(bot: Bot, conn, interval_sec: int = 60) -> None:
    """Фонова задача для щоденної розсилки о 17:00 актуального розкладу на наступний день."""
    log.info(f"Schedule daily notifier started (target time: {config.NOTIFICATION_TIME} {config.TIMEZONE})")
    target_hour, target_minute = map(int, config.NOTIFICATION_TIME.split(":"))

    while True:
        try:
            now = datetime.now(UK_TZ)
            await notify_remaining_pairs(bot, conn, now=now)
            today_key = f"evening_{now.strftime('%Y-%m-%d')}"

            # Надсилаємо у вечірній час (починаючи з 17:00 і до 22:00), якщо ще не надсилали вечірнє сповіщення сьогодні
            is_time_to_send = (
                (now.hour == target_hour and now.minute >= target_minute)
                or (target_hour < now.hour < 22)
            )

            if is_time_to_send and not has_sent_today(conn, today_key):
                sch_data = schedule.read_schedule(conn)
                tomorrow_dt = now.date() + timedelta(days=1)
                lessons, reps = schedule.get_actual_schedule(conn, sch_data, tomorrow_dt, config.STUDENT_GROUP)

                if lessons:
                    text = (
                        f"🌆 <b>Розклад занять на завтра:</b>\n\n"
                        f"{schedule.format_schedule_for_date(conn, sch_data, tomorrow_dt, config.STUDENT_GROUP)}"
                    )
                    uids = updates.get_active_user_ids(conn)
                    log.info(f"Надсилаю вечірній розклад на завтра для {len(uids)} користувачів...")
                    await updates.broadcast_message_to_users(bot, uids, text)

                mark_sent_today(conn, today_key)

        except Exception as e:
            from queue_bot.services import diagnostics
            await diagnostics.report(e, bot=bot, conn=conn, context={'source':'worker', 'operation':'schedule_notifier'})

        await asyncio.sleep(interval_sec)
