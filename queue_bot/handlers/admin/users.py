"""Admin users handlers."""
from .common import (
    AdminPanelState,
    AdminUserState,
    BotError,
    F,
    FSMContext,
    Message,
    StateFilter,
    admins,
    get_admin_settings_menu,
    get_user_card_menu,
    html,
    make_reply_keyboard,
    re,
    users,
)


async def admin_list_users_start(message: Message, state: FSMContext, conn):
    from .presentation import show_users_list
    await state.clear()
    await show_users_list(message, state, conn)



async def admin_paginate_users(message: Message, state: FSMContext, conn):
    from .presentation import show_users_list
    # Old arrow buttons refresh the full list instead of hiding any users.
    await show_users_list(message, state, conn)



async def admin_open_user_card(message: Message, state: FSMContext, conn):
    from .presentation import format_user_card
    match = re.search(r"👤 #(\d+)", message.text)
    if not match:
        return
    user_id = int(match.group(1))
    user = users.get_user_by_id(conn, user_id)
    if not user:
        raise BotError("Користувача не знайдено.")

    await state.set_state(AdminUserState.viewing_card)
    await state.update_data(current_user_id=user["id"])
    await message.answer(
        format_user_card(user),
        reply_markup=get_user_card_menu(bool(user.get("is_blocked"))),
    )



async def admin_back_to_users(message: Message, state: FSMContext, conn):
    from .presentation import show_users_list
    await show_users_list(message, state, conn)



async def admin_start_add_user(message: Message, state: FSMContext):
    await state.set_state(AdminUserState.adding_user)
    await message.answer(
        "➕ <b>Додавання користувача</b>\n\n"
        "Введіть ПІБ студента (двома словами, наприклад: <code>Петренко Іван</code>):",
        reply_markup=make_reply_keyboard([["↩️ Скасувати"]]),
    )



async def admin_finish_add_user(message: Message, state: FSMContext, conn):
    from .users import admin_category_users
    from .presentation import format_user_card
    if message.text in {"↩️ Скасувати", "🔙 Назад", "🔙 В адмін-панель"}:
        await admin_category_users(message, state)
        return
    try:
        new_user = users.admin_create_placeholder(conn, message.from_user.id, message.text)
    except BotError as err:
        await message.answer(f"⚠️ {err}\n\nСпробуйте ще раз або натисніть «↩️ Скасувати»:")
        return

    await state.set_state(AdminUserState.viewing_card)
    await state.update_data(current_user_id=new_user["id"])
    await message.answer(
        "✅ <b>Користувача додано!</b>\n\n" + format_user_card(new_user),
        reply_markup=get_user_card_menu(False),
    )



async def admin_start_search_user(message: Message, state: FSMContext):
    await state.set_state(AdminUserState.searching_user)
    await message.answer(
        "🔍 <b>Пошук користувача</b>\n\n"
        "Введіть частину імені або прізвища:",
        reply_markup=make_reply_keyboard([["↩️ Скасувати"]]),
    )



async def admin_finish_search_user(message: Message, state: FSMContext, conn):
    from .users import admin_category_users
    if message.text in {"↩️ Скасувати", "🔙 Назад", "🔙 До користувачів", "🔙 В адмін-панель"}:
        await admin_category_users(message, state)
        return

    results = users.search_users(conn, message.text)
    if not results:
        await message.answer(
            f"🔍 Користувачів за запитом «{html.escape(message.text)}» не знайдено.",
            reply_markup=make_reply_keyboard([["🔍 Шукати знову"], ["🔙 До користувачів"]]),
        )
        return

    from .presentation import send_user_rows
    await send_user_rows(message, results, f"🔍 <b>Результати пошуку:</b> ({len(results)})", '🔙 До користувачів')




