import logging
import re
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

from services import admins, queues
from services.errors import BotError
from subjects import find_subject, find_teacher

log = logging.getLogger(__name__)

STATUS_TEXT = {"open": "запис відкрито", "closed": "запис закрито", "archived": "архів"}


def make_keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """rows: [[(текст кнопки, callback_data), ...], ...]"""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows]
    )


def make_reply_keyboard(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    """Створює нижню клавіатуру зі списку рядків кнопок."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
    )


def get_reply_menu(conn, telegram_id: int) -> ReplyKeyboardMarkup:
    """Нижня постійна клавіатура (ReplyKeyboard) під полем вводу."""
    keyboard = [
        [KeyboardButton(text="📋 Стати в чергу"), KeyboardButton(text="➕ Відкрити чергу")],
        [KeyboardButton(text="👥 Мої черги"), KeyboardButton(text="🔍 Переглянути черги")],
        [KeyboardButton(text="❓ Допомога")],
    ]
    if admins.is_admin(conn, telegram_id):
        keyboard.append([KeyboardButton(text="⚡ Адмін-панель")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_admin_reply_menu() -> ReplyKeyboardMarkup:
    """Нижня постійна клавіатура адмін-панелі під полем вводу."""
    keyboard = [
        [KeyboardButton(text="➕ Створити чергу"), KeyboardButton(text="📝 Додати студента")],
        [KeyboardButton(text="👀 Активні черги"), KeyboardButton(text="🔢 Змінити порядок")],
        [KeyboardButton(text="✅ Позначити здачу"), KeyboardButton(text="🔒 Відкрити/закрити запис")],
        [KeyboardButton(text="📦 Історія черг"), KeyboardButton(text="⛔ Заблоковані")],
        [KeyboardButton(text="👥 Адміністратори"), KeyboardButton(text="🏠 Головне меню")],
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_queues_reply_menu(sessions: list, subjects: list[dict], back_text: str = "🏠 Головне меню") -> ReplyKeyboardMarkup:
    """Нижня клавіатура зі списком черг."""
    rows = []
    for s in sessions:
        rows.append([f"📋 {queue_label(subjects, s)}"])
    rows.append(["🔄 Оновити список"])
    rows.append([back_text])
    return make_reply_keyboard(rows)


def get_queue_view_reply_menu(conn, session_id: int, telegram_id: int, is_open: bool = True) -> ReplyKeyboardMarkup:
    """Нижня клавіатура під час перегляду черги."""
    is_in_queue = queues.get_user_position(conn, telegram_id, session_id) is not None
    is_adm = admins.is_admin(conn, telegram_id)
    rows = []
    if is_in_queue:
        rows.append(["🔄 Помінятися місцем", "📍 Моє місце"])
        rows.append(["✅ Здав", "❌ Не здав"])
        rows.append(["🚪 Покинути чергу", "🔄 Оновити"])
    else:
        if is_open:
            rows.append(["📝 Записатися в чергу", "🔄 Оновити"])
        else:
            rows.append(["🔄 Оновити"])

    if is_adm:
        rows.append(["⚡ Керувати цією чергою"])

    rows.append(["🔙 До списку черг", "🏠 Головне меню"])
    return make_reply_keyboard(rows)


def normalize_text(text: str) -> str:
    """Нормалізує текст для надійного порівняння назв черг та викладачів."""
    t = text.lower()
    for ch in ("📋", "🔒", "⚡", "📦", "👤", "✅", "❌", "👉"):
        t = t.replace(ch, "")
    t = t.replace("є", "е").replace("ё", "е").replace("і", "i").replace("ї", "i").replace("и", "i")
    t = t.replace("—", "-").replace("–", "-").replace("−", "-")
    t = re.sub(r"[^\w\s\-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def find_session_by_label(conn, subjects: list[dict], text: str):
    """Знаходить сесію черги за текстом кнопки або назвою з гнучкою нормалізацією."""
    norm_input = normalize_text(text)
    if not norm_input:
        return None
    sessions = queues.list_sessions(conn, ("open", "closed", "archived"))

    # 1. Точний збіг нормалізованого підпису
    for s in sessions:
        lbl = queue_label(subjects, s)
        norm_lbl = normalize_text(lbl)
        if norm_input == norm_lbl or norm_input.startswith(norm_lbl) or norm_lbl.startswith(norm_input):
            return s

    # 2. Пошук за прізвищем викладача та предметом
    for s in sessions:
        sub_name, teacher_name = session_names(subjects, s)
        t_norm = normalize_text(teacher_name)
        s_norm = normalize_text(sub_name)

        t_surname = t_norm.split()[0] if t_norm else ""
        if t_surname and t_surname in norm_input:
            sub_words = [w for w in s_norm.split() if len(w) > 3]
            if not sub_words or any(w in norm_input for w in sub_words):
                return s

    # 3. Резервний пошук за назвою предмета
    for s in sessions:
        sub_name, _ = session_names(subjects, s)
        s_norm = normalize_text(sub_name)
        if s_norm and (s_norm in norm_input or norm_input in s_norm):
            return s

    return None



def session_names(subjects: list[dict], session) -> tuple[str, str]:
    subject = find_subject(subjects, session["subject_id"])
    teacher = find_teacher(subject, session["teacher_id"]) if subject else None
    return (subject["name"] if subject else "?", teacher["name"] if teacher else "?")


def queue_label(subjects: list[dict], session) -> str:
    subject, teacher = session_names(subjects, session)
    lock = "" if session["status"] == "open" else " 🔒"
    return f"{subject} — {teacher}{lock}"


def format_queue(subjects: list[dict], session, entries, my_telegram_id: int | None = None, show_id: bool = False) -> str:
    from services.alerts import get_alert_status

    lines = []
    if get_alert_status():
        lines.append("🚨 ПОВІТРЯНА ТРИВОГА У М. ХМЕЛЬНИЦЬКИЙ!")
        lines.append("Заняття призупинено, чергу збережено до наступної пари.")
        lines.append("")

    subject, teacher = session_names(subjects, session)
    lines.extend([subject, teacher])
    if session["lesson_title"]:
        lines.append(f"Заняття: {session['lesson_title']}")
    status_str = "🚨 тривога (черга збережена)" if get_alert_status() else STATUS_TEXT[session['status']]
    lines.append(f"Статус: {status_str}")
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
