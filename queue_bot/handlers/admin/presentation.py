"""Admin presentation handlers."""
from .common import (
    AdminUserState,
    BotError,
    FSMContext,
    Message,
    ReplyKeyboardMarkup,
    config,
    format_entry,
    format_queue,
    get_admin_users_menu,
    get_paginated_reply_keyboard,
    html,
    make_reply_keyboard,
    paginate,
    queues,
    session_names,
    users,
)


def get_open_session(conn, session_id: int):
    session = queues.get_session(conn, session_id)
    if session is None or session["status"] == "archived":
        raise BotError("Ця черга вже завершена або не існує.")
    return session



def get_admin_queue_menu(is_open: bool) -> ReplyKeyboardMarkup:
    lock_text = "🔒 Закрити запис" if is_open else "🔓 Відкрити запис"
    rows = [
        ["Почати здачу на парі", "Зупинити здачу на парі"],
        ["➕ Додати студентів", "🔢 Керувати студентами"],
        [lock_text, "📦 Завершити чергу"],
        ["👤 Режим студента", "🔄 Оновити"],
        ["🔙 Назад", "🔙 В адмін-панель"],
    ]
    return make_reply_keyboard(rows)



def get_admin_student_menu(is_blocked: bool) -> ReplyKeyboardMarkup:
    block_btn = "🟢 Розблокувати" if is_blocked else "⛔ Заблокувати"
    rows = [
        ["⬆️ Вище", "⬇️ Нижче"],
        ['⏳ Бан на N днів'],
        ["🔢 Задати номер", "✏️ Змінити ім'я"],
        [block_btn, "✅ Позначити «Здав»"],
        ["❌ Видалити з черги"],
        ["🔙 До списку студентів", "🔙 Назад"],
    ]
    return make_reply_keyboard(rows)



async def open_admin_queue_manager(message: Message, conn, subjects, session_id: int, note: str = "", state=None):
    if state is not None:
        from queue_bot.handlers.navigation import AdminNavigation
        await state.set_state(AdminNavigation.queue)
        await state.update_data(session_id=session_id, ui_mode='admin')
    session = get_open_session(conn, session_id)
    entries = queues.get_entries(conn, session_id)
    from queue_bot.services import attendance
    text = '⚡ <b>Режим адміна</b>\n\n' + format_queue(subjects, session, entries, show_id=True, lesson=attendance.view(conn, session_id, message.from_user.id))
    from queue_bot.handlers.attendance import lesson_summary
    text += lesson_summary(conn, session_id, message.from_user.id)
    if note:
        text = f"{note}\n\n{text}"
    await message.answer(text, reply_markup=get_admin_queue_menu(session["status"] == "open"))



async def open_admin_student_list(message: Message, conn, subjects, session_id: int, note: str = "", page: int = 1):
    session = get_open_session(conn, session_id)
    entries = queues.get_entries(conn, session_id)
    from queue_bot.services import attendance
    lesson = attendance.view(conn, session_id, message.from_user.id)
    entries = attendance.display_entries(entries, lesson)
    if not entries:
        await open_admin_queue_manager(message, conn, subjects, session_id, note="У черзі немає студентів.")
        return
    page_entries, cur_page, total_pages = paginate(entries, page, per_page=8)
    rows = [[f"👤 {format_entry(e, show_id=True)}"] for e in page_entries]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад до черги")
    text = format_queue(subjects, session, entries, show_id=True, lesson=lesson) + "\n\nОберіть студента для дій на кнопках внизу 👇"
    if note:
        text = f"{note}\n\n{text}"
    await message.answer(text, reply_markup=markup)