async def admin_ask_edit_name(message: Message, state: FSMContext, conn):
    data = await state.get_data()
    user_id = data.get("current_user_id")
    if not user_id:
        raise BotError("Користувача не обрано.")
    user = users.get_user_by_id(conn, user_id)
    if not user:
        raise BotError("Користувача не знайдено.")

    await state.set_state(AdminUserState.editing_name)
    await message.answer(
        f"✏️ <b>Зміна ПІБ для користувача:</b>\n{html.escape(user['full_name'])}\n\n"
        "Введіть нове ПІБ (двома словами, наприклад: <code>Коваль Андрій</code>):",
        reply_markup=make_reply_keyboard([["↩️ Скасувати"]]),
    )



async def admin_receive_new_name(message: Message, state: FSMContext, conn):
    from .presentation import format_user_card
    data = await state.get_data()
    user_id = data.get("current_user_id")
    if message.text in {"↩️ Скасувати", "🔙 Назад"}:
        user = users.get_user_by_id(conn, user_id)
        await state.set_state(AdminUserState.viewing_card)
        await message.answer(format_user_card(user), reply_markup=get_user_card_menu(bool(user.get("is_blocked"))))
        return

    try:
        new_name = users.validate_full_name(message.text)
    except BotError as err:
        await message.answer(f"⚠️ {err}\n\nСпробуйте ще раз або натисніть «↩️ Скасувати»:")
        return

    user = users.get_user_by_id(conn, user_id)
    await state.set_state(AdminUserState.confirming_name_edit)
    await state.update_data(pending_name=new_name)
    await message.answer(
        f"Підтвердіть зміну ПІБ:\n\n"
        f"Було: <b>{html.escape(user['full_name'])}</b>\n"
        f"Стане: <b>{html.escape(new_name)}</b>",
        reply_markup=make_reply_keyboard([["✅ Підтвердити зміну"], ["↩️ Скасувати"]]),
    )



async def admin_confirm_new_name(message: Message, state: FSMContext, conn):
    from .presentation import format_user_card
    data = await state.get_data()
    user_id = data.get("current_user_id")
    pending_name = data.get("pending_name")

    if message.text != "✅ Підтвердити зміну":
        user = users.get_user_by_id(conn, user_id)
        await state.set_state(AdminUserState.viewing_card)
        await message.answer("Зміну ПІБ скасовано.\n\n" + format_user_card(user), reply_markup=get_user_card_menu(bool(user.get("is_blocked"))))
        return

    try:
        updated = users.admin_update_full_name(conn, message.from_user.id, user_id, pending_name)
    except BotError as err:
        user = users.get_user_by_id(conn, user_id)
        await state.set_state(AdminUserState.viewing_card)
        await message.answer(f"⚠️ {err}\n\n" + format_user_card(user), reply_markup=get_user_card_menu(bool(user.get("is_blocked"))))
        return

    await state.set_state(AdminUserState.viewing_card)
    await message.answer("✅ <b>ПІБ успішно змінено!</b>\n\n" + format_user_card(updated), reply_markup=get_user_card_menu(bool(updated.get("is_blocked"))))



async def admin_toggle_user_block(message: Message, state: FSMContext, conn):
    from .presentation import format_user_card
    data = await state.get_data()
    user_id = data.get("current_user_id")
    if not user_id:
        raise BotError("Користувача не обрано.")
    from queue_bot.services import restrictions
    new_state = message.text == '⛔ Заблокувати'
    if new_state:
        restrictions.set_restriction(conn, message.from_user.id, user_id)
    else:
        restrictions.clear_restriction(conn, message.from_user.id, user_id)
    if new_state:
        note = "⛔ <b>Користувача заблоковано</b> (знято з усіх черг)."
    else:
        note = "🟢 <b>Користувача розблоковано.</b>"

    updated = users.get_user_by_id(conn, user_id)
    await message.answer(f"{note}\n\n" + format_user_card(updated), reply_markup=get_user_card_menu(new_state))



