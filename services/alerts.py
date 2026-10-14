import asyncio
import logging
from contextlib import suppress
from datetime import datetime, time, timedelta, timezone

import aiohttp
from aiogram import Bot

import db
from services import admins, queues

log = logging.getLogger(__name__)

API_URL = "https://api.alerts.in.ua/v1/alerts/active.json"

try:
    import zoneinfo
    KYIV_TZ = zoneinfo.ZoneInfo("Europe/Kyiv")
    _ = datetime.now(KYIV_TZ)
except Exception:
    KYIV_TZ = timezone(timedelta(hours=3))

STUDY_START = time(8, 0)
STUDY_END = time(16, 40)

IS_KHMELNYTSKYI_ALERT_ACTIVE: bool = False


def is_study_hours() -> bool:
    """Перевіряє, чи поточний час у Києві в межах навчальних годин (08:00 - 16:40)."""
    now = datetime.now(KYIV_TZ).time()
    return STUDY_START <= now <= STUDY_END


def get_alert_status() -> bool:
    """Повертає True, якщо зараз навчальний час (08:00 - 16:40) і у Хмельницькому зараз повітряна тривога."""
    return is_study_hours() and IS_KHMELNYTSKYI_ALERT_ACTIVE


def is_khmelnytskyi_alert(alerts: list[dict]) -> bool:
    """Визначає, чи є тривога в місті Хмельницький, Хмельницькій громаді або області."""
    for a in alerts:
        if a.get("alert_type") != "air_raid":
            continue
        title = (a.get("location_title") or "").lower()
        loc_type = (a.get("location_type") or "").lower()
        oblast = (a.get("location_oblast") or "").lower()

        # 1. Тривога по всій Хмельницькій області
        if loc_type == "oblast" and ("хмельницьк" in title or "хмельницьк" in oblast):
            return True

        # 2. Тривога в м. Хмельницький / Хмельницькій громаді / районі
        if "хмельницьк" in title:
            return True

    return False


async def fetch_active_alerts(token: str) -> list[dict] | None:
    """Отримує список активних тривог через API alerts.in.ua."""
    headers = {"Authorization": f"Bearer {token}"}
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(API_URL, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("alerts", [])
                log.warning(f"alerts.in.ua API returned status {resp.status}")
    except Exception as e:
        log.warning(f"Error fetching alerts.in.ua: {e}")
    return None


async def broadcast_alert_status(bot: Bot, db_target: str, is_active: bool):
    """Розсилає сповіщення всім студентам активних черг та адмінам."""
    if not db_target:
        return

    try:
        conn = db.connect(db_target)
    except Exception as e:
        log.error(f"Cannot connect to db in alert broadcast: {e}")
        return

    try:
        active_sessions = queues.list_sessions(conn, ("open", "closed"))
        target_user_ids = set()

        for s in active_sessions:
            entries = queues.get_entries(conn, s["id"])
            for e in entries:
                if e.get("telegram_id") and e["telegram_id"] > 0:
                    target_user_ids.add(e["telegram_id"])

        # Додаємо адмінів, щоб вони завжди були в курсі
        for adm in admins.list_admins(conn):
            target_user_ids.add(adm["telegram_id"])
    finally:
        with suppress(Exception):
            conn.close()

    if not target_user_ids:
        return

    if is_active:
        text = (
            "🚨 **УВАГА! Повітряна тривога в м. Хмельницький!**\n\n"
            "Заняття призупинено. Чергу автоматично зафіксовано та збережено.\n"
            "Ваше місце не втрачено й залишається за вами до наступної пари!\n\n"
            "Пройдіть у найближче безпечне місце або укриття! 🛡"
        )
    else:
        text = (
            "🟢 **ВІДБІЙ повітряної тривоги в м. Хмельницький!**\n\n"
            "Черги та всі позиції студентів збережено.\n"
            "Якщо заняття продовжується — можна продовжити здачу, або черга чекатиме на наступну пару."
        )

    for uid in target_user_ids:
        with suppress(Exception):
            await bot.send_message(uid, text)
            await asyncio.sleep(0.05)  # плавне надсилання без блокування rate-limit


async def start_alerts_checker(bot: Bot, db_target: str, token: str, interval_sec: int = 30):
    """Фонова задача перевірки повітряної тривоги по Хмельницькому."""
    global IS_KHMELNYTSKYI_ALERT_ACTIVE
    log.info("Starting Khmelnytskyi Air Raid Alert checker...")

    while True:
        try:
            if not is_study_hours():
                # Поза навчальними годинами (з 16:40 до 08:00) моніторинг не тривожить студентів
                if IS_KHMELNYTSKYI_ALERT_ACTIVE:
                    IS_KHMELNYTSKYI_ALERT_ACTIVE = False
                await asyncio.sleep(interval_sec)
                continue

            alerts = await fetch_active_alerts(token)
            if alerts is not None:
                new_status = is_khmelnytskyi_alert(alerts)
                if new_status != IS_KHMELNYTSKYI_ALERT_ACTIVE:
                    IS_KHMELNYTSKYI_ALERT_ACTIVE = new_status
                    log.info(f"Khmelnytskyi alert status changed to: {new_status}")
                    await broadcast_alert_status(bot, db_target, new_status)
        except Exception as e:
            log.exception(f"Unexpected error in alerts checker: {e}")

        await asyncio.sleep(interval_sec)
