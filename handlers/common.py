import html
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
    """Головне меню студента."""
    keyboard = [
        [KeyboardButton(text="📋 Усі черги"), KeyboardButton(text="👥 Мої черги")],
        [KeyboardButton(text="📅 Розклад"), KeyboardButton(text="🔄 Заміни")],
        [KeyboardButton(text="➕ Відкрити чергу"), KeyboardButton(text="❓ Допомога")],
    ]
    if admins.is_admin(conn, telegram_id):
        keyboard.append([KeyboardButton(text="⚡ Адмін-панель")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_schedule_reply_menu() -> ReplyKeyboardMarkup:
    """Меню розділу «Розклад»."""
    return make_reply_keyboard([
        ["📅 Сьогодні", "➡️ Завтра"],
        ["🗓 Весь тиждень", "🔄 Активні заміни"],
        ["🏠 Головне меню"],
    ])


def get_queue_view_reply_menu() -> ReplyKeyboardMarkup:
    """Основні кнопки екрану перегляду черги: рівно 4 кнопки."""
    return make_reply_keyboard([
        ["📍 Моє місце", "⚙️ Дії"],
        ["🔄 Оновити", "🔙 До черг"],
    ])


def get_queue_actions_reply_menu(
    is_in_queue: bool,
    is_open: bool = True,
    can_pick_number: bool = True,
    swap_wish: str | None = None,
    ready_to_swap: bool = False,
) -> ReplyKeyboardMarkup:
    """Меню «Дії» залежно від статусу запису студента."""
    if is_in_queue:
        if swap_wish == "higher":
            btn_higher = "⏸ Зняти «Хочу вище»"
            btn_lower = "⬇️ Хочу нижче"
        elif swap_wish == "lower":
            btn_higher = "⬆️ Хочу вище"
            btn_lower = "⏸ Зняти «Хочу нижче»"
        else:
            btn_higher = "⬆️ Хочу вище"
            btn_lower = "⬇️ Хочу нижче"

        return make_reply_keyboard([
            ["✅ Здав", "❌ Не здав"],
            ["🔁 Обмінятися місцями"],
            [btn_higher, btn_lower],
            ["🚪 Покинути чергу", "🔙 Назад до черги"],
        ])
    if is_open:
        if can_pick_number:
            return make_reply_keyboard([
                ["📝 Стати в кінець", "🔢 Обрати місце"],
                ["🔙 Назад до черги"],
            ])
        return make_reply_keyboard([
            ["📝 Стати в кінець"],
            ["🔙 Назад до черги"],
        ])
    return make_reply_keyboard([["🔙 Назад до черги"]])


def get_admin_reply_menu() -> ReplyKeyboardMarkup:
    """Головне меню адмін-панелі: категорії."""
    return make_reply_keyboard([
        ["📋 Черги", "👥 Студенти"],
        ["📅 Розклад і заміни", "📊 Звіти"],
        ["👥 Користувачі", "⚙️ Налаштування"],
        ["🏠 Головне меню"],
    ])


def get_admin_schedule_menu() -> ReplyKeyboardMarkup:
    """Вкладене меню адміна: Розклад і заміни."""
    return make_reply_keyboard([
        ["📥 Отримати schedule.json", "📤 Завантажити розклад"],
        ["🏢 Робоча субота", "🗑 Очистити всі заміни"],
        ["🟢 Статус системи", "🔙 В адмін-панель"],
    ])


def get_admin_users_menu() -> ReplyKeyboardMarkup:
    """Вкладене меню адміна: Користувачі."""
    return make_reply_keyboard([
        ["📋 Список користувачів", "➕ Додати користувача"],
        ["🔍 Знайти користувача", "🔙 В адмін-панель"],
    ])


def get_user_card_menu(is_blocked: bool) -> ReplyKeyboardMarkup:
    """Клавіатура картки користувача."""
    block_btn = "🟢 Розблокувати" if is_blocked else "⛔ Заблокувати"
    return make_reply_keyboard([
        ["✏️ Змінити ПІБ", block_btn],
        ["🗑 Видалити", "🔙 До користувачів"],
    ])


def get_admin_queues_menu() -> ReplyKeyboardMarkup:
    """Вкладене меню адміна: Черги."""
    return make_reply_keyboard([
        ["👀 Активні черги", "➕ Створити чергу"],
        ["🔒 Відкрити/закрити", "📦 Історія"],
        ["🔙 Назад"],
    ])


def get_admin_students_menu() -> ReplyKeyboardMarkup:
    """Вкладене меню адміна: Студенти."""
    return make_reply_keyboard([
        ["📝 Додати студента", "🔢 Змінити порядок"],
        ["✅ Позначити здачу", "⛔ Заблоковані"],
        ["🔙 Назад"],
    ])


def get_admin_settings_menu(is_maintenance: bool = False) -> ReplyKeyboardMarkup:
    """Вкладене меню адміна: Налаштування."""
    maint_btn = "🟢 Вимкнути техроботи" if is_maintenance else "🛠 Увімкнути техроботи"
    return make_reply_keyboard([
        ["👥 Адміністратори", "⛔ Заблоковані"],
        [maint_btn, "📢 Оголошення для всіх"],
        ["🔙 Назад"],
    ])


def get_lost_context_menu() -> ReplyKeyboardMarkup:
    """Меню при втраті контексту черги."""
    return make_reply_keyboard([
        ["📋 Усі черги", "👥 Мої черги"],
        ["🏠 Головне меню"],
    ])


def paginate(items: list, page: int, per_page: int = 8) -> tuple[list, int, int]:
    """Розбиває список на сторінки (по 8 елементів)."""
    total = len(items)
    if total == 0:
        return [], 1, 1
    total_pages = (total + per_page - 1) // per_page
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    end = start + per_page
    return items[start:end], page, total_pages


def get_paginated_reply_keyboard(
    item_rows: list[list[str]],
    page: int,
    total_pages: int,
    back_text: str = "🔙 Назад",
    extra_row: list[str] | None = None,
) -> ReplyKeyboardMarkup:
    """Створює нижню клавіатуру з пагінацією (⬅️, 1/3, ➡️)."""
    rows = list(item_rows)
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append("⬅️")
        nav.append(f"{page}/{total_pages}")
        if page < total_pages:
            nav.append("➡️")
        rows.append(nav)
    if extra_row:
        rows.append(extra_row)
    rows.append([back_text])
    return make_reply_keyboard(rows)


def get_queues_reply_menu(
    sessions: list,
    subjects: list[dict],
    page: int = 1,
    back_text: str = "🏠 Головне меню",
    group_coursework: bool = True,
) -> ReplyKeyboardMarkup:
    """Нижня клавіатура зі списком черг з пагінацією по 8 штук.
    Якщо group_coursework=True, черги курсових робіт групуються в окрему кнопку."""
    item_rows = []
    if group_coursework:
        regular_sessions = []
        coursework_map = {}
        for s in sessions:
            sub_name, _ = session_names(subjects, s)
            if "курсов" in sub_name.lower():
                coursework_map.setdefault(sub_name, []).append(s)
            else:
                regular_sessions.append(s)

        for s in regular_sessions:
            item_rows.append([f"📋 {queue_label(subjects, s)}"])

        for cw_name in sorted(coursework_map.keys()):
            item_rows.append([f"📑 {cw_name}"])
    else:
        for s in sessions:
            item_rows.append([f"📋 {queue_label(subjects, s)}"])

    page_items, current_page, total_pages = paginate(item_rows, page, per_page=8)
    return get_paginated_reply_keyboard(
        page_items,
        page=current_page,
        total_pages=total_pages,
        back_text=back_text,
        extra_row=["🔄 Оновити"],
    )


def get_coursework_queues_menu(
    sessions: list,
    subjects: list[dict],
    subject_name: str,
    page: int = 1,
    back_text: str = "🔙 До списку черг",
) -> ReplyKeyboardMarkup:
    """Нижня клавіатура зі списком викладачів конкретної курсової роботи."""
    cw_sessions = []
    for s in sessions:
        sub_name, _ = session_names(subjects, s)
        if sub_name.lower() == subject_name.lower() or "курсов" in sub_name.lower():
            cw_sessions.append(s)

    item_rows = [[f"📋 {queue_label(subjects, s)}"] for s in cw_sessions]
    page_items, current_page, total_pages = paginate(item_rows, page, per_page=8)
    return get_paginated_reply_keyboard(
        page_items,
        page=current_page,
        total_pages=total_pages,
        back_text=back_text,
        extra_row=["🔄 Оновити"],
    )


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
        s_type = s["queue_type"] if "queue_type" in s.keys() else "lesson"
        is_consult = "консультац" in norm_input
        if (s_type == "consultation") != is_consult:
            continue

        lbl = queue_label(subjects, s)
        norm_lbl = normalize_text(lbl)
        if norm_input == norm_lbl or norm_input.startswith(norm_lbl) or norm_lbl.startswith(norm_input):
            return s

    # 2. Пошук за прізвищем викладача та предметом
    for s in sessions:
        s_type = s["queue_type"] if "queue_type" in s.keys() else "lesson"
        is_consult = "консультац" in norm_input
        if is_consult and s_type != "consultation":
            continue
        if not is_consult and s_type == "consultation":
            continue

        sub_name, teacher_name = session_names(subjects, s)
        t_norm = normalize_text(teacher_name)
        s_norm = normalize_text(sub_name)

        t_surname = t_norm.split()[0] if t_norm else ""
        if t_surname and t_surname in norm_input:
            sub_words = [w for w in s_norm.split() if len(w) > 3]
            if not sub_words or any(w in norm_input for w in sub_words):
                return s

    # 3. Резервний пошук за назвою предмета (тільки якщо в запиті не вказано іншого викладача)
    for s in sessions:
        s_type = s["queue_type"] if "queue_type" in s.keys() else "lesson"
        is_consult = "консультац" in norm_input
        if is_consult and s_type != "consultation":
            continue
        if not is_consult and s_type == "consultation":
            continue

        sub_name, teacher_name = session_names(subjects, s)
        s_norm = normalize_text(sub_name)
        if s_norm and (s_norm in norm_input or norm_input in s_norm):
            subject = find_subject(subjects, s["subject_id"])
            other_teachers = [t for t in subject["teachers"] if t["id"] != s["teacher_id"]] if subject else []
            has_other_teacher = any(
                normalize_text(ot["name"]).split()[0] in norm_input
                for ot in other_teachers
                if normalize_text(ot["name"]).split()
            )
            if not has_other_teacher:
                return s

    return None



def session_names(subjects: list[dict], session) -> tuple[str, str]:
    subject = find_subject(subjects, session["subject_id"])
    teacher = find_teacher(subject, session["teacher_id"]) if subject else None
    return (subject["name"] if subject else "?", teacher["name"] if teacher else "?")


def queue_label(subjects: list[dict], session) -> str:
    subject, teacher = session_names(subjects, session)
    lock = "" if session["status"] == "open" else " 🔒"
    q_type = session["queue_type"] if "queue_type" in session.keys() else "lesson"
    type_badge = " 💬 [Консультація]" if q_type == "consultation" else ""
    return f"{subject} — {teacher}{type_badge}{lock}"


def format_queue(subjects: list[dict], session, entries, my_telegram_id: int | None = None, show_id: bool = False) -> str:
    from services.alerts import get_alert_status

    lines = []
    if get_alert_status():
        lines.append("🚨 <b>Повітряна тривога</b>")
        lines.append("Заняття призупинено, чергу збережено.")
        lines.append("")

    subject, teacher = session_names(subjects, session)
    q_type = session["queue_type"] if "queue_type" in session.keys() else "lesson"
    if q_type == "consultation":
        lines.append(f"💬 <b>{html.escape(subject)} — Консультація</b>")
    else:
        lines.append(f"📋 <b>{html.escape(subject)}</b>")
    lines.append(f"👨‍🏫 Викладач: <b>{html.escape(teacher)}</b>")
    lesson_title = session["lesson_title"] if "lesson_title" in session.keys() else None
    if lesson_title:
        lines.append(f"Заняття: {html.escape(lesson_title)}")
    lines.append("")

    status = session["status"] if "status" in session.keys() else ""
    if get_alert_status():
        lines.append("🚨 <b>Тривога (черга збережена)</b>")
    elif status == "open":
        lines.append("🟢 <b>Запис відкрито</b>")
    elif status == "closed":
        lines.append("🔴 <b>Запис закрито</b>")
    elif status == "archived":
        lines.append("📦 <b>Архів</b>")
    else:
        lines.append(f"Статус: {status}")

    lines.append("")
    for row in entries:
        lines.append(format_entry(row, my_telegram_id, show_id=show_id))
    if not entries:
        lines.append("Черга порожня.")
    return "\n".join(lines)


def format_entry(row, my_telegram_id: int | None = None, marked: bool = False, show_id: bool = False) -> str:
    raw_name = row["full_name"] if "full_name" in row.keys() else None
    if raw_name:
        name = html.escape(raw_name)
    else:
        name = "Вільне місце"

    tg_id = row["telegram_id"] if "telegram_id" in row.keys() else None
    is_me = my_telegram_id is not None and tg_id == my_telegram_id
    prefix = "👉 " if (is_me or marked) else ""
    suffix = " — це ви" if is_me else ""

    swap_wish = row["swap_wish"] if "swap_wish" in row.keys() and row["swap_wish"] else None
    if swap_wish == "higher":
        swap_badge = " ⬆️ [хочу вище]"
    elif swap_wish == "lower":
        swap_badge = " ⬇️ [хочу нижче]"
    elif bool(row["ready_to_swap"]) if "ready_to_swap" in row.keys() and row["ready_to_swap"] is not None else False:
        swap_badge = " 🔄 [готовий до обміну]"
    else:
        swap_badge = ""

    id_part = ""
    if show_id and tg_id and tg_id > 0:
        id_part = f" <code>[ID: {tg_id}]</code>"
    pos = row["position"] if "position" in row.keys() else "?"
    return f"{prefix}{pos}. {name}{swap_badge}{suffix}{id_part}"


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
        conn = data.get("conn")
        if conn:
            from services.maintenance import (
                get_maintenance_end_time,
                get_maintenance_message,
                is_maintenance_active,
            )
            if is_maintenance_active(conn):
                user = getattr(event, "from_user", None)
                user_id = user.id if user else None
                is_adm = admins.is_admin(conn, user_id) if user_id else False
                if not is_adm:
                    end_time = get_maintenance_end_time(conn)
                    if isinstance(event, CallbackQuery):
                        with suppress(TelegramBadRequest):
                            await event.answer(
                                f"🛠 Бот на технічних роботах (орієнтовно до {end_time}). Кнопки деактивовано.",
                                show_alert=True,
                            )
                        return
                    elif isinstance(event, Message):
                        msg_text = get_maintenance_message(end_time)
                        from aiogram.types import ReplyKeyboardRemove
                        await event.answer(msg_text, reply_markup=ReplyKeyboardRemove())
                        return

        if isinstance(event, CallbackQuery):
            with suppress(TelegramBadRequest):
                await event.answer()
        try:
            return await handler(event, data)
        except BotError as error:
            await self.notify(event, f"⚠️ {html.escape(str(error))}")
        except TelegramAPIError:
            log.exception("Telegram API error")
        except Exception as error:
            log.exception(f"Unexpected error in handler: {error}")
            err_details = f"{type(error).__name__}: {error}"
            await self.notify(event, f"⚠️ <b>Помилка:</b> {html.escape(err_details)}\nСпробуйте /start або 🏠 Головне меню.")

    @staticmethod
    async def notify(event, text: str) -> None:
        try:
            user = getattr(event, "from_user", None)
            if user:
                await event.bot.send_message(user.id, text)
        except TelegramAPIError:
            log.exception("Cannot deliver error message")


async def notify_admins_queue_opened(bot, conn, creator_id: int, creator_name: str, subject_name: str, teacher_name: str) -> None:
    """Сповіщає всіх адміністраторів про відкриття нової черги."""
    admin_list = admins.list_admins(conn)
    text = (
        f"📢 <b>Відкрито нову чергу!</b>\n\n"
        f"👤 Створив(-ла): <b>{html.escape(creator_name)}</b>\n"
        f"📚 Предмет: <b>{html.escape(subject_name)}</b>\n"
        f"👨‍🏫 Викладач: <b>{html.escape(teacher_name)}</b>\n\n"
        f"Чергу відкрито для запису студентів."
    )
    for adm in admin_list:
        adm_id = adm["telegram_id"]
        if adm_id == creator_id:
            continue
        with suppress(Exception):
            await bot.send_message(adm_id, text)


async def notify_admins_user_registered(bot, conn, user_row, username: str | None = None) -> None:
    """Сповіщає всіх адміністраторів про реєстрацію нового студента."""
    admin_list = admins.list_admins(conn)
    username_part = f" (@{html.escape(username)})" if username else ""
    text = (
        f"👤 <b>Новий користувач зареєструвався в боті!</b>\n\n"
        f"• ПІБ: <b>{html.escape(user_row['full_name'])}</b>\n"
        f"• Telegram ID: <code>{user_row['telegram_id']}</code>{username_part}"
    )
    for adm in admin_list:
        adm_id = adm["telegram_id"]
        if adm_id == user_row["telegram_id"]:
            continue
        with suppress(Exception):
            await bot.send_message(adm_id, text)


async def notify_next_students(bot, conn, subjects: list[dict], session_id: int, removed_position: int | None = None) -> None:
    """Сповіщає студентів, які стали №1 або №2 у черзі після зміни порядку або здачі."""
    if removed_position is not None and removed_position > 2:
        return

    session = queues.get_session(conn, session_id)
    if not session or session["status"] not in ("open", "closed"):
        return

    subject, teacher = session_names(subjects, session)
    entries = queues.get_entries(conn, session_id)
    real_students = [e for e in entries if "telegram_id" in e.keys() and e["telegram_id"] and e["telegram_id"] > 0]
    if not real_students:
        return

    if removed_position is None or removed_position <= 1:
        first = real_students[0]
        text_first = (
            f"🔔 <b>Ваша черга здавати роботу!</b>\n\n"
            f"📚 Предмет: <b>{html.escape(subject)}</b>\n"
            f"👨‍🏫 Викладач: <b>{html.escape(teacher)}</b>\n"
            f"📍 Ви зараз <b>1-й</b> у черзі! Заходьте або готуйтеся показувати роботу."
        )
        with suppress(Exception):
            await bot.send_message(first["telegram_id"], text_first)

    if (removed_position is None or removed_position <= 2) and len(real_students) >= 2:
        second = real_students[1]
        text_second = (
            f"⏳ <b>Приготуйтеся, скоро ваша черга!</b>\n\n"
            f"📚 Предмет: <b>{html.escape(subject)}</b>\n"
            f"👨‍🏫 Викладач: <b>{html.escape(teacher)}</b>\n"
            f"📍 Ви зараз <b>2-й</b> у черзі. Перед вами здає останній студент."
        )
        with suppress(Exception):
            await bot.send_message(second["telegram_id"], text_second)
