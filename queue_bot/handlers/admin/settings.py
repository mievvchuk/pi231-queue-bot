"""Admin settings handlers."""
from .common import (
    AdminManageState,
    AdminPanelState,
    AdminUserState,
    F,
    FSMContext,
    Message,
    StateFilter,
    admins,
    config,
    get_admin_settings_menu,
    html,
    make_reply_keyboard,
    re,
)


async def admin_back_to_main_panel(message: Message, state: FSMContext, conn=None):
    from .panels import admin_panel_open
    await admin_panel_open(message, state, conn=conn)



async def admins_menu(message: Message, state: FSMContext, conn=None):
    admins.require_owner(conn, message.from_user.id)
    await state.clear()
    await state.set_state(AdminManageState.in_admins_menu)
    rows = [
        ["📋 Список адміністраторів", "➕ Додати адміністратора"],
        ["❌ Видалити адміністратора", "🔙 До налаштувань"],
    ]
    await message.answer("Керування адміністраторами. Оберіть дію на кнопках внизу 👇", reply_markup=make_reply_keyboard(rows))



async def list_admins_message(message: Message, conn, state: FSMContext):
    admins.require_owner(conn, message.from_user.id)
    await state.clear()
    await state.set_state(AdminManageState.viewing_admins)
    adm_list = admins.list_admins(conn)
    lines = ["👥 <b>Адміністратори:</b>"]
    for r in adm_list:
        name = r["full_name"] or "без реєстрації"
        lines.append(f"• <b>{html.escape(name)}</b> (<code>{r['telegram_id']}</code>)")
    rows = [
        ["➕ Додати адміністратора", "❌ Видалити адміністратора"],
        ["🔙 До адміністраторів"],
    ]
    await message.answer("\n".join(lines), reply_markup=make_reply_keyboard(rows))



async def ask_admin_id(message: Message, state: FSMContext, conn=None):
    admins.require_owner(conn, message.from_user.id)
    await state.set_state(AdminManageState.add_admin_id)
    rows = [["↩️ Скасувати", "🔙 Назад"]]
    await message.answer("Введіть числовий Telegram ID нового адміністратора:", reply_markup=make_reply_keyboard(rows))



async def receive_admin_id(message: Message, state: FSMContext, conn):
    admins.require_owner(conn, message.from_user.id)
    if message.text in {"↩️ Скасувати", "🔙 Скасувати", "🔙 Назад"}:
        await admins_menu(message, state, conn=conn)
        return
    text = message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Telegram ID — це число. Спробуйте ще раз або натисніть «↩️ Скасувати»:")
        return
    new_admin_id = int(text)
    if admins.is_admin(conn, new_admin_id):
        await message.answer("Цей користувач уже є адміністратором. Введіть інший ID:")
        return
    admins.add_admin(conn, message.from_user.id, new_admin_id)
    await state.clear()
    await message.answer(f"✅ Адміністратора з ID <code>{new_admin_id}</code> успішно додано!")
    await list_admins_message(message, conn, state)



async def choose_admin_to_remove(message: Message, state: FSMContext, conn):
    admins.require_owner(conn, message.from_user.id)
    adm_list = admins.list_admins(conn)
    rows = []
    for r in adm_list:
        if r["telegram_id"] == message.from_user.id:
            continue
        name = r["full_name"] or "без реєстрації"
        rows.append([f"❌ Видалити: {name} ({r['telegram_id']})"])
    rows.append(["↩️ Скасувати", "🔙 Назад"])
    await state.set_state(AdminManageState.select_admin_to_remove)
    await message.answer("Оберіть адміністратора для видалення на кнопках внизу 👇", reply_markup=make_reply_keyboard(rows))



async def confirm_remove_admin_text(message: Message, state: FSMContext, conn):
    admins.require_owner(conn, message.from_user.id)
    match = re.search(r"\((\d+)\)$", message.text)
    if not match:
        return
    target_id = int(match.group(1))
    admins.remove_admin(conn, message.from_user.id, target_id)
    await state.clear()
    await message.answer(f"✅ Права адміністратора для ID <code>{target_id}</code> видалено.")
    await list_admins_message(message, conn, state)



