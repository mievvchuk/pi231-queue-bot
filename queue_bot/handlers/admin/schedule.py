"""Admin schedule handlers."""
from .common import (
    AdminPanelState,
    AdminUserState,
    CallbackQuery,
    F,
    FSMContext,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    StateFilter,
    admins,
    config,
    date,
    get_admin_schedule_menu,
    get_admin_users_menu,
)


async def admin_category_schedule(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AdminPanelState.in_schedule_menu)
    await message.answer(
        "📅 <b>Керування розкладом та замінами</b>\n\n"
        "Заміни надходять автоматично з каналу й застосовуються після підтвердження. Відредагувати їх можна до або після збереження.\n\n"
        "• <b>🔄 Активні заміни</b> — переглянути поточні збережені заміни\n"
        "• <b>🏢 Робоча субота</b> — призначити суботу навчальним днем за розкладом іншого дня\n"
        "• <b>📦 Архів замін</b> — переглянути історію попередніх замін\n"
        "• <b>📥 Отримати / 📤 Завантажити розклад</b> — редагування базового файлу <code>schedule.json</code>\n"
        "• <b>🗑 Очистити всі заміни</b> — видалити поточні заміни пар",
        reply_markup=get_admin_schedule_menu(),
    )



async def admin_add_replacement_photo_hint(message: Message):
    await message.answer(
        "📸 <b>Додавання замін за фотографією</b>\n\n"
        "Надішліть <b>одне фото або два фото таблиці замін одним альбомом</b> прямо сюди в чат!\n"
        "Для двох частин виберіть обидва фото й надішліть разом: бот прочитає спільну дату, примітки та всі рядки.\n\n"
        "1️⃣ Бот автоматично знайде пари групи <b>ПІ-231</b>.\n"
        "2️⃣ Покаже вам попередній перегляд із кнопкою <b>«✏️ Внести корективи»</b> (якщо потрібно підправити пару, аудиторію чи дату).\n"
        "3️⃣ Після натискання <b>«✅ Підтвердити заміни»</b> бот збереже їх і автоматично розішле сповіщення всій групі!",
        reply_markup=get_admin_schedule_menu(),
    )



async def admin_export_schedule_action(message: Message, conn):
    from queue_bot.handlers.schedule import export_schedule_file
    await export_schedule_file(message, conn)



async def admin_upload_schedule_action(message: Message):
    await message.answer(
        "📤 <b>Як оновити розклад занять:</b>\n\n"
        "1. Натисніть кнопку <b>«📥 Отримати schedule.json»</b> та збережіть файл.\n"
        "2. Відкрийте файл на телефоні або комп'ютері та внесіть актуальні пари (предмети, аудиторії, викладачів).\n"
        "3. <b>Надішліть збережений файл <code>schedule.json</code> прямо сюди в чат як документ 📄.</b>\n\n"
        "Бот автоматично валідує формат JSON і миттєво оновить розклад для всієї групи!"
    )



async def admin_clear_replacements_action(message: Message, conn):
    from queue_bot.handlers.schedule import request_clear_replacements
    await request_clear_replacements(message, conn)



async def admin_status_action(message: Message, conn):
    from queue_bot.handlers.schedule import show_bot_status
    await show_bot_status(message, conn)



async def admin_working_saturday_btn(message: Message, conn):
    from queue_bot.services.working_days import get_next_saturday
    sat = get_next_saturday()
    await render_working_saturday_panel(message, conn, sat)



