import html
import logging
import re

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message, ReplyKeyboardMarkup

from handlers.common import (
    find_session_by_label,
    format_entry,
    format_queue,
    get_admin_queues_menu,
    get_admin_reply_menu,
    get_admin_settings_menu,
    get_admin_students_menu,
    get_admin_users_menu,
    get_paginated_reply_keyboard,
    get_user_card_menu,
    make_keyboard,
    make_reply_keyboard,
    notify_next_students,
    paginate,
    queue_label,
    session_names,
    show,
)
from services import admins, queues, users
from services.errors import BotError, PermissionDenied

log = logging.getLogger(__name__)
router = Router()

MAX_POSITION = 60


class AdminOnly(BaseMiddleware):
    """Перша лінія захисту. Сервіси додатково перевіряють роль самі."""

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id
        if not admins.is_admin(data["conn"], user_id):
            raise PermissionDenied("У вас немає прав адміністратора.")
        return await handler(event, data)


async def _is_admin_filter(event, conn) -> bool:
    user = getattr(event, "from_user", None)
    if not user:
        return False
    return admins.is_admin(conn, user.id)


router.callback_query.filter(_is_admin_filter)
router.message.filter(_is_admin_filter)
router.callback_query.middleware(AdminOnly())
router.message.middleware(AdminOnly())


class AdminPanelState(StatesGroup):
    in_queues_menu = State()
    in_students_menu = State()
    in_settings_menu = State()
    select_queue_for_view = State()
    select_queue_for_add_student = State()
    select_queue_for_order = State()
    select_queue_for_close = State()
    select_subject_for_report = State()
    add_student_names = State()
    confirm_finish_queue = State()


async def admin_state_filter(message: Message, state: FSMContext) -> bool:
    current_state = await state.get_state()
    return bool(
        current_state
        and (
            current_state.startswith("AdminPanelState")
            or current_state.startswith("AdminOrderState")
            or current_state.startswith("AdminManageState")
            or current_state.startswith("AdminUserState")
            or current_state.startswith("Admin")
        )
    )


class AdminUserState(StatesGroup):
    in_users_menu = State()
    browsing_users = State()
    viewing_card = State()
    adding_user = State()
    searching_user = State()
    editing_name = State()
    confirming_name_edit = State()
    confirming_delete = State()


class AdminOrderState(StatesGroup):
    picking_student = State()
    managing_entry = State()
    entering_number = State()
    entering_name = State()


class AdminManageState(StatesGroup):
    add_admin_id = State()
    select_admin_to_remove = State()
    history_view = State()
    entering_broadcast = State()
    confirming_broadcast = State()


def get_open_session(conn, session_id: int):
    session = queues.get_session(conn, session_id)
    if session is None or session["status"] == "archived":
        raise BotError("Ця черга вже завершена або не існує.")
    return session


def get_admin_queue_menu(is_open: bool) -> ReplyKeyboardMarkup:
    lock_text = "🔒 Закрити запис" if is_open else "🔓 Відкрити запис"
    rows = [
        ["➕ Додати студентів", "🔢 Керувати студентами"],
        [lock_text, "📦 Завершити чергу"],
        ["🔄 Оновити", "🔙 Назад"],
    ]
    return make_reply_keyboard(rows)


def get_admin_student_menu(is_blocked: bool) -> ReplyKeyboardMarkup:
    block_btn = "🟢 Розблокувати" if is_blocked else "⛔ Заблокувати"
    rows = [
        ["⬆️ Вище", "⬇️ Нижче"],
        ["🔢 Задати номер", "✏️ Змінити ім'я"],
        [block_btn, "✅ Позначити «Здав»"],
        ["❌ Видалити з черги"],
        ["🔙 До списку студентів", "🔙 Назад"],
    ]
    return make_reply_keyboard(rows)


async def open_admin_queue_manager(message: Message, conn, subjects, session_id: int, note: str = ""):
    session = get_open_session(conn, session_id)
    entries = queues.get_entries(conn, session_id)
    text = format_queue(subjects, session, entries, show_id=True)
    if note:
        text = f"{note}\n\n{text}"
    await message.answer(text, reply_markup=get_admin_queue_menu(session["status"] == "open"))


async def open_admin_student_list(message: Message, conn, subjects, session_id: int, note: str = "", page: int = 1):
    session = get_open_session(conn, session_id)
    entries = queues.get_entries(conn, session_id)
    if not entries:
        await open_admin_queue_manager(message, conn, subjects, session_id, note="У черзі немає студентів.")
        return
    page_entries, cur_page, total_pages = paginate(entries, page, per_page=8)
    rows = [[f"👤 {format_entry(e, show_id=True)}"] for e in page_entries]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад до черги")
    text = format_queue(subjects, session, entries, show_id=True) + "\n\nОберіть студента для дій на кнопках внизу 👇"
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
    status_info = "⛔ Заблокований" if user and user.get("is_blocked") else "✅ Активний"
    lines.append(f"👤 Студент: <b>{user_name}</b>{tg_info} — {status_info}")
    lines.append(f"📍 Поточна позиція в черзі: <b>{entry['position']}</b>")
    lines.append("")
    lines.append("Оберіть дію на кнопках внизу 👇")
    text = "\n".join(lines)
    if note:
        text = f"{note}\n\n{text}"

    is_blocked = bool(user and user.get("is_blocked"))
    await message.answer(text, reply_markup=get_admin_student_menu(is_blocked))


