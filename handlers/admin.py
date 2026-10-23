import html
import logging
import re

from datetime import date

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
)

from handlers.common import (
    find_session_by_label,
    format_entry,
    format_queue,
    get_admin_polls_menu,
    get_admin_queues_menu,
    get_admin_reply_menu,
    get_admin_reports_menu,
    get_admin_schedule_menu,
    get_admin_settings_menu,
    get_admin_students_menu,
    get_admin_subjects_menu,
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
import config
import subjects as subjects_module
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
router.message.filter(F.chat.type == "private", _is_admin_filter)
router.callback_query.middleware(AdminOnly())
router.message.middleware(AdminOnly())


class AdminPanelState(StatesGroup):
    in_main_menu = State()
    in_queues_menu = State()
    in_students_menu = State()
    in_reports_menu = State()
    in_settings_menu = State()
    in_schedule_menu = State()
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


class AdminSubjectsState(StatesGroup):
    in_menu = State()
    select_subject_for_teacher = State()
    entering_teacher_name = State()
    entering_new_subject_name = State()
    entering_new_subject_teacher = State()
    select_subject_for_rename = State()
    entering_subject_new_name = State()
    select_subject_for_remove_teacher = State()
    select_teacher_to_remove = State()
    select_subject_to_delete = State()
    confirming_delete_subject = State()


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
    entering_maintenance_time = State()


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
        from handlers.navigation import AdminNavigation
        await state.set_state(AdminNavigation.queue)
        await state.update_data(session_id=session_id, ui_mode='admin')
    session = get_open_session(conn, session_id)
    entries = queues.get_entries(conn, session_id)
    text = '⚡ <b>Режим адміна</b>\n\n' + format_queue(subjects, session, entries, show_id=True)
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


# ---------- Головна адмін-панель та категорії ----------

@router.message(F.text == "⚡ Керувати чергою (Адмін)", StateFilter("*"))
async def admin_quick_manage_from_student_view(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        await admin_panel_open(message, state, conn=conn)
        return
    await state.clear()
    await state.update_data(session_id=session_id)
    await open_admin_queue_manager(
        message,
        conn,
        subjects,
        session_id,
        note="⚡ <b>Режим старости/адміна:</b> тут можна додати студентів за списком, змінити порядок або завершити чергу.", state=state,
    )


@router.message(F.text == "👤 Як студент (Моє місце)", StateFilter("*"))
async def admin_switch_to_student_queue_view(message: Message, state: FSMContext, conn, subjects):
    from handlers.student import open_queue_screen
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        await admin_panel_open(message, state, conn=conn)
        return
    await state.clear()
    await state.update_data(session_id=session_id)
    await open_queue_screen(
        message,
        conn,
        subjects,
        session_id,
        note="👤 <b>Режим студента:</b> тут ви можете самі записатися в чергу, відмітити «Здав» або обмінятися місцем.",
    )


@router.message(F.text.in_({"⚡ Адмін-панель", "🔙 В адмін-панель"}), StateFilter("*"))
async def admin_panel_open(message: Message, state: FSMContext, conn=None):
    await state.clear()
    await state.set_state(AdminPanelState.in_main_menu)
    owner_flag = admins.is_owner(conn, message.from_user.id) if conn is not None else admins.is_owner(None, message.from_user.id)
    info_lines = ["⚡ <b>Адмін-панель старости (ПІ-231)</b>"]
    if conn is not None:
        try:
            from services import polls
            from services.maintenance import is_maintenance_active
            act_q = len(queues.list_sessions(conn, ("open", "closed")))
            act_p = len(polls.list_polls(conn, status="active", limit=50))
            maint_str = "🛠 Техроботи" if is_maintenance_active(conn) else "🟢 Працює"
            info_lines.append(
                f"<i>Черг: <b>{act_q}</b> • Опитувань: <b>{act_p}</b> • Статус: <b>{maint_str}</b></i>"
            )
        except Exception:
            pass
    info_lines.extend([
        "",
        "🧭 <b>Швидка навігація по розділах:</b>",
        "• <b>📋 Черги та студенти</b> — додати студентів у чергу, змінити порядок, позначити здачу, закрити/архівувати чергу.",
        "• <b>📅 Розклад і заміни</b> — додати заміни за фото, робоча субота, оновити розклад.",
        "• <b>🗳 Опитування</b> — створити голосування для групи (з таймером або без).",
        "• <b>📊 Звіти та статистика</b> — загальна успішність та вивантаження таблиць Excel/CSV.",
        "• <b>👥 Список групи</b> — база ПІБ студентів групи та блокування.",
    ])
    if owner_flag:
        info_lines.append("• <b>⚙️ Налаштування</b> — баг-репорти, оголошення на всю групу, техроботи, список адмінів.")
    info_lines.extend([
        "",
        "💡 <i>Щоб самому записатися в чергу або здати лабу як студент — натисніть <b>«👤 Режим студента»</b> внизу!</i>",
    ])
    await message.answer(
        "\n".join(info_lines),
        reply_markup=get_admin_reply_menu(is_owner=owner_flag),
    )


@router.message(F.text.in_({"📋 Черги та студенти", "📋 Черги", "🎓 Студенти в чергах", "👥 Студенти", "🔙 Назад до черг"}), StateFilter("*"))
async def admin_category_queues(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AdminPanelState.in_queues_menu)
    await message.answer(
        "📋 <b>Керування чергами та студентами в чергах</b>\n\n"
        "• <b>👀 Активні черги</b> — відкрити будь-яку чергу (всередині можна додати студентів, змінити порядок, закрити чи завершити її)\n"
        "• <b>➕ Створити чергу</b> — відкрити нову чергу на пару або консультацію\n"
        "• <b>📝 Додати студента</b> — швидко вписати студента (або список) у чергу\n"
        "• <b>🔢 Змінити порядок</b> — посунути студента вище/нижче, задати номер або позначити «Здав»\n"
        "• <b>🔒 Відкрити/закрити</b> — тимчасово зупинити або відновити запис\n"
        "• <b>📚 Предмети та викладачі</b> — перегляд, закріплення викладачів та створення предметів\n"
        "• <b>📦 Історія</b> — переглянути завершені черги",
        reply_markup=get_admin_queues_menu(),
    )


async def admin_category_students(message: Message, state: FSMContext):
    await admin_category_queues(message, state)


# ---------- Керування предметами та викладачами ----------

@router.message(F.text.in_({"📚 Предмети та викладачі", "📚 Предмети", "🔙 До предметів"}), StateFilter("*"))
async def admin_view_subjects_menu(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    await state.set_state(AdminSubjectsState.in_menu)
    is_owner = admins.is_owner(conn, message.from_user.id)

    lines = ["📚 <b>Список дисциплін та закріплених викладачів:</b>\n"]
    for idx, s in enumerate(subjects, 1):
        teachers_str = ", ".join(t["name"] for t in s.get("teachers", [])) or "— немає викладачів —"
        lines.append(f"{idx}. <b>{html.escape(s['name'])}</b>\n   👨‍🏫 {html.escape(teachers_str)}")

    lines.append("")
    if is_owner:
        lines.append("<i>Вам як власнику бота також доступні редагування та видалення.</i>")
    else:
        lines.append("<i>Як адміністратор ви можете закріплювати викладачів або додавати нові предмети.</i>")

    await message.answer("\n".join(lines), reply_markup=get_admin_subjects_menu(is_owner=is_owner))


@router.message(F.text == "👨‍🏫 Додати викладача", StateFilter("*"))
async def admin_add_teacher_start(message: Message, state: FSMContext, subjects):
    await state.set_state(AdminSubjectsState.select_subject_for_teacher)
    rows = [[f"📚 {s['name']}"] for s in subjects]
    rows.append(["🔙 До предметів"])
    await message.answer(
        "Оберіть предмет, до якого потрібно прикріпити викладача 👇",
        reply_markup=make_reply_keyboard(rows),
    )


@router.message(AdminSubjectsState.select_subject_for_teacher, F.text.startswith("📚 "))
async def admin_add_teacher_subject_picked(message: Message, state: FSMContext, subjects):
    sub_name = message.text.removeprefix("📚 ").strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == sub_name.lower()), None)
    if not matched_sub:
        await message.answer("Оберіть предмет на кнопках внизу:")
        return

    await state.set_state(AdminSubjectsState.entering_teacher_name)
    await state.update_data(sub_id=matched_sub["id"], sub_name=matched_sub["name"])

    all_teachers = subjects_module.list_all_teachers(subjects)
    curr_ids = {t["id"] for t in matched_sub.get("teachers", [])}
    other_teachers = [t["name"] for t in all_teachers if t["id"] not in curr_ids]

    rows = []
    for i in range(0, len(other_teachers), 2):
        rows.append(other_teachers[i:i+2])
    rows.append(["🔙 До предметів"])

    await message.answer(
        f"👨‍🏫 <b>Прикріплення викладача до предмета «{html.escape(matched_sub['name'])}»</b>\n\n"
        "Оберіть викладача на кнопках нижче або <b>введіть ПІБ текстом</b> (наприклад: <code>Барабаш Д.С.</code>) 👇",
        reply_markup=make_reply_keyboard(rows),
    )


@router.message(AdminSubjectsState.entering_teacher_name, F.text)
async def admin_add_teacher_name_received(message: Message, state: FSMContext, conn, subjects):
    if message.text in {"🔙 До предметів", "🔙 Скасувати", "↩️ Скасувати"}:
        await admin_view_subjects_menu(message, state, conn, subjects)
        return

    teacher_name = message.text.strip()
    data = await state.get_data()
    sub_id = data.get("sub_id")
    sub_name = data.get("sub_name", "Предмет")

    try:
        teacher = subjects_module.add_teacher_to_subject(
            conn, subjects, sub_id, teacher_name, path=config.SUBJECTS_PATH
        )
        admins.log_action(conn, message.from_user.id, "add_teacher_to_subject", f"{sub_name}: {teacher['name']}")
        await message.answer(
            f"✅ <b>Викладача «{html.escape(teacher['name'])}» успішно прикріплено до предмета «{html.escape(sub_name)}»!</b>\n"
            "Він уже доступний усім студентам для створення черг."
        )
    except BotError as e:
        await message.answer(f"⚠️ {e}\nСпробуйте ще раз або натисніть «🔙 До предметів»:")
        return

    await admin_view_subjects_menu(message, state, conn, subjects)


@router.message(F.text == "➕ Додати предмет", StateFilter("*"))
async def admin_add_new_subject_start(message: Message, state: FSMContext):
    await state.set_state(AdminSubjectsState.entering_new_subject_name)
    await message.answer(
        "➕ <b>Створення нового предмета</b>\n\n"
        "Введіть повну назву предмета (наприклад: <code>Програмування мовою Rust</code>):",
        reply_markup=make_reply_keyboard([["🔙 До предметів"]]),
    )


@router.message(AdminSubjectsState.entering_new_subject_name, F.text)
async def admin_add_new_subject_name_received(message: Message, state: FSMContext, conn, subjects):
    if message.text in {"🔙 До предметів", "🔙 Скасувати", "↩️ Скасувати"}:
        await admin_view_subjects_menu(message, state, conn, subjects)
        return

    sub_name = message.text.strip()
    if len(sub_name) < 2 or len(sub_name) > 80:
        await message.answer("Назва предмета має містити від 2 до 80 символів. Спробуйте ще раз:")
        return

    await state.set_state(AdminSubjectsState.entering_new_subject_teacher)
    await state.update_data(new_sub_name=sub_name)

    all_teachers = subjects_module.list_all_teachers(subjects)
    rows = []
    for i in range(0, len(all_teachers), 2):
        rows.append([t["name"] for t in all_teachers[i:i+2]])
    rows.append(["🔙 До предметів"])

    await message.answer(
        f"Тепер вкажіть першого викладача для предмета «<b>{html.escape(sub_name)}</b>».\n\n"
        "Оберіть на кнопках або <b>введіть ПІБ текстом</b> (наприклад: <code>Когут В.С.</code>) 👇",
        reply_markup=make_reply_keyboard(rows),
    )


@router.message(AdminSubjectsState.entering_new_subject_teacher, F.text)
async def admin_add_new_subject_teacher_received(message: Message, state: FSMContext, conn, subjects):
    if message.text in {"🔙 До предметів", "🔙 Скасувати", "↩️ Скасувати"}:
        await admin_view_subjects_menu(message, state, conn, subjects)
        return

    teacher_name = message.text.strip()
    data = await state.get_data()
    sub_name = data.get("new_sub_name")

    try:
        new_sub = subjects_module.add_new_subject(
            conn, subjects, sub_name, teacher_name, path=config.SUBJECTS_PATH
        )
        admins.log_action(conn, message.from_user.id, "add_new_subject", f"{new_sub['name']}: {teacher_name}")
        await message.answer(
            f"🎉 <b>Предмет «{html.escape(new_sub['name'])}» успішно створено!</b>\n"
            f"Викладач: <b>{html.escape(teacher_name)}</b>.\n"
            "Тепер студенти та адміни можуть відкривати черги на цей предмет."
        )
    except BotError as e:
        await message.answer(f"⚠️ {e}\nСпробуйте ще раз або натисніть «🔙 До предметів»:")
        return

    await admin_view_subjects_menu(message, state, conn, subjects)


# ---------- Дії власника: перейменування, відкріплення, видалення ----------

@router.message(F.text == "✏️ Перейменувати предмет", StateFilter("*"))
async def admin_rename_subject_start(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    await state.set_state(AdminSubjectsState.select_subject_for_rename)
    rows = [[f"✏️ {s['name']}"] for s in subjects]
    rows.append(["🔙 До предметів"])
    await message.answer("Оберіть предмет для перейменування 👇", reply_markup=make_reply_keyboard(rows))


@router.message(AdminSubjectsState.select_subject_for_rename, F.text.startswith("✏️ "))
async def admin_rename_subject_picked(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    sub_name = message.text.removeprefix("✏️ ").strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == sub_name.lower()), None)
    if not matched_sub:
        await message.answer("Оберіть предмет на кнопках внизу:")
        return

    await state.set_state(AdminSubjectsState.entering_subject_new_name)
    await state.update_data(sub_id=matched_sub["id"], old_name=matched_sub["name"])
    await message.answer(
        f"Введіть нову назву для предмета «<b>{html.escape(matched_sub['name'])}</b>»:",
        reply_markup=make_reply_keyboard([["🔙 До предметів"]]),
    )


@router.message(AdminSubjectsState.entering_subject_new_name, F.text)
async def admin_rename_subject_name_received(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    if message.text in {"🔙 До предметів", "🔙 Скасувати", "↩️ Скасувати"}:
        await admin_view_subjects_menu(message, state, conn, subjects)
        return

    new_name = message.text.strip()
    data = await state.get_data()
    sub_id = data.get("sub_id")
    old_name = data.get("old_name")

    try:
        subjects_module.rename_subject(conn, subjects, sub_id, new_name, path=config.SUBJECTS_PATH)
        admins.log_action(conn, message.from_user.id, "rename_subject", f"{old_name} -> {new_name}")
        await message.answer(f"✅ Предмет перейменовано на «<b>{html.escape(new_name)}</b>»!")
    except BotError as e:
        await message.answer(f"⚠️ {e}\nСпробуйте ще раз:")
        return

    await admin_view_subjects_menu(message, state, conn, subjects)


@router.message(F.text == "🗑 Відкріпити викладача", StateFilter("*"))
async def admin_remove_teacher_start(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    await state.set_state(AdminSubjectsState.select_subject_for_remove_teacher)
    rows = [[f"📚 {s['name']}"] for s in subjects if len(s.get("teachers", [])) > 1]
    if not rows:
        await message.answer(
            "Усі предмети мають лише по одному викладачу (відкріпити не можна).",
            reply_markup=get_admin_subjects_menu(is_owner=True),
        )
        return
    rows.append(["🔙 До предметів"])
    await message.answer("Оберіть предмет, від якого потрібно відкріпити викладача 👇", reply_markup=make_reply_keyboard(rows))


@router.message(AdminSubjectsState.select_subject_for_remove_teacher, F.text.startswith("📚 "))
async def admin_remove_teacher_subject_picked(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    sub_name = message.text.removeprefix("📚 ").strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == sub_name.lower()), None)
    if not matched_sub:
        await message.answer("Оберіть предмет на кнопках внизу:")
        return

    await state.set_state(AdminSubjectsState.select_teacher_to_remove)
    await state.update_data(sub_id=matched_sub["id"], sub_name=matched_sub["name"])
    rows = [[f"❌ {t['name']}"] for t in matched_sub["teachers"]]
    rows.append(["🔙 До предметів"])
    await message.answer(
        f"Оберіть викладача для відкріплення від предмета «{html.escape(matched_sub['name'])}» 👇",
        reply_markup=make_reply_keyboard(rows),
    )


@router.message(AdminSubjectsState.select_teacher_to_remove, F.text.startswith("❌ "))
async def admin_remove_teacher_confirmed(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    t_name = message.text.removeprefix("❌ ").strip()
    data = await state.get_data()
    sub_id = data.get("sub_id")
    sub_name = data.get("sub_name")

    matched_sub = subjects_module.find_subject(subjects, sub_id)
    if not matched_sub:
        await admin_view_subjects_menu(message, state, conn, subjects)
        return

    matched_t = next((t for t in matched_sub["teachers"] if t["name"].lower() == t_name.lower()), None)
    if not matched_t:
        await message.answer("Викладача не знайдено.")
        return

    try:
        subjects_module.remove_teacher_from_subject(conn, subjects, sub_id, matched_t["id"], path=config.SUBJECTS_PATH)
        admins.log_action(conn, message.from_user.id, "remove_teacher_from_subject", f"{sub_name}: {t_name}")
        await message.answer(f"✅ Викладача «{html.escape(t_name)}» успішно відкріплено від предмета «{html.escape(sub_name)}».")
    except BotError as e:
        await message.answer(f"⚠️ {e}")

    await admin_view_subjects_menu(message, state, conn, subjects)


@router.message(F.text == "🗑 Видалити предмет", StateFilter("*"))
async def admin_delete_subject_start(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    await state.set_state(AdminSubjectsState.select_subject_to_delete)
    rows = [[f"🗑 {s['name']}"] for s in subjects]
    rows.append(["🔙 До предметів"])
    await message.answer("Оберіть предмет, який потрібно видалити зі списку 👇", reply_markup=make_reply_keyboard(rows))


@router.message(AdminSubjectsState.select_subject_to_delete, F.text.startswith("🗑 "))
async def admin_delete_subject_picked(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    sub_name = message.text.removeprefix("🗑 ").strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == sub_name.lower()), None)
    if not matched_sub:
        await message.answer("Оберіть предмет на кнопках внизу:")
        return

    await state.set_state(AdminSubjectsState.confirming_delete_subject)
    await state.update_data(del_sub_id=matched_sub["id"], del_sub_name=matched_sub["name"])
    rows = [["⚠️ Підтвердити видалення"], ["🔙 До предметів"]]
    await message.answer(
        f"⚠️ <b>Ви впевнені, що хочете видалити предмет «{html.escape(matched_sub['name'])}»?</b>\n\n"
        "Студенти більше не зможуть відкривати на нього черги.",
        reply_markup=make_reply_keyboard(rows),
    )


@router.message(AdminSubjectsState.confirming_delete_subject, F.text == "⚠️ Підтвердити видалення")
async def admin_delete_subject_confirmed(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    data = await state.get_data()
    sub_id = data.get("del_sub_id")
    sub_name = data.get("del_sub_name")

    try:
        subjects_module.delete_subject(conn, subjects, sub_id, path=config.SUBJECTS_PATH)
        admins.log_action(conn, message.from_user.id, "delete_subject", f"{sub_name}")
        await message.answer(f"✅ Предмет «{html.escape(sub_name)}» успішно видалено.")
    except BotError as e:
        await message.answer(f"⚠️ {e}")

    await admin_view_subjects_menu(message, state, conn, subjects)


@router.message(F.text == "📅 Розклад і заміни", StateFilter("*"))
async def admin_category_schedule(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AdminPanelState.in_schedule_menu)
    await message.answer(
        "📅 <b>Керування розкладом та замінами</b>\n\n"
        "• <b>📸 Додати заміни (фото)</b> — надіслати скріншот/фото таблиці замін для автоматичного розпізнавання та розсилки групі\n"
        "• <b>🔄 Активні заміни</b> — переглянути поточні збережені заміни\n"
        "• <b>🏢 Робоча субота</b> — призначити суботу навчальним днем за розкладом іншого дня\n"
        "• <b>📦 Архів замін</b> — переглянути історію попередніх замін\n"
        "• <b>📥 Отримати / 📤 Завантажити розклад</b> — редагування базового файлу <code>schedule.json</code>\n"
        "• <b>🗑 Очистити всі заміни</b> — видалити поточні заміни пар",
        reply_markup=get_admin_schedule_menu(),
    )


@router.message(F.text == "📸 Додати заміни (фото)", StateFilter("*"))
async def admin_add_replacement_photo_hint(message: Message):
    await message.answer(
        "📸 <b>Додавання замін за фотографією</b>\n\n"
        "Просто <b>надішліть фото або скріншот таблиці замін</b> прямо сюди в чат!\n\n"
        "1️⃣ Бот автоматично знайде пари групи <b>ПІ-231</b>.\n"
        "2️⃣ Покаже вам попередній перегляд із кнопкою <b>«✏️ Внести корективи»</b> (якщо потрібно підправити пару, аудиторію чи дату).\n"
        "3️⃣ Після натискання <b>«✅ Підтвердити заміни»</b> бот збереже їх і автоматично розішле сповіщення всій групі!",
        reply_markup=get_admin_schedule_menu(),
    )


@router.message(F.text == "📥 Отримати schedule.json", StateFilter("*"))
async def admin_export_schedule_action(message: Message, conn):
    from handlers.schedule import export_schedule_file
    await export_schedule_file(message, conn)


@router.message(F.text == "📤 Завантажити розклад", StateFilter("*"))
async def admin_upload_schedule_action(message: Message):
    await message.answer(
        "📤 <b>Як оновити розклад занять:</b>\n\n"
        "1. Натисніть кнопку <b>«📥 Отримати schedule.json»</b> та збережіть файл.\n"
        "2. Відкрийте файл на телефоні або комп'ютері та внесіть актуальні пари (предмети, аудиторії, викладачів).\n"
        "3. <b>Надішліть збережений файл <code>schedule.json</code> прямо сюди в чат як документ 📄.</b>\n\n"
        "Бот автоматично валідує формат JSON і миттєво оновить розклад для всієї групи!"
    )


@router.message(F.text == "🗑 Очистити всі заміни", StateFilter("*"))
async def admin_clear_replacements_action(message: Message, conn):
    from handlers.schedule import request_clear_replacements
    await request_clear_replacements(message, conn)


@router.message(F.text == "🟢 Статус системи", StateFilter("*"))
async def admin_status_action(message: Message, conn):
    from handlers.schedule import show_bot_status
    await show_bot_status(message, conn)


# ---------- Робоча субота ----------

@router.message(F.text == "🏢 Робоча субота", StateFilter("*"))
async def admin_working_saturday_btn(message: Message, conn):
    from services.working_days import get_next_saturday
    sat = get_next_saturday()
    await render_working_saturday_panel(message, conn, sat)


async def render_working_saturday_panel(event: Message | CallbackQuery, conn, sat_date: date):
    from services.working_days import (
        get_working_saturday,
        DAY_NAMES_UK,
    )
    from services import schedule
    import config

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

    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup)
    else:
        await event.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ws_set:"))
async def admin_set_working_saturday_day(callback: CallbackQuery, conn):
    parts = callback.data.split(":")
    date_str, day_key = parts[1], parts[2]
    from services.working_days import set_working_saturday, DAY_NAMES_UK
    set_working_saturday(conn, date_str, day_key)
    admins.log_action(conn, callback.from_user.id, "set_working_saturday", f"date={date_str} day={day_key}")
    day_uk = DAY_NAMES_UK.get(day_key, day_key)
    await callback.answer(f"Встановлено розклад: {day_uk}!")
    sat_date = date.fromisoformat(date_str)
    await render_working_saturday_panel(callback, conn, sat_date)


@router.callback_query(F.data.startswith("ws_pick:"))
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


@router.callback_query(F.data.startswith("ws_par:"))
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


@router.callback_query(F.data.startswith("ws_setpar:"))
async def admin_set_parity_working_saturday(callback: CallbackQuery, conn):
    parts = callback.data.split(":")
    date_str, par = parts[1], parts[2]
    from services.working_days import get_working_saturday, set_working_saturday
    ws = get_working_saturday(conn, date_str)
    if ws:
        week_type = None if par == "auto" else par
        set_working_saturday(conn, date_str, ws["replaces_day"], week_type=week_type, description=ws.get("description") or "")
        par_uk = "авто" if par == "auto" else ("Чисельник" if par == "numerator" else "Знаменник")
        await callback.answer(f"Тип тижня: {par_uk}!")
    sat_date = date.fromisoformat(date_str)
    await render_working_saturday_panel(callback, conn, sat_date)


@router.callback_query(F.data.startswith("ws_cancel:"))
async def admin_cancel_working_saturday(callback: CallbackQuery, conn):
    date_str = callback.data.split(":", 1)[1]
    from services.working_days import remove_working_saturday
    remove_working_saturday(conn, date_str)
    admins.log_action(conn, callback.from_user.id, "remove_working_saturday", f"date={date_str}")
    await callback.answer("Суботу повернуто у вихідний!")
    sat_date = date.fromisoformat(date_str)
    await render_working_saturday_panel(callback, conn, sat_date)


@router.callback_query(F.data.startswith("ws_view:"))
async def admin_view_working_saturday_schedule(callback: CallbackQuery, conn):
    date_str = callback.data.split(":", 1)[1]
    from services import schedule
    import config
    sat_date = date.fromisoformat(date_str)
    sch_data = schedule.load_schedule_data(config.SCHEDULE_PATH)
    text = schedule.format_schedule_for_date(conn, sch_data, sat_date, config.STUDENT_GROUP)
    kb = [
        [InlineKeyboardButton(text="🔙 Назад до налаштувань", callback_data=f"ws_back:{date_str}")],
    ]
    await callback.answer()
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data.startswith("ws_other:"))
async def admin_other_working_saturdays(callback: CallbackQuery, conn):
    from services.working_days import get_upcoming_saturdays, get_working_saturday
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


@router.callback_query(F.data.startswith("ws_goto:"))
async def admin_goto_working_saturday(callback: CallbackQuery, conn):
    date_str = callback.data.split(":", 1)[1]
    sat_date = date.fromisoformat(date_str)
    await callback.answer()
    await render_working_saturday_panel(callback, conn, sat_date)


@router.callback_query(F.data.startswith("ws_back:"))
async def admin_back_working_saturday(callback: CallbackQuery, conn):
    date_str = callback.data.split(":", 1)[1]
    sat_date = date.fromisoformat(date_str)
    await callback.answer()
    await render_working_saturday_panel(callback, conn, sat_date)


@router.callback_query(F.data.startswith("ws_bc_prompt:"))
async def admin_broadcast_working_saturday_prompt(callback: CallbackQuery, conn):
    date_str = callback.data.split(":", 1)[1]
    from services.working_days import get_working_saturday, DAY_NAMES_UK
    from services.updates import get_active_user_ids
    from services import schedule
    import config
    sat_date = date.fromisoformat(date_str)
    date_formatted = sat_date.strftime("%d.%m.%Y")
    ws = get_working_saturday(conn, date_str)
    if not ws:
        await callback.answer("Субота не налаштована як робоча.", show_alert=True)
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


@router.callback_query(F.data.startswith("ws_bc_send:"))
async def admin_broadcast_working_saturday_send(callback: CallbackQuery, conn):
    date_str = callback.data.split(":", 1)[1]
    from services.working_days import get_working_saturday, DAY_NAMES_UK
    from services.updates import get_active_user_ids, broadcast_message_to_users
    from services import schedule
    import config
    sat_date = date.fromisoformat(date_str)
    date_formatted = sat_date.strftime("%d.%m.%Y")
    ws = get_working_saturday(conn, date_str)
    if not ws:
        await callback.answer("Субота не налаштована як робоча.", show_alert=True)
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



@router.message(F.text.in_({"👥 Список групи", "👥 База користувачів", "👥 Користувачі"}), StateFilter("*"))
async def admin_category_users(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AdminUserState.in_users_menu)
    await message.answer(
        "👥 <b>Список групи (база користувачів)</b>\n\n"
        "Тут зберігаються профілі всіх студентів групи ПІ-231.\n"
        "Оберіть дію нижче 👇",
        reply_markup=get_admin_users_menu(),
    )


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
    admins.require_owner(conn, message.from_user.id)
    from services.maintenance import is_maintenance_active
    is_maint = is_maintenance_active(conn) if conn else False
    await state.set_state(AdminPanelState.in_settings_menu)
    await message.answer(
        "⚙️ <b>Налаштування (Тільки для власника)</b>\n\nОберіть розділ нижче 👇",
        reply_markup=get_admin_settings_menu(is_maintenance=is_maint),
    )


@router.message(
    F.text.in_({"🔙 Назад", "🔙 В адмін-панель"}),
    StateFilter(
        AdminPanelState.in_main_menu,
        AdminPanelState.in_queues_menu,
        AdminPanelState.in_students_menu,
        AdminPanelState.in_reports_menu,
        AdminPanelState.in_settings_menu,
        AdminPanelState.in_schedule_menu,
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
async def admin_back_to_main_panel(message: Message, state: FSMContext, conn=None):
    await admin_panel_open(message, state, conn=conn)


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


# ---------- Звіти та статистика ----------

@router.message(F.text.in_({"📊 Звіти та статистика", "📊 Звіти", "📊 Звіти та таблиці"}), StateFilter("*"))
async def admin_category_reports(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AdminPanelState.in_reports_menu)
    await message.answer(
        "📊 <b>Звіти, графічні діаграми та аналітика ПІ-231</b>\n\n"
        "• <b>📈 Загальна статистика</b> — зведений візуальний мульти-дашборд (4 графіки на одному полотні) + текстовий звіт.\n"
        "• <b>🍩 Діаграма: Типи та статуси</b> — кільцеві діаграми (Donut Charts) категорій черг та результатів здач.\n"
        "• <b>📊 Діаграма: Предмети</b> — вертикальна стовпчикова діаграма (Column Chart) порівняння дисциплін.\n"
        "• <b>🏆 Діаграма: Топ студентів</b> — горизонтальна рейтингова діаграма (Leaderboard Bar Chart) лідерів здач.\n"
        "• <b>📚 Звіти за предметами</b> — комбінована діаграма та CSV-таблиця по конкретній дисципліні.\n"
        "• <b>📥 Експорт зведеної таблиці (CSV)</b> — єдиний файл Excel/CSV з матрицею всіх студентів та предметів.\n\n"
        "Оберіть потрібний тип звіту або діаграми нижче 👇",
        reply_markup=get_admin_reports_menu(),
    )


@router.message(F.text == "📈 Загальна статистика", StateFilter("*"))
async def admin_global_statistics_view(message: Message, conn, subjects):
    from services import charts

    stats = queues.get_global_statistics(conn, subjects)
    lines = [
        "📈 <b>Загальна статистика черг ПІ-231</b>\n",
        f"📁 <b>Проведено черг усього:</b> {stats['total_sessions']} "
        f"(активних: {stats['active_sessions']}, в архіві: {stats['archived_sessions']})",
        f"   ├ 📖 Звичайні пари: <b>{stats['lessons_count']}</b>",
        f"   ├ 💬 Консультації: <b>{stats['consults_count']}</b>",
        f"   └ 📑 Курсова робота: <b>{stats['cw_count']}</b>\n",
        f"✅ <b>Успішних здач робіт:</b> {stats['total_passed']}",
        f"🔄 <b>Не здано / перенесено:</b> {stats['total_removed']}",
        f"🚪 <b>Виходів із черги:</b> {stats['total_left']}",
        f"📊 <b>Конверсія здач:</b> {stats['pass_rate']}%\n",
        f"👥 <b>Студентів у базі:</b> {stats['registered_users'] + stats['placeholder_users']} "
        f"(прив'язали TG: {stats['registered_users']})\n",
    ]

    if stats["subjects"]:
        lines.append("📚 <b>Активність за предметами:</b>")
        for s in stats["subjects"]:
            lines.append(
                f"• <b>{html.escape(s['name'])}</b> — здач: <b>{s['passed']}</b> (черг: {s['sessions']})"
            )
        lines.append("")

    active_top = [st for st in stats["top_students"] if st["passed"] > 0]
    if active_top:
        lines.append("🏆 <b>Топ-10 студентів за кількістю здач:</b>")
        for idx, st in enumerate(active_top[:10], 1):
            medal = "🥇" if idx == 1 else ("🥈" if idx == 2 else ("🥉" if idx == 3 else f"{idx}."))
            lines.append(f"{medal} {html.escape(st['name'])} — <b>{st['passed']}</b> ✅")

    try:
        png_bytes = charts.generate_overview_dashboard_png(stats)
        chart_file = BufferedInputFile(png_bytes, filename="pi231_overview_dashboard.png")
        await message.answer_photo(
            chart_file,
            caption="📈 <b>Зведений аналітичний дашборд групи ПІ-231</b>",
        )
    except Exception as e:
        log.warning(f"Could not render overview dashboard chart: {e}")

    await message.answer("\n".join(lines), reply_markup=get_admin_reports_menu())


@router.message(F.text == "🍩 Діаграма: Типи та статуси", StateFilter("*"))
async def admin_chart_donut_status(message: Message, conn, subjects):
    from services import charts

    stats = queues.get_global_statistics(conn, subjects)
    png_bytes = charts.generate_donut_status_chart_png(stats)
    chart_file = BufferedInputFile(png_bytes, filename="pi231_donut_status.png")
    await message.answer_photo(
        chart_file,
        caption=(
            "🍩 <b>Кільцеві діаграми: Категорії черг та Статуси здач</b>\n"
            f"• Усього проведено черг: <b>{stats['total_sessions']}</b>\n"
            f"• Загальна конверсія успішних здач: <b>{stats['pass_rate']}%</b> ({stats['total_passed']} здано)"
        ),
        reply_markup=get_admin_reports_menu(),
    )


@router.message(F.text == "📊 Діаграма: Предмети", StateFilter("*"))
async def admin_chart_subjects_columns(message: Message, conn, subjects):
    from services import charts

    stats = queues.get_global_statistics(conn, subjects)
    png_bytes = charts.generate_subjects_column_chart_png(stats)
    chart_file = BufferedInputFile(png_bytes, filename="pi231_subjects_columns.png")
    await message.answer_photo(
        chart_file,
        caption=(
            "📊 <b>Стовпчикова діаграма активності за предметами</b>\n"
            "Порівняння кількості успішних здач робіт (зелений) та проведених черг (блакитний) по кожній дисципліні."
        ),
        reply_markup=get_admin_reports_menu(),
    )


@router.message(F.text == "🏆 Діаграма: Топ студентів", StateFilter("*"))
async def admin_chart_top_students(message: Message, conn, subjects):
    from services import charts

    stats = queues.get_global_statistics(conn, subjects)
    png_bytes = charts.generate_top_students_bar_chart_png(stats)
    chart_file = BufferedInputFile(png_bytes, filename="pi231_top_students.png")
    await message.answer_photo(
        chart_file,
        caption=(
            "🏆 <b>Горизонтальна рейтингова діаграма активності студентів</b>\n"
            "Лідери групи ПІ-231 за загальною кількістю успішно зданих робіт у чергах."
        ),
        reply_markup=get_admin_reports_menu(),
    )


@router.message(F.text == "📥 Експорт зведеної таблиці (CSV)", StateFilter("*"))
async def admin_export_global_csv(message: Message, conn, subjects):
    csv_bytes = queues.export_global_statistics_csv(conn, subjects)
    file = BufferedInputFile(csv_bytes, filename="pi231_full_statistics.csv")
    await message.answer_document(
        file,
        caption=(
            "📊 <b>Зведена таблиця успішності групи ПІ-231</b>\n"
            "Містить дані по кожному студенту та всіх предметах семестру (формат CSV для Excel / Google Таблиць)."
        ),
        reply_markup=get_admin_reports_menu(),
    )


@router.message(F.text.in_({"📚 Звіти за предметами", "📊 Звіти за предметами", "📊 Інший предмет"}), StateFilter("*"))
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

    try:
        from services import charts
        chart_bytes = charts.generate_subject_report_chart_png(matched_sub["name"], teachers, report)
        chart_photo = BufferedInputFile(chart_bytes, filename=f"chart_{safe_name}.png")
        await message.answer_photo(
            chart_photo,
            caption=f"📊 <b>Діаграма успішності: {html.escape(matched_sub['name'])}</b>",
        )
    except Exception as e:
        log.warning(f"Could not generate subject chart: {e}")

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
    await state.update_data(session_id=session["id"])
    from handlers.navigation import switch_admin
    await switch_admin(message, state, conn, subjects)


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
    await open_admin_queue_manager(message, conn, subjects, session["id"], note=note, state=state)


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
    await open_admin_queue_manager(message, conn, subjects, session_id, note=note, state=state)


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
    await open_admin_queue_manager(message, conn, subjects, session_id, state=state)


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
    new_state = users.admin_toggle_block_user(conn, message.from_user.id, user["id"])
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
    await open_admin_queue_manager(message, conn, subjects, new_id, note=f"✅ Чергу архівовано. Нову чергу створено (№{new_id})!", state=state)


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
    from services.restrictions import clear_restriction
    clear_restriction(conn, message.from_user.id, user['id'])
    await message.answer(f"🟢 Студента «{html.escape(user['full_name'])}» успішно розблоковано!")
    await show_blocked_list(message, state, conn)


# ---------- Керування адміністраторами ----------

@router.message(F.text == "👥 Адміністратори", StateFilter("*"))
async def admins_menu(message: Message, state: FSMContext, conn=None):
    admins.require_owner(conn, message.from_user.id)
    await state.clear()
    rows = [
        ["📋 Список адміністраторів", "➕ Додати адміністратора"],
        ["❌ Видалити адміністратора", "🔙 Назад"],
    ]
    await message.answer("Керування адміністраторами. Оберіть дію на кнопках внизу 👇", reply_markup=make_reply_keyboard(rows))


@router.message(F.text == "📋 Список адміністраторів", StateFilter("*"))
async def list_admins_message(message: Message, conn):
    admins.require_owner(conn, message.from_user.id)
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
async def ask_admin_id(message: Message, state: FSMContext, conn=None):
    admins.require_owner(conn, message.from_user.id)
    await state.set_state(AdminManageState.add_admin_id)
    rows = [["↩️ Скасувати", "🔙 Назад"]]
    await message.answer("Введіть числовий Telegram ID нового адміністратора:", reply_markup=make_reply_keyboard(rows))


@router.message(AdminManageState.add_admin_id, F.text)
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
    await list_admins_message(message, conn)


@router.message(F.text == "❌ Видалити адміністратора", StateFilter("*"))
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


@router.message(AdminManageState.select_admin_to_remove, F.text.startswith("❌ Видалити: "))
async def confirm_remove_admin_text(message: Message, state: FSMContext, conn):
    admins.require_owner(conn, message.from_user.id)
    match = re.search(r"\((\d+)\)$", message.text)
    if not match:
        return
    target_id = int(match.group(1))
    admins.remove_admin(conn, message.from_user.id, target_id)
    await state.clear()
    await message.answer(f"✅ Права адміністратора для ID <code>{target_id}</code> видалено.")
    await list_admins_message(message, conn)


@router.message(AdminManageState.select_admin_to_remove, F.text.in_({"↩️ Скасувати", "🔙 Скасувати", "🔙 Назад"}))
async def cancel_remove_admin(message: Message, state: FSMContext, conn=None):
    await state.clear()
    await admins_menu(message, state, conn=conn)


# ---------- Розсилка повідомлень усім користувачам ----------

@router.message(F.text == "📢 Оголошення для всіх", StateFilter("*"))
async def start_broadcast_flow(message: Message, state: FSMContext, conn):
    admins.require_owner(conn, message.from_user.id)
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
    admins.require_owner(conn, message.from_user.id)
    if message.text in {"🔙 Скасувати", "🔙 Назад", "↩️ Скасувати"}:
        await state.clear()
        await admin_category_settings(message, state, conn=conn)
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

@router.message(F.text == "🛠 Увімкнути техроботи", StateFilter("*"))
async def start_maintenance_prompt(message: Message, state: FSMContext, conn):
    admins.require_owner(conn, message.from_user.id)
    from services.maintenance import is_maintenance_active
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


@router.message(AdminManageState.entering_maintenance_time, F.text)
async def process_maintenance_time_entry(message: Message, state: FSMContext, conn):
    admins.require_owner(conn, message.from_user.id)
    if message.text in {"🔙 Скасувати", "🔙 Назад"}:
        await state.set_state(AdminPanelState.in_settings_menu)
        from services.maintenance import is_maintenance_active
        await message.answer(
            "Скасовано. Режим техробіт не увімкнено.",
            reply_markup=get_admin_settings_menu(is_maintenance=is_maintenance_active(conn)),
        )
        return

    import config
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

    from services.maintenance import broadcast_maintenance_notice, set_maintenance_mode
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


@router.message(F.text == "🟢 Вимкнути техроботи", StateFilter("*"))
async def disable_maintenance(message: Message, state: FSMContext, conn):
    admins.require_owner(conn, message.from_user.id)
    from services.maintenance import broadcast_maintenance_end, set_maintenance_mode
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

