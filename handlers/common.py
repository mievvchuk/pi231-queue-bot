import logging
from contextlib import suppress

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from services import admins
from services.errors import BotError
from subjects import find_subject, find_teacher

log = logging.getLogger(__name__)

STATUS_TEXT = {"open": "запис відкрито", "closed": "запис закрито", "archived": "архів"}


def make_keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """rows: [[(текст кнопки, callback_data), ...], ...]"""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows]
    )


def get_reply_menu(conn, telegram_id: int) -> ReplyKeyboardMarkup:
    """Нижня постійна клавіатура (ReplyKeyboard) під полем вводу."""
    keyboard = [
        [KeyboardButton(text="📋 Стати в чергу"), KeyboardButton(text="➕ Відкрити чергу")],
        [KeyboardButton(text="👥 Мої черги"), KeyboardButton(text="🔍 Переглянути черги")],
        [KeyboardButton(text="🏠 Головне меню"), KeyboardButton(text="❓ Допомога")],
    ]
    if admins.is_admin(conn, telegram_id):
        keyboard.append([KeyboardButton(text="⚡ Адмін-панель")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)



def session_names(subjects: list[dict], session) -> tuple[str, str]:
    subject = find_subject(subjects, session["subject_id"])
    teacher = find_teacher(subject, session["teacher_id"]) if subject else None
    return (subject["name"] if subject else "?", teacher["name"] if teacher else "?")


def queue_label(subjects: list[dict], session) -> str:
    subject, teacher = session_names(subjects, session)
    lock = "" if session["status"] == "open" else " 🔒"
    return f"{subject} — {teacher}{lock}"


def format_queue(subjects: list[dict], session, entries, my_telegram_id: int | None = None, show_id: bool = False) -> str:
    subject, teacher = session_names(subjects, session)
    lines = [subject, teacher]
    if session["lesson_title"]:
        lines.append(f"Заняття: {session['lesson_title']}")
    lines.append(f"Статус: {STATUS_TEXT[session['status']]}")
    lines.append("")
    for row in entries:
        lines.append(format_entry(row, my_telegram_id, show_id=show_id))
    if not entries:
        lines.append("Черга порожня.")
    return "\n".join(lines)


def format_entry(row, my_telegram_id: int | None = None, marked: bool = False, show_id: bool = False) -> str:
    name = row["full_name"] or "— вільне місце —"
    is_me = my_telegram_id is not None and row["telegram_id"] == my_telegram_id
    prefix = "👉 " if is_me or marked else ""
    suffix = " (ви)" if is_me else ""
    id_part = ""
    if show_id and row["telegram_id"] and row["telegram_id"] > 0:
        id_part = f" [ID: {row['telegram_id']}]"
    return f"{prefix}{row['position']}. {name}{suffix}{id_part}"



async def show(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None):
    """Редагує повідомлення з кнопкою; якщо воно застаріле — надсилає нове."""
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as error:
            if "message is not modified" in str(error):
                return
    await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)


class Guard(BaseMiddleware):
    """Підтверджує натискання кнопки та перетворює помилки на повідомлення."""

    async def __call__(self, handler, event, data):
        if isinstance(event, CallbackQuery):
            with suppress(TelegramBadRequest):  # прострочена кнопка — не страшно
                await event.answer()
        try:
            return await handler(event, data)
        except BotError as error:
            await self.notify(event, str(error))
        except TelegramAPIError:
            log.exception("Telegram API error")

    @staticmethod
    async def notify(event, text: str) -> None:
        try:
            await event.bot.send_message(event.from_user.id, text)
        except TelegramAPIError:
            log.exception("Cannot deliver error message")