# ---------- Головна адмін-панель та категорії ----------

@router.message(F.text.in_({"⚡ Адмін-панель", "🔙 В адмін-панель"}), StateFilter("*"))
async def admin_panel_open(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "⚡ <b>Адмін-панель</b>\n\nОберіть категорію на кнопках нижче 👇",
        reply_markup=get_admin_reply_menu(),
    )


@router.message(F.text == "📋 Черги", StateFilter("*"))
async def admin_category_queues(message: Message, state: FSMContext):
    await state.set_state(AdminPanelState.in_queues_menu)
    await message.answer(
        "📋 <b>Керування чергами</b>\n\nОберіть дію нижче 👇",
        reply_markup=get_admin_queues_menu(),
    )


@router.message(F.text == "👥 Студенти", StateFilter("*"))
async def admin_category_students(message: Message, state: FSMContext):
    await state.set_state(AdminPanelState.in_students_menu)
    await message.answer(
        "👥 <b>Керування студентами</b>\n\nОберіть дію нижче 👇",
        reply_markup=get_admin_students_menu(),
    )


@router.message(F.text == "👥 Користувачі", StateFilter("*"))
async def admin_category_users(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AdminUserState.in_users_menu)
    await message.answer(
        "👥 <b>Користувачі</b>\n\nОберіть дію нижче 👇",
        reply_markup=get_admin_users_menu(),
    )


def format_user_card(user) -> str:
    is_placeholder = bool(user["telegram_id"] and user["telegram_id"] < 0)
    tg_status = "ще не прив’язано" if is_placeholder else "прив’язано"
    status_text = "⛔ заблокований" if user.get("is_blocked") else "активний"
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


async def show_users_page(message: Message, state: FSMContext, conn, page: int = 1):
    users_list, current_page, total_pages = users.list_users(conn, page=page, per_page=8)
    if not users_list:
        await message.answer("Користувачів у базі ще немає.", reply_markup=get_admin_users_menu())
        return

    await state.set_state(AdminUserState.browsing_users)
    await state.update_data(users_page=current_page)

    lines = ["👥 <b>Користувачі</b>", ""]
    for idx, u in enumerate(users_list, 1):
        lines.append(f"{idx}. {html.escape(u['full_name'])}")
    lines.append("")
    lines.append(f"Сторінка {current_page}/{total_pages}")
    text = "\n".join(lines)

    user_buttons = [f"👤 #{u['id']} {u['full_name']}" for u in users_list]
    row_pairs = [user_buttons[i:i + 2] for i in range(0, len(user_buttons), 2)]
    markup = get_paginated_reply_keyboard(row_pairs, current_page, total_pages, back_text="🔙 В адмін-панель")
    await message.answer(text, reply_markup=markup)


@router.message(F.text == "📋 Список користувачів", StateFilter("*"))
async def admin_list_users_start(message: Message, state: FSMContext, conn):
    await state.clear()
    await show_users_page(message, state, conn, page=1)


@router.message(AdminUserState.browsing_users, F.text.in_({"⬅️", "➡️"}))
async def admin_paginate_users(message: Message, state: FSMContext, conn):
    data = await state.get_data()
    page = data.get("users_page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await show_users_page(message, state, conn, page=page)


@router.message(F.text.startswith("👤 #"), StateFilter("*"))
async def admin_open_user_card(message: Message, state: FSMContext, conn):
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


@router.message(F.text == "🔙 До користувачів", StateFilter("*"))
async def admin_back_to_users(message: Message, state: FSMContext, conn):
    data = await state.get_data()
    page = data.get("users_page", 1)
    await show_users_page(message, state, conn, page=page)


@router.message(F.text == "➕ Додати користувача", StateFilter("*"))
async def admin_start_add_user(message: Message, state: FSMContext):
    await state.set_state(AdminUserState.adding_user)
    await message.answer(
        "➕ <b>Додавання користувача</b>\n\n"
        "Введіть ПІБ студента (двома словами, наприклад: <code>Петренко Іван</code>):",
        reply_markup=make_reply_keyboard([["↩️ Скасувати"]]),
    )


@router.message(AdminUserState.adding_user, F.text)
async def admin_finish_add_user(message: Message, state: FSMContext, conn):
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


@router.message(F.text.in_({"🔍 Знайти користувача", "🔍 Шукати знову"}), StateFilter("*"))
async def admin_start_search_user(message: Message, state: FSMContext):
    await state.set_state(AdminUserState.searching_user)
    await message.answer(
        "🔍 <b>Пошук користувача</b>\n\n"
        "Введіть частину імені або прізвища:",
        reply_markup=make_reply_keyboard([["↩️ Скасувати"]]),
    )


@router.message(AdminUserState.searching_user, F.text)
async def admin_finish_search_user(message: Message, state: FSMContext, conn):
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

    lines = [f"🔍 <b>Результати пошуку:</b> ({len(results)})", ""]
    for idx, u in enumerate(results[:10], 1):
        lines.append(f"{idx}. {html.escape(u['full_name'])}")
    if len(results) > 10:
        lines.append("")
        lines.append(f"... та ще {len(results) - 10} знайдених користувачів.")

    user_buttons = [f"👤 #{u['id']} {u['full_name']}" for u in results[:8]]
    row_pairs = [user_buttons[i:i + 2] for i in range(0, len(user_buttons), 2)]
    row_pairs.append(["🔙 До користувачів"])
    await message.answer("\n".join(lines), reply_markup=make_reply_keyboard(row_pairs))


@router.message(F.text == "✏️ Змінити ПІБ", AdminUserState.viewing_card)
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


@router.message(AdminUserState.editing_name, F.text)
async def admin_receive_new_name(message: Message, state: FSMContext, conn):
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


@router.message(AdminUserState.confirming_name_edit, F.text)
async def admin_confirm_new_name(message: Message, state: FSMContext, conn):
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


@router.message(F.text.in_({"⛔ Заблокувати", "🟢 Розблокувати"}), AdminUserState.viewing_card)
async def admin_toggle_user_block(message: Message, state: FSMContext, conn):
    data = await state.get_data()
    user_id = data.get("current_user_id")
    if not user_id:
        raise BotError("Користувача не обрано.")
    new_state = users.admin_toggle_block_user(conn, message.from_user.id, user_id)
    if new_state:
        queues.remove_user_from_all_queues(conn, message.from_user.id, user_id)
        note = "⛔ <b>Користувача заблоковано</b> (знято з усіх черг)."
    else:
        note = "🟢 <b>Користувача розблоковано.</b>"

    updated = users.get_user_by_id(conn, user_id)
    await message.answer(f"{note}\n\n" + format_user_card(updated), reply_markup=get_user_card_menu(new_state))


@router.message(F.text == "🗑 Видалити", AdminUserState.viewing_card)
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
        f"Ви впевнені, що хочете безповоротно видалити <b>{html.escape(user['full_name'])}</b>?\n\n"
        f"⚠️ Користувача можна видалити лише якщо він не має записів чи історії в чергах.",
        reply_markup=make_reply_keyboard([["⚠️ Підтвердити видалення"], ["↩️ Скасувати"]]),
    )