async def render_working_saturday_panel(event: Message | CallbackQuery, conn, sat_date: date):
    from queue_bot.services.working_days import (
        get_working_saturday,
        DAY_NAMES_UK,
    )
    from queue_bot.services import schedule
    from queue_bot import config
    date_str = sat_date.isoformat()
    date_formatted = sat_date.strftime("%d.%m.%Y")
    ws = get_working_saturday(conn, date_str)
    sch_data = schedule.load_schedule_data(config.SCHEDULE_PATH)

    if ws:
        rep_day_key = ws["replaces_day"]
        rep_day_uk = DAY_NAMES_UK.get(rep_day_key, rep_day_key.capitalize())
        w_type = ws.get("week_type") or schedule.get_week_type(sat_date)
        w_type_uk = "Знаменник" if w_type == "denominator" else "Чисельник"

        actual_lessons, reps = schedule.get_actual_schedule(conn, sch_data, sat_date, config.STUDENT_GROUP)
        lessons_summary = f"Пари за розкладом: <b>{len(actual_lessons)}</b>"
        reps_summary = f"Активних замін на суботу: <b>{len(reps)}</b>"

        text = (
            f"🏢 <b>Робоча субота — {date_formatted}</b>\n\n"
            f"• Статус: 🟢 <b>Робочий день</b>\n"
            f"• Працюємо за розкладом: <b>{rep_day_uk}</b> ({w_type_uk})\n"
            f"• {lessons_summary}\n"
            f"• {reps_summary}\n\n"
            "<i>Щоб змінити день або скасувати робочу суботу, скористайтеся кнопками нижче:</i>"
        )
        kb = [
            [
                InlineKeyboardButton(text="📢 Оголосити групі", callback_data=f"ws_bc_prompt:{date_str}"),
                InlineKeyboardButton(text="👀 Переглянути розклад", callback_data=f"ws_view:{date_str}"),
            ],
            [
                InlineKeyboardButton(text="✏️ Змінити день тижня", callback_data=f"ws_pick:{date_str}"),
                InlineKeyboardButton(text="🔄 Тип тижня", callback_data=f"ws_par:{date_str}"),
            ],
            [
                InlineKeyboardButton(text="🏖 Зробити вихідним (скасувати)", callback_data=f"ws_cancel:{date_str}"),
            ],
            [
                InlineKeyboardButton(text="📅 Обрати іншу суботу", callback_data=f"ws_other:{date_str}"),
            ],
        ]
    else:
        w_type = schedule.get_week_type(sat_date)
        w_type_uk = "Знаменник" if w_type == "denominator" else "Чисельник"
        text = (
            f"🏢 <b>Налаштування робочої суботи — {date_formatted}</b>\n\n"
            f"• Статус: 🏖 <b>Вихідний день</b> (занять немає)\n"
            f"• Календарний тип тижня: <b>{w_type_uk}</b>\n\n"
            "Бажаєте призначити цю суботу робочим днем?\n"
            "<b>Оберіть день тижня, за розкладом якого працюватиме група:</b>"
        )
        kb = [
            [
                InlineKeyboardButton(text="🔹 Понеділок", callback_data=f"ws_set:{date_str}:monday"),
                InlineKeyboardButton(text="🔹 Вівторок", callback_data=f"ws_set:{date_str}:tuesday"),
            ],
            [
                InlineKeyboardButton(text="🔹 Середа", callback_data=f"ws_set:{date_str}:wednesday"),
                InlineKeyboardButton(text="🔹 Четвер", callback_data=f"ws_set:{date_str}:thursday"),
            ],
            [
                InlineKeyboardButton(text="🔹 П'ятниця", callback_data=f"ws_set:{date_str}:friday"),
            ],
            [
                InlineKeyboardButton(text="📅 Обрати іншу суботу", callback_data=f"ws_other:{date_str}"),
            ],
        ]

    from queue_bot.services.working_days import is_saturday_archived
    if ws and is_saturday_archived(date_str):
        text = (f"📦 <b>Архів робочих субот — {date_formatted}</b>\n\n"
                f"За розкладом: <b>{DAY_NAMES_UK.get(ws['replaces_day'], ws['replaces_day'])}</b>.\n"
                "Цю суботу завершено о 13:30. Запис збережено для історії.")
        kb = [[InlineKeyboardButton(text="👀 Переглянути розклад", callback_data=f"ws_view:{date_str}")],
              [InlineKeyboardButton(text="📅 Обрати іншу суботу", callback_data=f"ws_other:{date_str}")]]
    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup)
    else:
        await event.answer(text, reply_markup=markup)



