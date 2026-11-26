"""Вибір підгрупи та одноразове запрошення для наявних студентів."""
import asyncio
import logging

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from queue_bot.services.restrictions import restriction_message
from queue_bot.services import users
from queue_bot.services.errors import BotError

PROMPT = "👥 Оберіть свою підгрупу. Бот збереже її у вашому профілі:"
log = logging.getLogger(__name__)

# Stable catalog IDs: КПЗ (5), Щуцький (3), Кондратюк (2).
# Course work (7) and every other subject remain shared, for both queue types.
KPZ_TEACHER_SUBGROUPS = {3: 1, 2: 2}


def queue_subgroup(session):
    return KPZ_TEACHER_SUBGROUPS.get(session['teacher_id']) if session['subject_id'] == 5 else None


def available_to(session, subgroup):
    target = queue_subgroup(session)
    return target is None or target == subgroup


def filter_sessions(conn, telegram_id, sessions):
    user = users.get_user(conn, telegram_id)
    subgroup = user['subgroup'] if user else None
    return [session for session in sessions if available_to(session, subgroup)]


def require_queue_access(conn, telegram_id, session):
    if queue_subgroup(session) is None:
        return
    user = users.get_user(conn, telegram_id)
    subgroup = user['subgroup'] if user else None
    if not available_to(session, subgroup):
        if subgroup is None:
            raise BotError('Спочатку оберіть свою підгрупу в боті: /subgroup')
        raise BotError('Ця черга КПЗ призначена для іншої підгрупи.')


def subgroup_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="1 підгрупа", callback_data="subgroup:1"),
        InlineKeyboardButton(text="2 підгрупа", callback_data="subgroup:2"),
    ]])


async def prompt_missing_subgroups(bot, conn):
    """Після запуску просить вибір один раз; невдалі доставки повторює при наступному запуску."""
    rows = conn.execute(
        "SELECT telegram_id FROM users WHERE subgroup IS NULL AND subgroup_prompted = 0 "
        "AND telegram_id > 0 AND NOT is_blocked AND deleted_at IS NULL"
    ).fetchall()
    delivered = 0
    for row in rows:
        telegram_id = row["telegram_id"]
        # Студент міг уже вибрати підгрупу, поки надсилали іншим.
        user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if not user or user["subgroup"] is not None or user["deleted_at"] or restriction_message(conn, telegram_id):
            continue
        try:
            await bot.send_message(telegram_id, PROMPT, reply_markup=subgroup_keyboard())
        except TelegramForbiddenError:
            continue
        except TelegramRetryAfter as error:
            await asyncio.sleep(error.retry_after)
            continue
        except TelegramAPIError:
            log.warning("Не вдалося надіслати вибір підгрупи користувачу %s", telegram_id)
            continue
        conn.execute("UPDATE users SET subgroup_prompted = 1 WHERE telegram_id = ?", (telegram_id,))
        delivered += 1
        await asyncio.sleep(0.05)
    log.info("Subgroup selection prompts delivered: %s", delivered)
