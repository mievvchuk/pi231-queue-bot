"""Вибір підгрупи та одноразове запрошення для наявних студентів."""
import asyncio
import logging

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

PROMPT = "👥 Оберіть свою підгрупу. Бот збереже її у вашому профілі:"
log = logging.getLogger(__name__)


def subgroup_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="1 підгрупа", callback_data="subgroup:1"),
        InlineKeyboardButton(text="2 підгрупа", callback_data="subgroup:2"),
    ]])


async def prompt_missing_subgroups(bot, conn):
    """Після запуску просить вибір один раз; невдалі доставки повторює при наступному запуску."""
    rows = conn.execute(
        "SELECT telegram_id FROM users WHERE subgroup IS NULL AND subgroup_prompted = 0 "
        "AND telegram_id > 0 AND NOT is_blocked"
    ).fetchall()
    for row in rows:
        telegram_id = row["telegram_id"]
        # Студент міг уже вибрати підгрупу, поки надсилали іншим.
        user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if not user or user["subgroup"] is not None or user["is_blocked"]:
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
        await asyncio.sleep(0.05)