async def admin_set_working_saturday_day(callback: CallbackQuery, conn):
    parts = callback.data.split(":")
    date_str, day_key = parts[1], parts[2]
    from queue_bot.services.working_days import is_saturday_archived
    if is_saturday_archived(date_str):
        await callback.answer("Ця субота вже в архіві. Запис доступний лише для перегляду.", show_alert=True)
        return
    from queue_bot.services.working_days import set_working_saturday, DAY_NAMES_UK
    set_working_saturday(conn, date_str, day_key)
    admins.log_action(conn, callback.from_user.id, "set_working_saturday", f"date={date_str} day={day_key}")
    day_uk = DAY_NAMES_UK.get(day_key, day_key)
    await callback.answer(f"Встановлено розклад: {day_uk}!")
    sat_date = date.fromisoformat(date_str)
    await render_working_saturday_panel(callback, conn, sat_date)



async def admin_pick_working_saturday_day(callback: CallbackQuery, conn):
    date_str = callback.data.split(":", 1)[1]
    sat_date = date.fromisoformat(date_str)
    date_formatted = sat_date.strftime("%d.%m.%Y")
    kb = [
        [
            InlineKeyboardButton(text="🔹 Понеділок", callback_data=f"ws_set:{date_str}:monday"),
            InlineKeyboardButton(text="🔹 Вівторок", callback_data=f"ws_set:{date_str}:tuesday"),
        ],
        [
            InlineKeyboardButton(text="🔹 Середа", callback_data=f"ws_set:{date_str}:wednesday"),
            InlineKeyboardButton(text="🔹 Четвер", callback_data=f"ws_set:{date_str}:thursday"),
        ],
        [
            InlineKeyboardButton(text="🔹 П'ятниця", callback_data=f"ws_set:{date_str}:friday"),
        ],
        [
            InlineKeyboardButton(text="🔙 Скасувати", callback_data=f"ws_back:{date_str}"),
        ],
    ]
    await callback.answer()
    await callback.message.edit_text(
        f"🏢 <b>Оберіть новий день тижня для суботи {date_formatted}:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )



async def admin_parity_working_saturday(callback: CallbackQuery, conn):
    date_str = callback.data.split(":", 1)[1]
    sat_date = date.fromisoformat(date_str)
    date_formatted = sat_date.strftime("%d.%m.%Y")
    kb = [
        [
            InlineKeyboardButton(text="🔹 Чисельник", callback_data=f"ws_setpar:{date_str}:numerator"),
            InlineKeyboardButton(text="🔹 Знаменник", callback_data=f"ws_setpar:{date_str}:denominator"),
        ],
        [
            InlineKeyboardButton(text="🔹 За календарем (авто)", callback_data=f"ws_setpar:{date_str}:auto"),
        ],
        [
            InlineKeyboardButton(text="🔙 Скасувати", callback_data=f"ws_back:{date_str}"),
        ],
    ]
    await callback.answer()
    await callback.message.edit_text(
        f"🔄 <b>Оберіть тип тижня для суботи {date_formatted}:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )



async def admin_set_parity_working_saturday(callback: CallbackQuery, conn):
    parts = callback.data.split(":")
    date_str, par = parts[1], parts[2]
    from queue_bot.services.working_days import is_saturday_archived
    if is_saturday_archived(date_str):
        await callback.answer("Ця субота вже в архіві. Запис доступний лише для перегляду.", show_alert=True)
        return
    from queue_bot.services.working_days import get_working_saturday, set_working_saturday
    ws = get_working_saturday(conn, date_str)
    if ws:
        week_type = None if par == "auto" else par
        set_working_saturday(conn, date_str, ws["replaces_day"], week_type=week_type, description=ws.get("description") or "")
        par_uk = "авто" if par == "auto" else ("Чисельник" if par == "numerator" else "Знаменник")
        await callback.answer(f"Тип тижня: {par_uk}!")
    sat_date = date.fromisoformat(date_str)
    await render_working_saturday_panel(callback, conn, sat_date)



async def admin_cancel_working_saturday(callback: CallbackQuery, conn):
    date_str = callback.data.split(":", 1)[1]
    from queue_bot.services.working_days import is_saturday_archived
    if is_saturday_archived(date_str):
        await callback.answer("Ця субота вже в архіві. Запис доступний лише для перегляду.", show_alert=True)
        return
    from queue_bot.services.working_days import remove_working_saturday
    remove_working_saturday(conn, date_str)
    admins.log_action(conn, callback.from_user.id, "remove_working_saturday", f"date={date_str}")
    await callback.answer("Суботу повернуто у вихідний!")
    sat_date = date.fromisoformat(date_str)
    await render_working_saturday_panel(callback, conn, sat_date)



async def admin_view_working_saturday_schedule(callback: CallbackQuery, conn):
    date_str = callback.data.split(":", 1)[1]
    from queue_bot.services import schedule
    from queue_bot import config
    sat_date = date.fromisoformat(date_str)
    sch_data = schedule.load_schedule_data(config.SCHEDULE_PATH)
    text = schedule.format_schedule_for_date(conn, sch_data, sat_date, config.STUDENT_GROUP)
    kb = [
        [InlineKeyboardButton(text="🔙 Назад до налаштувань", callback_data=f"ws_back:{date_str}")],
    ]
    await callback.answer()
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))