async def open_admin_student_manager(message: Message, conn, subjects, entry_id: int, note: str = ""):
    entry = queues.get_entry(conn, entry_id)
    if entry is None or entry["status"] != "active":
        raise BotError("Запис не знайдено або він уже неактивний.")
    session = get_open_session(conn, entry["session_id"])
    subject, teacher = session_names(subjects, session)

    user = None
    if entry["user_id"]:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (entry["user_id"],)).fetchone()

    lines = []
    lines.append(f"📚 <b>{html.escape(subject)} — {html.escape(teacher)}</b>")
    user_name = html.escape(user["full_name"]) if user else "— вільне місце —"
    tg_info = f" <code>[ID: {user['telegram_id']}]</code>" if user and user["telegram_id"] and user["telegram_id"] > 0 else " [без TG]"
    status_info = "⛔ Заблокований" if user and user["is_blocked"] else "✅ Активний"
    lines.append(f"👤 Студент: <b>{user_name}</b>{tg_info} — {status_info}")
    lines.append(f"📍 Поточна позиція в черзі: <b>{entry['position']}</b>")
    lines.append("")
    lines.append("Оберіть дію на кнопках внизу 👇")
    text = "\n".join(lines)
    if note:
        text = f"{note}\n\n{text}"

    is_blocked = bool(user and user["is_blocked"])
    await message.answer(text, reply_markup=get_admin_student_menu(is_blocked))
    from queue_bot.services import attendance
    if attendance.active_run(conn, entry['session_id']):
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        await message.answer('Готовність на цій парі:', reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f'attadmin:{entry_id}:{attendance.active_run(conn, entry["session_id"])["id"]}:{action}')]
            for label, action in [('Підтвердити присутність', 'present'), ('Пропустити на цій парі', 'skipped'),
                                  ('Завершити підхід з рештою робіт', 'finish')]]))



def format_user_card(user) -> str:
    is_placeholder = bool(user["telegram_id"] and user["telegram_id"] < 0)
    tg_status = "ще не прив’язано" if is_placeholder else "прив’язано"
    user = dict(user)
    status_text = "⛔ заблокований" if user.get("is_blocked") else "активний"
    if user.get('is_blocked') and user.get('banned_until'):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        until = datetime.fromisoformat(user['banned_until']).astimezone(ZoneInfo(config.TIMEZONE))
        status_text = f'⏳ бан до {until:%d.%m.%Y %H:%M} (за Києвом)'
    lines = [
        "👤 <b>Користувач</b>",
        "",
        f"ПІБ: <b>{html.escape(user['full_name'])}</b>",
        f"Telegram: {tg_status}",
    ]
    if not is_placeholder and user["telegram_id"] and user["telegram_id"] > 0:
        lines.append(f"Telegram ID: <code>{user['telegram_id']}</code>")
    lines.append(f"Статус: {status_text}")
    return "\n".join(lines)



async def send_user_rows(message, users_list, title, back_text):
    user_buttons = [f"👤 #{u['id']} {u['full_name']}" for u in users_list]
    row_pairs = [user_buttons[i:i + 2] for i in range(0, len(user_buttons), 2)]
    markup = make_reply_keyboard(row_pairs + [[back_text]])
    lines = [title, ''] + [f"{index}. {html.escape(user['full_name'])}" for index, user in enumerate(users_list, 1)]
    # Send every name immediately, without page controls. Split only if a long
    # roster exceeds Telegram's message limit; numbering stays continuous.
    chunks, chunk = [], ''
    for line in lines:
        candidate = chunk + ('\n' if chunk else '') + line
        if len(candidate.encode('utf-16-le')) // 2 > 3900:
            chunks.append(chunk)
            chunk = line
        else:
            chunk = candidate
    if chunk:
        chunks.append(chunk)
    for index, text in enumerate(chunks):
        await message.answer(text, reply_markup=markup if index == len(chunks) - 1 else None)


async def show_users_list(message: Message, state: FSMContext, conn):
    users_list = users.list_users(conn)
    await state.set_state(AdminUserState.browsing_users)
    if not users_list:
        await message.answer("Користувачів у базі ще немає.", reply_markup=get_admin_users_menu())
        return
    await send_user_rows(message, users_list, f"👥 <b>Користувачі</b> · Усього: {len(users_list)}", '🔙 В адмін-панель')
