"""Admin queues handlers."""
from queue_bot.handlers.common import format_queue_list_pair_plans

from .common import (
    AdminManageState,
    AdminOrderState,
    AdminPanelState,
    BotError,
    F,
    FSMContext,
    Message,
    StateFilter,
    find_session_by_label,
    format_queue,
    get_admin_queues_menu,
    get_admin_reply_menu,
    get_paginated_reply_keyboard,
    html,
    make_reply_keyboard,
    notify_next_students,
    paginate,
    queue_label,
    queues,
    re,
    users,
)


async def _show_active_queues(message, state, conn, subjects, page):
    sessions = queues.list_sessions(conn, ("open", "closed"))
    await state.set_state(AdminPanelState.select_queue_for_view)
    if not sessions:
        await state.update_data(page=1, ui_mode='admin')
        await message.answer("Активних черг немає.", reply_markup=get_admin_queues_menu())
        return
    page = max(1, min(page, (len(sessions) + 7) // 8))
    await state.update_data(page=page, ui_mode='admin')
    page_sessions, cur_page, total_pages = paginate(sessions, page, per_page=8)
    rows = [[f"📋 {queue_label(subjects, session)}"] for session in page_sessions]
    rows.append(["➕ Створити чергу", "📚 Предмети та викладачі"])
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 В адмін-панель")
    text = "📋 <b>Активні черги</b>\nОберіть чергу для керування."
    if summaries := format_queue_list_pair_plans(conn, page_sessions, subjects):
        text += "\n" + summaries
    await message.answer(text, reply_markup=markup)


async def admin_pick_active_queues(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    await _show_active_queues(message, state, conn, subjects, 1)


async def admin_paginate_active_queues(message: Message, state: FSMContext, conn, subjects):
    page = (await state.get_data()).get("page", 1)
    await _show_active_queues(message, state, conn, subjects, page + (-1 if message.text == "⬅️" else 1))


async def admin_selected_queue_view(message: Message, state: FSMContext, conn, subjects):
    session = find_session_by_label(conn, subjects, message.text)
    if not session:
        await message.answer("⚠️ Чергу не знайдено. Оберіть зі списку на кнопках внизу 👇")
        return
    await state.update_data(session_id=session["id"])
    from queue_bot.handlers.navigation import switch_admin
    await switch_admin(message, state, conn, subjects)


async def admin_ask_queue_deletion(message: Message, state: FSMContext, conn, subjects):
    from .presentation import get_open_session

    session = get_open_session(conn, (await state.get_data()).get("session_id"))
    await state.set_state(AdminPanelState.confirm_queue_deletion)
    label = html.escape(queue_label(subjects, session))
    await message.answer(
        f"Видалити чергу <b>{label}</b>?\n\n"
        "Чергу, усі її записи та історію буде видалено безповоротно.",
        reply_markup=make_reply_keyboard([["Підтвердити видалення черги"], ["🔙 Назад до черги"]]),
    )


async def admin_confirm_queue_deletion(message: Message, state: FSMContext, conn, subjects):
    sid = (await state.get_data()).get("session_id")
    queues.delete_session(conn, message.from_user.id, sid)
    await state.clear()
    await message.answer("Чергу та всі її записи видалено назавжди.")
    await _show_active_queues(message, state, conn, subjects, 1)



async def admin_click_add_students(message: Message, state: FSMContext, conn, subjects):
    from .presentation import get_open_session
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        raise BotError("Чергу не обрано.")
    session = get_open_session(conn, session_id)
    await state.set_state(AdminPanelState.add_student_names)
    await state.update_data(session_id=session_id)
    await message.answer(
        f"📝 <b>Додавання студентів:</b>\n"
        f"{html.escape(queue_label(subjects, session))}\n\n"
        "Надішліть ПІБ студента або список (кожне з нового рядка).\n"
        "Можна вказувати номер (наприклад: <code>3. Петренко Іван</code>).",
        reply_markup=make_reply_keyboard([["🔙 Назад до черги"], ["🔙 Назад"]]),
    )



async def receive_student_names(message: Message, state: FSMContext, conn, subjects):
    from .panels import admin_panel_open
    from .presentation import open_admin_queue_manager
    data = await state.get_data()
    session_id = data.get("session_id")
    if message.text in {"🔙 Назад до черги", "🔙 Назад"}:
        await state.clear()
        if session_id and message.text == "🔙 Назад до черги":
            await state.update_data(session_id=session_id)
            await open_admin_queue_manager(message, conn, subjects, session_id, state=state)
        else:
            await admin_panel_open(message, state)
        return

    lines = [line.strip() for line in message.text.split("\n") if line.strip()]
    if not lines:
        await message.answer("Введіть хоча б одне прізвище та ім'я:")
        return

    added = []
    skipped = []

    for raw in lines:
        match = re.match(r"^(\d+)[\.\)\-\s]+(.+)$", raw)
        if match:
            requested_pos = int(match.group(1))
            cleaned = match.group(2).strip()
        else:
            requested_pos = None
            cleaned = raw.strip()

        try:
            user = users.get_or_create_user_by_name(conn, cleaned)
            pos = queues.add_student_by_admin(
                conn, message.from_user.id, session_id, user["id"], position=requested_pos
            )
            added.append(f"{pos}. {user['full_name']}")
        except Exception as e:
            skipped.append(f"{raw} ({e})")

    await state.clear()
    await state.update_data(session_id=session_id)
    result_text = f"✅ <b>Додано студентів:</b> {len(added)}\n"
    if added:
        result_text += "\n".join(added[:10]) + ("\n..." if len(added) > 10 else "")
    if skipped:
        result_text += f"\n\n⚠️ Пропущено: {len(skipped)}"

    await open_admin_queue_manager(message, conn, subjects, session_id, note=result_text, state=state)






async def start_managing_students(message: Message, state: FSMContext, conn, subjects):
    from .presentation import open_admin_student_list
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        raise BotError("Чергу не обрано.")
    await state.set_state(AdminOrderState.picking_student)
    await state.update_data(session_id=session_id, students_page=1)
    await open_admin_student_list(message, conn, subjects, session_id, page=1)



async def admin_paginate_students(message: Message, state: FSMContext, conn, subjects):
    from .presentation import open_admin_student_list
    data = await state.get_data()
    session_id = data.get("session_id")
    page = data.get("students_page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await state.update_data(students_page=page)
    await open_admin_student_list(message, conn, subjects, session_id, page=page)



async def select_student_from_list(message: Message, state: FSMContext, conn, subjects):
    from .presentation import open_admin_student_manager
    data = await state.get_data()
    session_id = data.get("session_id")
    entries = queues.get_entries(conn, session_id)

    raw = message.text.removeprefix("👤").strip()
    match = re.match(r"^(\d+)[\.\)\-\s]+(.+)$", raw)
    entry = None
    if match:
        pos = int(match.group(1))
        for e in entries:
            if e["position"] == pos:
                entry = e
                break
    if not entry:
        for e in entries:
            if e["full_name"] and e["full_name"] in raw:
                entry = e
                break
    if not entry:
        await message.answer("Не вдалося знайти запис. Оберіть зі списку на кнопках внизу 👇")
        return

    await state.set_state(AdminOrderState.managing_entry)
    await state.update_data(session_id=session_id, entry_id=entry["id"])
    await open_admin_student_manager(message, conn, subjects, entry["id"])



async def back_to_students_list(message: Message, state: FSMContext, conn, subjects):
    from .panels import admin_panel_open
    from .presentation import open_admin_student_list
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        await admin_panel_open(message, state)
        return
    page = data.get("students_page", 1)
    await state.set_state(AdminOrderState.picking_student)
    await state.update_data(session_id=session_id)
    await open_admin_student_list(message, conn, subjects, session_id, page=page)



async def back_to_queue_manager_from_any(message: Message, state: FSMContext, conn, subjects):
    from .panels import admin_panel_open
    from .guards import admin_state_filter
    from .presentation import open_admin_queue_manager
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        await admin_panel_open(message, state)
        return
    await state.clear()
    await state.update_data(session_id=session_id)
    await open_admin_queue_manager(message, conn, subjects, session_id, state=state)



async def student_move_up(message: Message, state: FSMContext, conn, subjects):
    from .presentation import open_admin_student_manager
    data = await state.get_data()
    entry_id = data.get("entry_id")
    if not entry_id:
        raise BotError("Студента не обрано.")
    queues.move_up(conn, message.from_user.id, entry_id)
    await open_admin_student_manager(message, conn, subjects, entry_id, note="⬆️ Переміщено вище.")



async def student_move_down(message: Message, state: FSMContext, conn, subjects):
    from .presentation import open_admin_student_manager
    data = await state.get_data()
    entry_id = data.get("entry_id")
    if not entry_id:
        raise BotError("Студента не обрано.")
    queues.move_down(conn, message.from_user.id, entry_id)
    await open_admin_student_manager(message, conn, subjects, entry_id, note="⬇️ Переміщено нижче.")



async def student_ask_number(message: Message, state: FSMContext):
    data = await state.get_data()
    entry_id = data.get("entry_id")
    if not entry_id:
        raise BotError("Студента не обрано.")
    await state.set_state(AdminOrderState.entering_number)
    await message.answer(
        "Введіть бажаний номер для студента (1–100):\n"
        "Якщо номер більший за поточний список — перед ним утворяться вільні місця.",
        reply_markup=make_reply_keyboard([["↩️ Скасувати"]]),
    )



async def student_receive_number(message: Message, state: FSMContext, conn, subjects):
    from .presentation import open_admin_student_manager
    data = await state.get_data()
    entry_id = data.get("entry_id")
    if message.text in {"↩️ Скасувати", "🔙 Скасувати", "🔙 Назад"}:
        await state.set_state(AdminOrderState.managing_entry)
        await open_admin_student_manager(message, conn, subjects, entry_id)
        return
    if not message.text.strip().isdigit():
        await message.answer("Введіть число або натисніть «↩️ Скасувати»:")
        return
    pos = int(message.text.strip())
    try:
        queues.move_entry(conn, message.from_user.id, entry_id, pos)
    except BotError as err:
        await message.answer(f"{err}\nСпробуйте ще раз:")
        return
    await state.set_state(AdminOrderState.managing_entry)
    await open_admin_student_manager(message, conn, subjects, entry_id, note=f"🔢 Позицію змінено на {pos}.")



async def student_ask_rename(message: Message, state: FSMContext, conn):
    data = await state.get_data()
    entry_id = data.get("entry_id")
    if not entry_id:
        raise BotError("Студента не обрано.")
    entry = queues.get_entry(conn, entry_id)
    user = conn.execute("SELECT * FROM users WHERE id = ?", (entry["user_id"],)).fetchone() if entry["user_id"] else None
    if not user:
        raise BotError("Користувача не знайдено.")
    await state.set_state(AdminOrderState.entering_name)
    await state.update_data(user_id=user["id"])
    await message.answer(
        f"Введіть нове ПІБ для студента «{html.escape(user['full_name'])}»:",
        reply_markup=make_reply_keyboard([["↩️ Скасувати"]]),
    )



async def student_receive_rename(message: Message, state: FSMContext, conn, subjects):
    from .presentation import open_admin_student_manager
    data = await state.get_data()
    entry_id = data.get("entry_id")
    user_id = data.get("user_id")
    if message.text in {"↩️ Скасувати", "🔙 Скасувати", "🔙 Назад"}:
        await state.set_state(AdminOrderState.managing_entry)
        await open_admin_student_manager(message, conn, subjects, entry_id)
        return
    new_name = message.text.strip()
    try:
        updated_user = users.admin_rename_user(conn, user_id, new_name)
    except BotError as err:
        await message.answer(f"{err}\nСпробуйте ще раз:")
        return
    await state.set_state(AdminOrderState.managing_entry)
    await open_admin_student_manager(message, conn, subjects, entry_id, note=f"✅ Ім'я змінено на «{html.escape(updated_user['full_name'])}».")



async def student_toggle_block(message: Message, state: FSMContext, conn, subjects):
    from .presentation import open_admin_student_list, open_admin_student_manager
    data = await state.get_data()
    entry_id = data.get("entry_id")
    session_id = data.get("session_id")
    if not entry_id:
        raise BotError("Студента не обрано.")
    entry = queues.get_entry(conn, entry_id)
    user = conn.execute("SELECT * FROM users WHERE id = ?", (entry["user_id"],)).fetchone() if entry["user_id"] else None
    if not user:
        raise BotError("Користувача не знайдено.")
    from queue_bot.services import restrictions
    new_state = message.text == '⛔ Заблокувати'
    if new_state:
        restrictions.set_restriction(conn, message.from_user.id, user['id'])
    else:
        restrictions.clear_restriction(conn, message.from_user.id, user['id'])
    if new_state:
        await state.set_state(AdminOrderState.picking_student)
        await open_admin_student_list(
            message, conn, subjects, session_id,
            note=f"⛔ Студента «{html.escape(user['full_name'])}» заблоковано.",
        )
    else:
        await open_admin_student_manager(
            message, conn, subjects, entry_id,
            note=f"🟢 Студента «{html.escape(user['full_name'])}» розблоковано.",
        )



async def student_mark_passed_by_admin(message: Message, state: FSMContext, conn, subjects):
    from .presentation import open_admin_student_list
    data = await state.get_data()
    entry_id = data.get("entry_id")
    session_id = data.get("session_id")
    if not entry_id:
        raise BotError("Студента не обрано.")
    entry = queues.get_entry(conn, entry_id)
    pos = entry["position"] if entry else None
    queues.mark_done(conn, message.from_user.id, entry_id)
    if session_id:
        await notify_next_students(message.bot, conn, subjects, session_id, removed_position=pos)
    await state.set_state(AdminOrderState.picking_student)
    await open_admin_student_list(message, conn, subjects, session_id, note="✅ <b>Здачу зараховано</b>")



async def student_remove_by_admin(message: Message, state: FSMContext, conn, subjects):
    from .presentation import open_admin_student_list
    data = await state.get_data()
    entry_id = data.get("entry_id")
    session_id = data.get("session_id")
    if not entry_id:
        raise BotError("Студента не обрано.")
    entry = queues.get_entry(conn, entry_id)
    pos = entry["position"] if entry else None
    queues.remove_entry(conn, message.from_user.id, entry_id)
    if session_id:
        await notify_next_students(message.bot, conn, subjects, session_id, removed_position=pos)
    await state.set_state(AdminOrderState.picking_student)
    await open_admin_student_list(message, conn, subjects, session_id, note="❌ <b>Запис видалено</b>")