async def admin_other_working_saturdays(callback: CallbackQuery, conn):
    from queue_bot.services.working_days import get_upcoming_saturdays, get_working_saturday
    saturdays = get_upcoming_saturdays(count=4)
    kb = []
    for s in saturdays:
        s_str = s.isoformat()
        s_fmt = s.strftime("%d.%m.%Y")
        ws = get_working_saturday(conn, s_str)
        status_icon = "🏢" if ws else "🏖"
        kb.append([InlineKeyboardButton(text=f"{status_icon} {s_fmt}", callback_data=f"ws_goto:{s_str}")])

    cur_date_str = callback.data.split(":", 1)[1]
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"ws_back:{cur_date_str}")],)
    await callback.answer()
    await callback.message.edit_text(
        "📅 <b>Оберіть суботу для налаштування:</b>\n"
        "🏢 — робочий день\n"
        "🏖 — вихідний день",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )



async def admin_goto_working_saturday(callback: CallbackQuery, conn):
    date_str = callback.data.split(":", 1)[1]
    sat_date = date.fromisoformat(date_str)
    await callback.answer()
    await render_working_saturday_panel(callback, conn, sat_date)



async def admin_back_working_saturday(callback: CallbackQuery, conn):
    date_str = callback.data.split(":", 1)[1]
    sat_date = date.fromisoformat(date_str)
    await callback.answer()
    await render_working_saturday_panel(callback, conn, sat_date)