async def admin_ask_delete_user(message: Message, state: FSMContext, conn):
    data = await state.get_data()
    user_id = data.get("current_user_id")
    if not user_id:
        raise BotError("Користувача не обрано.")
    user = users.get_user_by_id(conn, user_id)
    if not user:
        raise BotError("Користувача не знайдено.")

    await state.set_state(AdminUserState.confirming_delete)
    await message.answer(
        f"🗑 <b>Видалити користувача?</b>\n\n"
        f"Видалити зі списку групи <b>{html.escape(user['full_name'])}</b>?\n\n"
        f"Активних записів у чергах бути не повинно. Історія здач залишиться збереженою.",
        reply_markup=make_reply_keyboard([["⚠️ Підтвердити видалення"], ["↩️ Скасувати"]]),
    )



async def admin_confirm_delete_user(message: Message, state: FSMContext, conn):
    from .presentation import format_user_card, show_users_list
    data = await state.get_data()
    user_id = data.get("current_user_id")
    user = users.get_user_by_id(conn, user_id)

    if message.text != "⚠️ Підтвердити видалення":
        await state.set_state(AdminUserState.viewing_card)
        await message.answer("Видалення скасовано.\n\n" + format_user_card(user), reply_markup=get_user_card_menu(bool(user.get("is_blocked"))))
        return

    try:
        users.admin_delete_user(conn, message.from_user.id, user_id)
    except BotError as err:
        await state.set_state(AdminUserState.viewing_card)
        await message.answer(
            f"⚠️ {err}\n\n" + format_user_card(user),
            reply_markup=get_user_card_menu(bool(user.get("is_blocked"))),
        )
        return

    await message.answer("✅ <b>Користувача успішно видалено!</b>")
    await show_users_list(message, state, conn)






async def show_blocked_list(message: Message, state: FSMContext, conn):
    await state.clear()
    blocked = users.list_blocked_users(conn)
    if not blocked:
        from queue_bot.services.maintenance import is_maintenance_active
        await message.answer("Заблокованих користувачів немає.", reply_markup=get_admin_settings_menu(is_maintenance=is_maintenance_active(conn)))
        return
    rows = []
    for u in blocked:
        tg_id_str = f" (ID: {u['telegram_id']})" if u["telegram_id"] and u["telegram_id"] > 0 else ""
        rows.append([f"🟢 Розблокувати: {u['full_name']}{tg_id_str}"])
    rows.append(["🔙 Назад"])
    await message.answer(
        "⛔ <b>Заблоковані користувачі:</b>\nНатисніть на студента, щоб розблокувати його 👇",
        reply_markup=make_reply_keyboard(rows),
    )



async def unblock_student_click(message: Message, state: FSMContext, conn):
    raw = message.text.replace("🟢 Розблокувати: ", "").strip()
    match = re.search(r"\(ID: (\d+)\)", raw)
    user = None
    if match:
        tg_id = int(match.group(1))
        user = users.get_user(conn, tg_id)
    if not user:
        name = re.sub(r"\(ID: \d+\)", "", raw).strip()
        user = conn.execute("SELECT * FROM users WHERE full_name = ? AND is_blocked = TRUE", (name,)).fetchone()
    if not user:
        raise BotError("Студента не знайдено.")
    from queue_bot.services.restrictions import clear_restriction
    clear_restriction(conn, message.from_user.id, user['id'])
    await message.answer(f"🟢 Студента «{html.escape(user['full_name'])}» успішно розблоковано!")
    await show_blocked_list(message, state, conn)



from .common import AdminUserState, get_admin_users_menu

async def admin_category_users(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AdminUserState.in_users_menu)
    await message.answer(
        "👥 <b>Список групи (база користувачів)</b>\n\n"
        "Тут зберігаються профілі всіх студентів групи ПІ-231.\n"
        "Оберіть дію нижче 👇",
        reply_markup=get_admin_users_menu(),
    )