@router.message(AdminUserState.confirming_delete, F.text)
async def admin_confirm_delete_user(message: Message, state: FSMContext, conn):
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
    page = data.get("users_page", 1)
    await show_users_page(message, state, conn, page=page)


@router.message(F.text == "⚙️ Налаштування", StateFilter("*"))
async def admin_category_settings(message: Message, state: FSMContext, conn=None):
    from services.maintenance import is_maintenance_active
    is_maint = is_maintenance_active(conn) if conn else False
    await state.set_state(AdminPanelState.in_settings_menu)
    await message.answer(
        "⚙️ <b>Налаштування</b>\n\nОберіть розділ нижче 👇",
        reply_markup=get_admin_settings_menu(is_maintenance=is_maint),
    )


@router.message(
    F.text.in_({"🔙 Назад", "🔙 В адмін-панель"}),
    StateFilter(
        AdminPanelState.in_queues_menu,
        AdminPanelState.in_students_menu,
        AdminPanelState.in_settings_menu,
        AdminUserState.in_users_menu,
        AdminUserState.browsing_users,
        AdminPanelState.select_queue_for_view,
        AdminPanelState.select_queue_for_add_student,
        AdminPanelState.select_queue_for_order,
        AdminPanelState.select_queue_for_close,
        AdminPanelState.select_subject_for_report,
        AdminManageState.history_view,
    ),
)
async def admin_back_to_main_panel(message: Message, state: FSMContext):
    await admin_panel_open(message, state)


# ---------- Вибір черги для дій адміна ----------