async def cancel_remove_admin(message: Message, state: FSMContext, conn=None):
    await state.clear()
    await admins_menu(message, state, conn=conn)



async def start_broadcast_flow(message: Message, state: FSMContext, conn):
    admins.require_owner(conn, message.from_user.id)
    from queue_bot.services.updates import get_active_user_ids
    uids = get_active_user_ids(conn)
    await state.set_state(AdminManageState.entering_broadcast)
    await message.answer(
        f"📢 <b>Розсилка повідомлення всім користувачам</b>\n\n"
        f"Зареєстровано активних користувачів: <b>{len(uids)}</b>.\n\n"
        "Надішліть текст оголошення (підтримується HTML-розмітка: <code>&lt;b&gt;жирний&lt;/b&gt;</code> тощо):\n"
        "Або натисніть «🔙 Скасувати» нижче 👇",
        reply_markup=make_reply_keyboard([["🔙 Скасувати"]]),
    )



async def preview_broadcast_message(message: Message, state: FSMContext, conn):
    from .settings import admin_category_settings
    admins.require_owner(conn, message.from_user.id)
    if message.text in {"🔙 Скасувати", "🔙 Назад", "↩️ Скасувати"}:
        await state.clear()
        await admin_category_settings(message, state, conn=conn)
        return
    text = message.text.strip()
    if not text:
        await message.answer("Введіть текст оголошення або натисніть «🔙 Скасувати»:")
        return

    from queue_bot.services.updates import get_active_user_ids
    uids = get_active_user_ids(conn)
    await state.set_state(AdminManageState.confirming_broadcast)
    await state.update_data(broadcast_text=text)

    await message.answer(
        f"👀 <b>Попередній перегляд оголошення:</b>\n\n{text}\n\n"
        f"➖➖➖➖➖➖➖➖\n"
        f"Надіслати це повідомлення усім <b>{len(uids)}</b> користувачам?",
        reply_markup=make_reply_keyboard([["✅ Надіслати всім"], ["🔙 Скасувати"]]),
    )



async def perform_broadcast(message: Message, state: FSMContext, conn):
    from .settings import admin_category_settings
    admins.require_owner(conn, message.from_user.id)
    if message.text in {"🔙 Скасувати", "🔙 Назад", "↩️ Скасувати"}:
        await state.clear()
        await admin_category_settings(message, state, conn=conn)
        return
    if message.text != "✅ Надіслати всім":
        await message.answer("Оберіть дію на кнопках: «✅ Надіслати всім» або «🔙 Скасувати» 👇")
        return

    data = await state.get_data()
    text = data.get("broadcast_text")
    if not text:
        await state.clear()
        await admin_category_settings(message, state, conn=conn)
        return

    from queue_bot.services.updates import get_active_user_ids, broadcast_message_to_users
    uids = get_active_user_ids(conn)
    await message.answer(f"⏳ Розпочинаю розсилку для {len(uids)} користувачів...")
    success, errors = await broadcast_message_to_users(message.bot, uids, text)
    admins.log_action(conn, message.from_user.id, "manual_broadcast", f"success={success} errors={errors}")
    await state.clear()
    from queue_bot.services.maintenance import is_maintenance_active
    await message.answer(
        f"✅ <b>Розсилку успішно завершено!</b>\n\n"
        f"• Доставлено: <b>{success}</b>\n"
        f"• Помилок: <b>{errors}</b>",
        reply_markup=get_admin_settings_menu(is_maintenance=is_maintenance_active(conn)),
    )



async def start_maintenance_prompt(message: Message, state: FSMContext, conn):
    admins.require_owner(conn, message.from_user.id)
    from queue_bot.services.maintenance import is_maintenance_active
    if is_maintenance_active(conn):
        await message.answer(
            "🛠 Режим технічних робіт вже увімкнено.",
            reply_markup=get_admin_settings_menu(is_maintenance=True),
        )
        return

    await state.set_state(AdminManageState.entering_maintenance_time)
    keyboard = make_reply_keyboard([
        ["через 15 хв", "через 30 хв"],
        ["через 1 год", "через 2 год"],
        ["🔙 Скасувати"],
    ])
    await message.answer(
        "🛠 <b>Увімкнення режиму технічних робіт</b>\n\n"
        "Вкажіть орієнтовний час завершення (наприклад: <code>21:30</code>, <code>22:00</code>) "
        "або оберіть швидкий варіант на кнопках нижче 👇",
        reply_markup=keyboard,
    )