async def admin_broadcast_working_saturday_prompt(callback: CallbackQuery, conn):
    date_str = callback.data.split(":", 1)[1]
    from queue_bot.services.working_days import get_working_saturday, DAY_NAMES_UK
    from queue_bot.services.updates import get_active_user_ids
    from queue_bot.services import schedule
    from queue_bot import config
    sat_date = date.fromisoformat(date_str)
    date_formatted = sat_date.strftime("%d.%m.%Y")
    ws = get_working_saturday(conn, date_str)
    if not ws:
        await callback.answer("Субота не налаштована як робоча.", show_alert=True)
        return

    from queue_bot.services.working_days import is_saturday_archived
    if is_saturday_archived(date_str):
        await callback.answer("Ця субота вже в архіві.", show_alert=True)
        return

    rep_day_uk = DAY_NAMES_UK.get(ws["replaces_day"], ws["replaces_day"].capitalize())
    w_type = ws.get("week_type") or schedule.get_week_type(sat_date)
    w_type_uk = "Знаменник" if w_type == "denominator" else "Чисельник"

    sch_data = schedule.load_schedule_data(config.SCHEDULE_PATH)
    actual_lessons, reps = schedule.get_actual_schedule(conn, sch_data, sat_date, config.STUDENT_GROUP)

    reps_note = f"\n⚠️ <i>Враховано замін: {len(reps)}</i>" if reps else ""
    announcement_text = (
        f"📢 <b>Увага! Оголошення щодо робочої суботи</b>\n\n"
        f"🗓 <b>Субота, {date_formatted}</b> — навчальний день!\n"
        f"• Навчання проходитиме за розкладом: <b>{rep_day_uk}</b> ({w_type_uk})\n"
        f"• Кількість пар: <b>{len(actual_lessons)}</b>{reps_note}\n\n"
        "💡 <i>Актуальний розклад занять та заміни можна переглянути за допомогою кнопки «📅 Розклад» або команди /schedule.</i>"
    )

    uids = get_active_user_ids(conn)
    kb = [
        [
            InlineKeyboardButton(text=f"✅ Надіслати всім ({len(uids)} студ.)", callback_data=f"ws_bc_send:{date_str}"),
        ],
        [
            InlineKeyboardButton(text="🔙 Скасувати", callback_data=f"ws_back:{date_str}"),
        ],
    ]
    await callback.answer()
    await callback.message.edit_text(
        f"👀 <b>Попередній перегляд оголошення:</b>\n\n"
        f"{announcement_text}\n\n"
        f"➖➖➖➖➖➖➖➖\n"
        f"Надіслати це повідомлення всім <b>{len(uids)}</b> користувачам групи?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )



async def admin_broadcast_working_saturday_send(callback: CallbackQuery, conn):
    date_str = callback.data.split(":", 1)[1]
    from queue_bot.services.working_days import get_working_saturday, DAY_NAMES_UK
    from queue_bot.services.updates import get_active_user_ids, broadcast_message_to_users
    from queue_bot.services import schedule
    from queue_bot import config
    sat_date = date.fromisoformat(date_str)
    date_formatted = sat_date.strftime("%d.%m.%Y")
    ws = get_working_saturday(conn, date_str)
    if not ws:
        await callback.answer("Субота не налаштована як робоча.", show_alert=True)
        return

    from queue_bot.services.working_days import is_saturday_archived
    if is_saturday_archived(date_str):
        await callback.answer("Ця субота вже в архіві.", show_alert=True)
        return

    rep_day_uk = DAY_NAMES_UK.get(ws["replaces_day"], ws["replaces_day"].capitalize())
    w_type = ws.get("week_type") or schedule.get_week_type(sat_date)
    w_type_uk = "Знаменник" if w_type == "denominator" else "Чисельник"

    sch_data = schedule.load_schedule_data(config.SCHEDULE_PATH)
    actual_lessons, reps = schedule.get_actual_schedule(conn, sch_data, sat_date, config.STUDENT_GROUP)
    reps_note = f"\n⚠️ <i>Враховано замін: {len(reps)}</i>" if reps else ""

    announcement_text = (
        f"📢 <b>Увага! Оголошення щодо робочої суботи</b>\n\n"
        f"🗓 <b>Субота, {date_formatted}</b> — навчальний день!\n"
        f"• Навчання проходитиме за розкладом: <b>{rep_day_uk}</b> ({w_type_uk})\n"
        f"• Кількість пар: <b>{len(actual_lessons)}</b>{reps_note}\n\n"
        "💡 <i>Актуальний розклад занять та заміни можна переглянути за допомогою кнопки «📅 Розклад» або команди /schedule.</i>"
    )

    uids = get_active_user_ids(conn)
    await callback.message.edit_text(f"⏳ Розсилаю оголошення для {len(uids)} користувачів...")
    success, errors = await broadcast_message_to_users(callback.bot, uids, announcement_text)
    admins.log_action(conn, callback.from_user.id, "broadcast_working_saturday", f"date={date_str} success={success} errors={errors}")
    await callback.answer("Оголошення надіслано!")

    await callback.message.answer(
        f"✅ <b>Оголошення успішно надіслано!</b>\n\n"
        f"• Доставлено: <b>{success}</b>\n"
        f"• Помилок: <b>{errors}</b>"
    )
    await render_working_saturday_panel(callback, conn, sat_date)