@router.message(F.text == "👀 Активні черги", StateFilter("*"))
async def admin_pick_active_queues(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    sessions = queues.list_sessions(conn, ("open", "closed"))
    if not sessions:
        await message.answer("Активних черг немає.", reply_markup=get_admin_queues_menu())
        return
    await state.set_state(AdminPanelState.select_queue_for_view)
    await state.update_data(page=1)
    page_sessions, cur_page, total_pages = paginate(sessions, 1, per_page=8)
    rows = [[f"📋 {queue_label(subjects, s)}"] for s in page_sessions]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад")
    await message.answer("👀 Оберіть чергу для перегляду та керування 👇", reply_markup=markup)


@router.message(AdminPanelState.select_queue_for_view, F.text.in_({"⬅️", "➡️"}))
async def admin_paginate_active_queues(message: Message, state: FSMContext, conn, subjects):
    sessions = queues.list_sessions(conn, ("open", "closed"))
    data = await state.get_data()
    page = data.get("page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await state.update_data(page=page)
    page_sessions, cur_page, total_pages = paginate(sessions, page, per_page=8)
    rows = [[f"📋 {queue_label(subjects, s)}"] for s in page_sessions]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад")
    await message.answer("Оберіть чергу на кнопках внизу 👇", reply_markup=markup)


@router.message(F.text == "📝 Додати студента", StateFilter("*"))
async def admin_pick_add_student(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    sessions = queues.list_sessions(conn, ("open", "closed"))
    if not sessions:
        await message.answer("Активних черг немає.", reply_markup=get_admin_students_menu())
        return
    await state.set_state(AdminPanelState.select_queue_for_add_student)
    await state.update_data(page=1)
    page_sessions, cur_page, total_pages = paginate(sessions, 1, per_page=8)
    rows = [[f"📋 {queue_label(subjects, s)}"] for s in page_sessions]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад")
    await message.answer("📝 Оберіть чергу, в яку додати студентів 👇", reply_markup=markup)


@router.message(AdminPanelState.select_queue_for_add_student, F.text.in_({"⬅️", "➡️"}))
async def admin_paginate_add_student_queues(message: Message, state: FSMContext, conn, subjects):
    sessions = queues.list_sessions(conn, ("open", "closed"))
    data = await state.get_data()
    page = data.get("page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await state.update_data(page=page)
    page_sessions, cur_page, total_pages = paginate(sessions, page, per_page=8)
    rows = [[f"📋 {queue_label(subjects, s)}"] for s in page_sessions]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад")
    await message.answer("Оберіть чергу на кнопках внизу 👇", reply_markup=markup)


@router.message(F.text.in_({"🔢 Змінити порядок", "✅ Позначити здачу"}), StateFilter("*"))
async def admin_pick_order_queue(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    sessions = queues.list_sessions(conn, ("open", "closed"))
    if not sessions:
        await message.answer("Активних черг немає.", reply_markup=get_admin_students_menu())
        return
    await state.set_state(AdminPanelState.select_queue_for_order)
    await state.update_data(page=1)
    page_sessions, cur_page, total_pages = paginate(sessions, 1, per_page=8)
    rows = [[f"📋 {queue_label(subjects, s)}"] for s in page_sessions]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад")
    await message.answer("🔢 Оберіть чергу для керування студентами 👇", reply_markup=markup)


@router.message(AdminPanelState.select_queue_for_order, F.text.in_({"⬅️", "➡️"}))
async def admin_paginate_order_queues(message: Message, state: FSMContext, conn, subjects):
    sessions = queues.list_sessions(conn, ("open", "closed"))
    data = await state.get_data()
    page = data.get("page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await state.update_data(page=page)
    page_sessions, cur_page, total_pages = paginate(sessions, page, per_page=8)
    rows = [[f"📋 {queue_label(subjects, s)}"] for s in page_sessions]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад")
    await message.answer("Оберіть чергу на кнопках внизу 👇", reply_markup=markup)


@router.message(F.text.in_({"🔒 Відкрити/закрити", "🔒 Відкрити/закрити запис"}), StateFilter("*"))
async def admin_pick_close_queue(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    sessions = queues.list_sessions(conn, ("open", "closed"))
    if not sessions:
        await message.answer("Активних черг немає.", reply_markup=get_admin_queues_menu())
        return
    await state.set_state(AdminPanelState.select_queue_for_close)
    await state.update_data(page=1)
    page_sessions, cur_page, total_pages = paginate(sessions, 1, per_page=8)
    rows = [[f"📋 {queue_label(subjects, s)}"] for s in page_sessions]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад")
    await message.answer("🔒 Оберіть чергу для зміни статусу запису 👇", reply_markup=markup)


@router.message(AdminPanelState.select_queue_for_close, F.text.in_({"⬅️", "➡️"}))
async def admin_paginate_close_queues(message: Message, state: FSMContext, conn, subjects):
    sessions = queues.list_sessions(conn, ("open", "closed"))
    data = await state.get_data()
    page = data.get("page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await state.update_data(page=page)
    page_sessions, cur_page, total_pages = paginate(sessions, page, per_page=8)
    rows = [[f"📋 {queue_label(subjects, s)}"] for s in page_sessions]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад")
    await message.answer("Оберіть чергу на кнопках внизу 👇", reply_markup=markup)


# ---------- Звіти по предметах ----------

@router.message(F.text.in_({"📊 Звіти", "📊 Звіти за предметами", "📊 Інший предмет"}), StateFilter("*"))
async def admin_pick_report_subject(message: Message, state: FSMContext, subjects):
    await state.clear()
    await state.set_state(AdminPanelState.select_subject_for_report)
    await state.update_data(page=1)
    page_subjects, cur_page, total_pages = paginate(subjects, 1, per_page=8)
    rows = [[f"📊 {s['name']}"] for s in page_subjects]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад")
    await message.answer(
        "📊 <b>Звіти по предметах</b>\n\nОберіть предмет для формування звіту та файлу таблиці 👇",
        reply_markup=markup,
    )


@router.message(AdminPanelState.select_subject_for_report, F.text.in_({"⬅️", "➡️"}))
async def admin_paginate_report_subjects(message: Message, state: FSMContext, subjects):
    data = await state.get_data()
    page = data.get("page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await state.update_data(page=page)
    page_subjects, cur_page, total_pages = paginate(subjects, page, per_page=8)
    rows = [[f"📊 {s['name']}"] for s in page_subjects]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад")
    await message.answer("Оберіть предмет зі списку нижче 👇", reply_markup=markup)


@router.message(AdminPanelState.select_subject_for_report, F.text.startswith("📊"))
async def admin_generate_subject_report(message: Message, state: FSMContext, conn, subjects):
    raw_name = message.text.removeprefix("📊").strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == raw_name.lower()), None)
    if not matched_sub:
        await message.answer("⚠️ Предмет не знайдено. Оберіть зі списку на кнопках внизу 👇")
        return

    report = queues.get_subject_report(conn, matched_sub["id"])
    teachers = ", ".join(t["name"] for t in matched_sub.get("teachers", [])) or "—"

    lines = [
        f"📊 <b>Звіт по предмету: {html.escape(matched_sub['name'])}</b>",
        f"👨‍🏫 Викладачі: {html.escape(teachers)}",
        f"📁 Проведено занять: <b>{report['sessions_count']}</b>",
        f"✅ Всього здач: <b>{report['total_passed']}</b>",
    ]

    if report["active_session"]:
        lines.append("⚡ Триває активна черга.")

    lines.append("")
    lines.append("🏆 <b>Рейтинг здач студентів:</b>")
    if not report["students"]:
        lines.append("Записів немає.")
    else:
        for idx, s in enumerate(report["students"][:15], 1):
            pos_info = f" (в черзі: #{s['current_position']})" if s["current_position"] else ""
            lines.append(f"{idx}. {html.escape(s['name'])} — <b>{s['passed_count']}</b> ✅{pos_info}")
        if len(report["students"]) > 15:
            lines.append(f"... та ще {len(report['students']) - 15} студентів у таблиці нижче.")

    text_msg = "\n".join(lines)

    csv_rows = [
        "Прізвище та ім'я;Здано робіт;Спроб (не здав);Місце в поточній черзі;Дати здач"
    ]
    for s in report["students"]:
        dates_str = ", ".join(s["dates"]) if s["dates"] else "-"
        cur_pos = str(s["current_position"]) if s["current_position"] else "-"
        csv_rows.append(f"{s['name']};{s['passed_count']};{s['not_passed_count']};{cur_pos};{dates_str}")

    csv_data = "\r\n".join(csv_rows).encode("utf-8-sig")
    safe_name = re.sub(r"[^\w\s-]", "", matched_sub["name"]).replace(" ", "_")
    file = BufferedInputFile(csv_data, filename=f"report_{safe_name}.csv")

    menu_rows = [
        ["📊 Інший предмет"],
        ["🔙 Назад"],
    ]

    await message.answer(text_msg)
    await message.answer_document(
        file,
        caption=f"📄 Таблиця: {html.escape(matched_sub['name'])}",
        reply_markup=make_reply_keyboard(menu_rows),
    )


# ---------- Вибір конкретної черги зі списків ----------

@router.message(AdminPanelState.select_queue_for_view, F.text.startswith("📋 "))
async def admin_selected_queue_view(message: Message, state: FSMContext, conn, subjects):
    session = find_session_by_label(conn, subjects, message.text)
    if not session:
        await message.answer("⚠️ Чергу не знайдено. Оберіть зі списку на кнопках внизу 👇")
        return
    await state.clear()
    await state.update_data(session_id=session["id"])
    await open_admin_queue_manager(message, conn, subjects, session["id"])


@router.message(AdminPanelState.select_queue_for_add_student, F.text.startswith("📋 "))
async def admin_selected_queue_add_student(message: Message, state: FSMContext, conn, subjects):
    session = find_session_by_label(conn, subjects, message.text)
    if not session:
        await message.answer("⚠️ Чергу не знайдено. Оберіть зі списку на кнопках внизу 👇")
        return
    await state.set_state(AdminPanelState.add_student_names)
    await state.update_data(session_id=session["id"])
    await message.answer(
        f"📝 <b>Додавання студентів:</b>\n"
        f"{html.escape(queue_label(subjects, session))}\n\n"
        "Надішліть ПІБ студента або список (кожне з нового рядка).\n"
        "Можна вказувати номер (наприклад: <code>3. Петренко Іван</code>).",
        reply_markup=make_reply_keyboard([["🔙 Назад до черги"], ["🔙 Назад"]]),
    )


@router.message(AdminPanelState.select_queue_for_order, F.text.startswith("📋 "))
async def admin_selected_queue_order(message: Message, state: FSMContext, conn, subjects):
    session = find_session_by_label(conn, subjects, message.text)
    if not session:
        await message.answer("⚠️ Чергу не знайдено. Оберіть зі списку на кнопках внизу 👇")
        return
    await state.set_state(AdminOrderState.picking_student)
    await state.update_data(session_id=session["id"], students_page=1)
    await open_admin_student_list(message, conn, subjects, session["id"], page=1)


@router.message(AdminPanelState.select_queue_for_close, F.text.startswith("📋 "))
async def admin_selected_queue_close(message: Message, state: FSMContext, conn, subjects):
    session = find_session_by_label(conn, subjects, message.text)
    if not session:
        await message.answer("⚠️ Чергу не знайдено. Оберіть зі списку на кнопках внизу 👇")
        return
    make_open = session["status"] == "closed"
    queues.set_signup_open(conn, message.from_user.id, session["id"], make_open)
    note = "🟢 <b>Запис відкрито</b>" if make_open else "🔴 <b>Запис закрито</b>"
    await state.clear()
    await state.update_data(session_id=session["id"])
    await open_admin_queue_manager(message, conn, subjects, session["id"], note=note)


# ---------- Керування обраною чергою ----------

@router.message(F.text == "➕ Додати студентів", StateFilter("*"))
async def admin_click_add_students(message: Message, state: FSMContext, conn, subjects):
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


@router.message(AdminPanelState.add_student_names, F.text)
async def receive_student_names(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if message.text in {"🔙 Назад до черги", "🔙 Назад"}:
        await state.clear()
        if session_id and message.text == "🔙 Назад до черги":
            await state.update_data(session_id=session_id)
            await open_admin_queue_manager(message, conn, subjects, session_id)
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

    await open_admin_queue_manager(message, conn, subjects, session_id, note=result_text)


@router.message(F.text.in_({"🔒 Закрити запис", "🔓 Відкрити запис"}), StateFilter("*"))
async def toggle_signup_in_manager(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        raise BotError("Чергу не обрано.")
    session = get_open_session(conn, session_id)
    make_open = session["status"] == "closed"
    queues.set_signup_open(conn, message.from_user.id, session_id, make_open)
    note = "🟢 <b>Запис відкрито</b>" if make_open else "🔴 <b>Запис закрито</b>"
    await open_admin_queue_manager(message, conn, subjects, session_id, note=note)


@router.message(F.text == "🔢 Керувати студентами", StateFilter("*"))
async def start_managing_students(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        raise BotError("Чергу не обрано.")
    await state.set_state(AdminOrderState.picking_student)
    await state.update_data(session_id=session_id, students_page=1)
    await open_admin_student_list(message, conn, subjects, session_id, page=1)


@router.message(AdminOrderState.picking_student, F.text.in_({"⬅️", "➡️"}))
async def admin_paginate_students(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    page = data.get("students_page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await state.update_data(students_page=page)
    await open_admin_student_list(message, conn, subjects, session_id, page=page)


@router.message(AdminOrderState.picking_student, F.text.startswith("👤"))
async def select_student_from_list(message: Message, state: FSMContext, conn, subjects):
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


@router.message(F.text == "🔙 До списку студентів", StateFilter("*"))
async def back_to_students_list(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        await admin_panel_open(message, state)
        return
    page = data.get("students_page", 1)
    await state.set_state(AdminOrderState.picking_student)
    await state.update_data(session_id=session_id)
    await open_admin_student_list(message, conn, subjects, session_id, page=page)


@router.message(F.text == "🔙 Назад до черги", admin_state_filter)
async def back_to_queue_manager_from_any(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        await admin_panel_open(message, state)
        return
    await state.clear()
    await state.update_data(session_id=session_id)
    await open_admin_queue_manager(message, conn, subjects, session_id)


# ---------- Дії над студентом ----------

@router.message(F.text == "⬆️ Вище", StateFilter("*"))
async def student_move_up(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    entry_id = data.get("entry_id")
    if not entry_id:
        raise BotError("Студента не обрано.")
    queues.move_up(conn, message.from_user.id, entry_id)
    await open_admin_student_manager(message, conn, subjects, entry_id, note="⬆️ Переміщено вище.")


@router.message(F.text == "⬇️ Нижче", StateFilter("*"))
async def student_move_down(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    entry_id = data.get("entry_id")
    if not entry_id:
        raise BotError("Студента не обрано.")
    queues.move_down(conn, message.from_user.id, entry_id)
    await open_admin_student_manager(message, conn, subjects, entry_id, note="⬇️ Переміщено нижче.")


@router.message(F.text == "🔢 Задати номер", StateFilter("*"))
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


@router.message(AdminOrderState.entering_number, F.text)
async def student_receive_number(message: Message, state: FSMContext, conn, subjects):
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


@router.message(F.text == "✏️ Змінити ім'я", StateFilter("*"))
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


@router.message(AdminOrderState.entering_name, F.text)
async def student_receive_rename(message: Message, state: FSMContext, conn, subjects):
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


@router.message(F.text.in_({"⛔ Заблокувати", "🟢 Розблокувати"}), StateFilter("*"))
async def student_toggle_block(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    entry_id = data.get("entry_id")
    session_id = data.get("session_id")
    if not entry_id:
        raise BotError("Студента не обрано.")
    entry = queues.get_entry(conn, entry_id)
    user = conn.execute("SELECT * FROM users WHERE id = ?", (entry["user_id"],)).fetchone() if entry["user_id"] else None
    if not user:
        raise BotError("Користувача не знайдено.")
    new_state = users.toggle_block_user(conn, user["id"])
    if new_state:
        queues.remove_user_from_all_queues(conn, message.from_user.id, user["id"])
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


@router.message(F.text == "✅ Позначити «Здав»", StateFilter("*"))
async def student_mark_passed_by_admin(message: Message, state: FSMContext, conn, subjects):
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


@router.message(F.text == "❌ Видалити з черги", StateFilter("*"))
async def student_remove_by_admin(message: Message, state: FSMContext, conn, subjects):
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


# ---------- Завершення черги ----------

@router.message(F.text == "📦 Завершити чергу", StateFilter("*"))
async def ask_finish_queue(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        raise BotError("Чергу не обрано.")
    session = get_open_session(conn, session_id)
    await state.set_state(AdminPanelState.confirm_finish_queue)
    rows = [
        ["📦 Завершити й архівувати"],
        ["📦 Нова черга (порожня)"],
        ["🔙 Назад до черги"],
    ]
    await message.answer(
        f"Завершити заняття?\n{html.escape(queue_label(subjects, session))}\n\nОберіть дію нижче 👇",
        reply_markup=make_reply_keyboard(rows),
    )


@router.message(F.text == "📦 Завершити й архівувати", StateFilter("*"))
async def confirm_finish_archive(message: Message, state: FSMContext, conn):
    data = await state.get_data()
    session_id = data.get("session_id")
    queues.finish_session(conn, message.from_user.id, session_id, create_new=False)
    await state.clear()
    await message.answer("✅ Чергу успішно архівовано.", reply_markup=get_admin_reply_menu())


@router.message(F.text == "📦 Нова черга (порожня)", StateFilter("*"))
async def confirm_finish_new(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    new_id = queues.finish_session(conn, message.from_user.id, session_id, create_new=True)
    await state.clear()
    await state.update_data(session_id=new_id)
    await open_admin_queue_manager(message, conn, subjects, new_id, note=f"✅ Чергу архівовано. Нову чергу створено (№{new_id})!")


# ---------- Історія черг ----------

@router.message(F.text.in_({"📦 Історія", "📦 Історія черг", "🔙 До списку історії"}), StateFilter("*"))
async def history_list(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    sessions = queues.list_sessions(conn, ("archived",))
    if not sessions:
        await message.answer("Історія порожня.", reply_markup=get_admin_queues_menu())
        return
    await state.set_state(AdminManageState.history_view)
    await state.update_data(page=1)
    page_sessions, cur_page, total_pages = paginate(sessions, 1, per_page=8)
    rows = [[f"📦 №{s['id']} {queue_label(subjects, s)}"] for s in page_sessions]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад")
    await message.answer("Історія черг. Оберіть чергу для перегляду деталей 👇", reply_markup=markup)


@router.message(AdminManageState.history_view, F.text.in_({"⬅️", "➡️"}))
async def admin_paginate_history(message: Message, state: FSMContext, conn, subjects):
    sessions = queues.list_sessions(conn, ("archived",))
    data = await state.get_data()
    page = data.get("page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await state.update_data(page=page)
    page_sessions, cur_page, total_pages = paginate(sessions, page, per_page=8)
    rows = [[f"📦 №{s['id']} {queue_label(subjects, s)}"] for s in page_sessions]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад")
    await message.answer("Оберіть чергу з історії 👇", reply_markup=markup)


HISTORY_MARK = {
    "completed": "✅ здав",
    "transferred": "↪ перенесено",
    "removed": "✖ не здав / видалено",
    "left": "вийшов із черги",
}


@router.message(AdminManageState.history_view, F.text.startswith("📦 №"))
async def history_details_view(message: Message, state: FSMContext, conn, subjects):
    match = re.match(r"^📦 №(\d+)", message.text)
    if not match:
        return
    session_id = int(match.group(1))
    session = queues.get_session(conn, session_id)
    if not session:
        raise BotError("Чергу не знайдено.")
    lines = [f"{html.escape(r['full_name'])} — {HISTORY_MARK.get(r['status'], r['status'])}" for r in queues.get_history(conn, session_id)]
    header = format_queue(subjects, session, [])
    body = "\n".join(lines) or "Записів немає."
    text = header.replace("Черга порожня.", "") + body
    rows = [["🔙 До списку історії", "🔙 Назад"]]
    await message.answer(text, reply_markup=make_reply_keyboard(rows))


# ---------- Заблоковані користувачі ----------

@router.message(F.text.in_({"⛔ Заблоковані", "⛔ Заблоковані користувачі"}), StateFilter("*"))
async def show_blocked_list(message: Message, state: FSMContext, conn):
    await state.clear()
    blocked = users.list_blocked_users(conn)
    if not blocked:
        from services.maintenance import is_maintenance_active
        await message.answer("Заблокованих користувачів немає.", reply_markup=get_admin_settings_menu(is_maintenance=is_maintenance_active(conn)))
        return
    rows = []
    for u in blocked[:8]:
        tg_id_str = f" (ID: {u['telegram_id']})" if u["telegram_id"] and u["telegram_id"] > 0 else ""
        rows.append([f"🟢 Розблокувати: {u['full_name']}{tg_id_str}"])
    rows.append(["🔙 Назад"])
    await message.answer(
        "⛔ <b>Заблоковані користувачі:</b>\nНатисніть на студента, щоб розблокувати його 👇",
        reply_markup=make_reply_keyboard(rows),
    )


@router.message(F.text.startswith("🟢 Розблокувати: "), StateFilter("*"))
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
    users.toggle_block_user(conn, user["id"])
    await message.answer(f"🟢 Студента «{html.escape(user['full_name'])}» успішно розблоковано!")
    await show_blocked_list(message, state, conn)


# ---------- Керування адміністраторами ----------

@router.message(F.text == "👥 Адміністратори", StateFilter("*"))
async def admins_menu(message: Message, state: FSMContext):
    await state.clear()
    rows = [
        ["📋 Список адміністраторів", "➕ Додати адміністратора"],
        ["❌ Видалити адміністратора", "🔙 Назад"],
    ]
    await message.answer("Керування адміністраторами. Оберіть дію на кнопках внизу 👇", reply_markup=make_reply_keyboard(rows))


@router.message(F.text == "📋 Список адміністраторів", StateFilter("*"))
async def list_admins_message(message: Message, conn):
    adm_list = admins.list_admins(conn)
    lines = ["👥 <b>Адміністратори:</b>"]
    for r in adm_list:
        name = r["full_name"] or "без реєстрації"
        lines.append(f"• <b>{html.escape(name)}</b> (<code>{r['telegram_id']}</code>)")
    rows = [
        ["➕ Додати адміністратора", "❌ Видалити адміністратора"],
        ["🔙 Назад"],
    ]
    await message.answer("\n".join(lines), reply_markup=make_reply_keyboard(rows))


@router.message(F.text == "➕ Додати адміністратора", StateFilter("*"))
async def ask_admin_id(message: Message, state: FSMContext):
    await state.set_state(AdminManageState.add_admin_id)
    rows = [["↩️ Скасувати", "🔙 Назад"]]
    await message.answer("Введіть числовий Telegram ID нового адміністратора:", reply_markup=make_reply_keyboard(rows))


@router.message(AdminManageState.add_admin_id, F.text)
async def receive_admin_id(message: Message, state: FSMContext, conn):
    if message.text in {"↩️ Скасувати", "🔙 Скасувати", "🔙 Назад"}:
        await admins_menu(message, state)
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
    await list_admins_message(message, conn)


@router.message(F.text == "❌ Видалити адміністратора", StateFilter("*"))
async def choose_admin_to_remove(message: Message, state: FSMContext, conn):
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


@router.message(AdminManageState.select_admin_to_remove, F.text.startswith("❌ Видалити: "))
async def confirm_remove_admin_text(message: Message, state: FSMContext, conn):
    match = re.search(r"\((\d+)\)$", message.text)
    if not match:
        return
    target_id = int(match.group(1))
    admins.remove_admin(conn, message.from_user.id, target_id)
    await state.clear()
    await message.answer(f"✅ Права адміністратора для ID <code>{target_id}</code> видалено.")
    await list_admins_message(message, conn)


@router.message(AdminManageState.select_admin_to_remove, F.text.in_({"↩️ Скасувати", "🔙 Скасувати", "🔙 Назад"}))
async def cancel_remove_admin(message: Message, state: FSMContext):
    await state.clear()
    await admins_menu(message, state)


# ---------- Розсилка повідомлень усім користувачам ----------

@router.message(F.text == "📢 Оголошення для всіх", StateFilter("*"))
async def start_broadcast_flow(message: Message, state: FSMContext, conn):
    from services.updates import get_active_user_ids
    uids = get_active_user_ids(conn)
    await state.set_state(AdminManageState.entering_broadcast)
    await message.answer(
        f"📢 <b>Розсилка повідомлення всім користувачам</b>\n\n"
        f"Зареєстровано активних користувачів: <b>{len(uids)}</b>.\n\n"
        "Надішліть текст оголошення (підтримується HTML-розмітка: <code>&lt;b&gt;жирний&lt;/b&gt;</code> тощо):\n"
        "Або натисніть «🔙 Скасувати» нижче 👇",
        reply_markup=make_reply_keyboard([["🔙 Скасувати"]]),
    )


@router.message(AdminManageState.entering_broadcast, F.text)
async def preview_broadcast_message(message: Message, state: FSMContext, conn):
    if message.text in {"🔙 Скасувати", "🔙 Назад", "↩️ Скасувати"}:
        await state.clear()
        await admin_category_settings(message, state)
        return
    text = message.text.strip()
    if not text:
        await message.answer("Введіть текст оголошення або натисніть «🔙 Скасувати»:")
        return

    from services.updates import get_active_user_ids
    uids = get_active_user_ids(conn)
    await state.set_state(AdminManageState.confirming_broadcast)
    await state.update_data(broadcast_text=text)

    await message.answer(
        f"👀 <b>Попередній перегляд оголошення:</b>\n\n{text}\n\n"
        f"➖➖➖➖➖➖➖➖\n"
        f"Надіслати це повідомлення усім <b>{len(uids)}</b> користувачам?",
        reply_markup=make_reply_keyboard([["✅ Надіслати всім"], ["🔙 Скасувати"]]),
    )


@router.message(AdminManageState.confirming_broadcast, F.text)
async def perform_broadcast(message: Message, state: FSMContext, conn):
    if message.text in {"🔙 Скасувати", "🔙 Назад", "↩️ Скасувати"}:
        await state.clear()
        await admin_category_settings(message, state)
        return
    if message.text != "✅ Надіслати всім":
        await message.answer("Оберіть дію на кнопках: «✅ Надіслати всім» або «🔙 Скасувати» 👇")
        return

    data = await state.get_data()
    text = data.get("broadcast_text")
    if not text:
        await state.clear()
        await admin_category_settings(message, state)
        return

    from services.updates import get_active_user_ids, broadcast_message_to_users
    uids = get_active_user_ids(conn)
    await message.answer(f"⏳ Розпочинаю розсилку для {len(uids)} користувачів...")
    success, errors = await broadcast_message_to_users(message.bot, uids, text)
    admins.log_action(conn, message.from_user.id, "manual_broadcast", f"success={success} errors={errors}")
    await state.clear()
    from services.maintenance import is_maintenance_active
    await message.answer(
        f"✅ <b>Розсилку успішно завершено!</b>\n\n"
        f"• Доставлено: <b>{success}</b>\n"
        f"• Помилок: <b>{errors}</b>",
        reply_markup=get_admin_settings_menu(is_maintenance=is_maintenance_active(conn)),
    )


# ---------- Режим технічних робіт ----------

@router.message(F.text.in_({"🛠 Увімкнути техроботи", "🟢 Вимкнути техроботи"}), StateFilter("*"))
async def toggle_maintenance(message: Message, state: FSMContext, conn):
    from services.maintenance import get_maintenance_end_time, is_maintenance_active, set_maintenance_mode
    cur = is_maintenance_active(conn)
    new_state = not cur
    set_maintenance_mode(conn, new_state, "20:15")
    end_t = get_maintenance_end_time(conn)
    admins.log_action(conn, message.from_user.id, "toggle_maintenance", f"active={new_state}")

    if new_state:
        status_txt = (
            f"🛠 <b>Режим техробіт УВІМКНЕНО!</b> (орієнтовно до {end_t})\n\n"
            f"• Доступ до запису та відкриття черг заблоковано.\n"
            f"• Кнопки у звичайних користувачів деактивовано."
        )
    else:
        status_txt = (
            "🟢 <b>Режим техробіт ВИМКНЕНО!</b>\n\n"
            "Бот знову працює у штатному режимі для всіх користувачів."
        )

    await message.answer(
        status_txt,
        reply_markup=get_admin_settings_menu(is_maintenance=new_state),
    )