async def process_maintenance_time_entry(message: Message, state: FSMContext, conn):
    admins.require_owner(conn, message.from_user.id)
    if message.text in {"🔙 Скасувати", "🔙 Назад"}:
        await state.set_state(AdminPanelState.in_settings_menu)
        from queue_bot.services.maintenance import is_maintenance_active
        await message.answer(
            "Скасовано. Режим техробіт не увімкнено.",
            reply_markup=get_admin_settings_menu(is_maintenance=is_maintenance_active(conn)),
        )
        return

    from queue_bot import config
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now_tz = datetime.now(ZoneInfo(config.TIMEZONE))
    t_text = message.text.strip()
    if t_text == "через 15 хв":
        end_t = (now_tz + timedelta(minutes=15)).strftime("%H:%M")
    elif t_text == "через 30 хв":
        end_t = (now_tz + timedelta(minutes=30)).strftime("%H:%M")
    elif t_text == "через 1 год":
        end_t = (now_tz + timedelta(hours=1)).strftime("%H:%M")
    elif t_text == "через 2 год":
        end_t = (now_tz + timedelta(hours=2)).strftime("%H:%M")
    else:
        end_t = t_text

    from queue_bot.services.maintenance import broadcast_maintenance_notice, set_maintenance_mode
    set_maintenance_mode(conn, True, end_t)
    admins.log_action(conn, message.from_user.id, "toggle_maintenance", f"active=True, end_time={end_t}")

    await message.answer("⏳ Розсилаю сповіщення про техроботи та приховую кнопки у студентів...")
    succ, err = await broadcast_maintenance_notice(message.bot, conn, end_t)
    await state.set_state(AdminPanelState.in_settings_menu)
    status_txt = (
        f"🛠 <b>Режим техробіт УВІМКНЕНО!</b> (орієнтовно до {end_t})\n\n"
        f"• Сповіщення доставлено: <b>{succ}</b> користувачам (помилок: {err})\n"
        f"• Доступ до запису заблоковано, кнопки у студентів деактивовано."
    )
    await message.answer(
        status_txt,
        reply_markup=get_admin_settings_menu(is_maintenance=True),
    )



async def disable_maintenance(message: Message, state: FSMContext, conn):
    admins.require_owner(conn, message.from_user.id)
    from queue_bot.services.maintenance import broadcast_maintenance_end, set_maintenance_mode
    set_maintenance_mode(conn, False)
    admins.log_action(conn, message.from_user.id, "toggle_maintenance", "active=False")

    await message.answer("⏳ Розсилаю сповіщення про завершення робіт та повертаю кнопки студентам...")
    succ, err = await broadcast_maintenance_end(message.bot, conn)
    await state.set_state(AdminPanelState.in_settings_menu)
    status_txt = (
        "🟢 <b>Режим техробіт ВИМКНЕНО!</b>\n\n"
        f"• Сповіщення доставлено: <b>{succ}</b> користувачам (помилок: {err})\n"
        f"• Студентам надіслано повідомлення про завершення робіт та вказівку скористатися /start для оновлення меню!"
    )
    await message.answer(
        status_txt,
        reply_markup=get_admin_settings_menu(is_maintenance=False),
    )



from .common import get_admin_settings_menu, AdminPanelState

async def admin_category_settings(message: Message, state: FSMContext, conn=None):
    admins.require_owner(conn, message.from_user.id)
    from queue_bot.services.maintenance import is_maintenance_active
    is_maint = is_maintenance_active(conn) if conn else False
    await state.clear()
    await state.set_state(AdminPanelState.in_settings_menu)
    await message.answer(
        "⚙️ <b>Налаштування (Тільки для власника)</b>\n\nОберіть розділ нижче 👇",
        reply_markup=get_admin_settings_menu(is_maintenance=is_maint),
    )

