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
    get_admin_reply_menu,
    make_keyboard,
    make_reply_keyboard,
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


router.callback_query.middleware(AdminOnly())
router.message.middleware(AdminOnly())


class AdminPanelState(StatesGroup):
    select_queue_for_view = State()
    select_queue_for_add_student = State()
    select_queue_for_order = State()
    select_queue_for_close = State()
    select_subject_for_report = State()
    add_student_names = State()


class AdminOrderState(StatesGroup):
    picking_student = State()
    managing_entry = State()
    entering_number = State()
    entering_name = State()


class AdminManageState(StatesGroup):
    add_admin_id = State()
    select_admin_to_remove = State()
    history_view = State()


def get_open_session(conn, session_id: int):
    session = queues.get_session(conn, session_id)
    if session is None or session["status"] == "archived":
        raise BotError("Ця черга вже завершена або не існує.")
    return session


def get_admin_queue_menu(is_open: bool) -> ReplyKeyboardMarkup:
    lock_text = "🔒 Закрити запис" if is_open else "🔓 Відкрити запис"
    rows = [
        ["➕ Додати студентів", "🔢 Керувати студентами"],
        [lock_text, "🔄 Оновити"],
        ["📦 Завершити чергу", "🔙 В адмін-панель"],
        ["🏠 Головне меню"],
    ]
    return make_reply_keyboard(rows)


async def open_admin_queue_manager(message: Message, conn, subjects, session_id: int, note: str = ""):
    session = get_open_session(conn, session_id)
    entries = queues.get_entries(conn, session_id)
    text = format_queue(subjects, session, entries, show_id=True)
    if note:
        text = f"{note}\n\n{text}"
    await message.answer(text, reply_markup=get_admin_queue_menu(session["status"] == "open"))


async def open_admin_student_list(message: Message, conn, subjects, session_id: int, note: str = ""):
    session = get_open_session(conn, session_id)
    entries = queues.get_entries(conn, session_id)
    if not entries:
        await open_admin_queue_manager(message, conn, subjects, session_id, note="У черзі немає студентів.")
        return
    rows = [[f"👤 {format_entry(e, show_id=True)}"] for e in entries]
    rows.append(["🔙 Назад до черги", "🔙 В адмін-панель"])
    text = format_queue(subjects, session, entries, show_id=True) + "\n\nОберіть студента для дій на кнопках внизу 👇"
    if note:
        text = f"{note}\n\n{text}"
    await message.answer(text, reply_markup=make_reply_keyboard(rows))


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
    lines.append(f"📚 {subject} — {teacher}")
    user_name = user["full_name"] if user else "— вільне місце —"
    tg_info = f" [ID: {user['telegram_id']}]" if user and user["telegram_id"] and user["telegram_id"] > 0 else " [без TG]"
    status_info = "⛔ Заблокований" if user and user.get("is_blocked") else "✅ Активний"
    lines.append(f"👤 Студент: **{user_name}**{tg_info} — {status_info}")
    lines.append(f"📍 Поточна позиція в черзі: **{entry['position']}**")
    lines.append("")
    lines.append("Оберіть дію на кнопках внизу 👇")
    text = "\n".join(lines)
    if note:
        text = f"{note}\n\n{text}"

    block_btn = "🟢 Розблокувати" if user and user.get("is_blocked") else "⛔ Заблокувати"
    rows = [
        ["⬆️ Вище", "⬇️ Нижче"],
        ["🔢 Задати номер", "✏️ Змінити ім'я"],
        [block_btn, "✅ Позначити «Здав»"],
        ["❌ Видалити з черги"],
        ["🔙 До списку студентів", "🔙 Назад до черги"],
    ]
    await message.answer(text, reply_markup=make_reply_keyboard(rows))


# ---------- Головна адмін-панель ----------

@router.message(F.text.in_({"⚡ Адмін-панель", "🔙 В адмін-панель"}), StateFilter("*"))
async def admin_panel_open(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "⚡ **Адмін-панель**\nУсі дії та черги доступні на кнопках внизу під полем вводу 👇",
        reply_markup=get_admin_reply_menu(),
    )


@router.message(F.text == "⚡ Керувати цією чергою", StateFilter("*"))
async def admin_manage_from_view(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        sessions = queues.list_sessions(conn, ("open", "closed"))
        if not sessions:
            raise BotError("Активних черг немає.")
        session_id = sessions[0]["id"]
    await state.clear()
    await state.update_data(session_id=session_id)
    await open_admin_queue_manager(message, conn, subjects, session_id)


# ---------- Вибір черги для адмін-дій ----------

@router.message(F.text == "👀 Активні черги", StateFilter("*"))
async def admin_pick_active_queues(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    sessions = queues.list_sessions(conn, ("open", "closed"))
    if not sessions:
        await message.answer("Активних черг немає.", reply_markup=get_admin_reply_menu())
        return
    rows = [[f"📋 {queue_label(subjects, s)}"] for s in sessions]
    rows.append(["🔙 В адмін-панель", "🏠 Головне меню"])
    await state.set_state(AdminPanelState.select_queue_for_view)
    await message.answer("👀 Оберіть чергу для перегляду та керування на кнопках внизу 👇", reply_markup=make_reply_keyboard(rows))


@router.message(F.text == "📝 Додати студента", StateFilter("*"))
async def admin_pick_add_student(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    sessions = queues.list_sessions(conn, ("open", "closed"))
    if not sessions:
        await message.answer("Активних черг немає.", reply_markup=get_admin_reply_menu())
        return
    rows = [[f"📋 {queue_label(subjects, s)}"] for s in sessions]
    rows.append(["🔙 В адмін-панель", "🏠 Головне меню"])
    await state.set_state(AdminPanelState.select_queue_for_add_student)
    await message.answer("📝 Оберіть чергу, в яку додати студентів, на кнопках внизу 👇", reply_markup=make_reply_keyboard(rows))


@router.message(F.text.in_({"🔢 Змінити порядок", "✅ Позначити здачу"}), StateFilter("*"))
async def admin_pick_order_queue(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    sessions = queues.list_sessions(conn, ("open", "closed"))
    if not sessions:
        await message.answer("Активних черг немає.", reply_markup=get_admin_reply_menu())
        return
    rows = [[f"📋 {queue_label(subjects, s)}"] for s in sessions]
    rows.append(["🔙 В адмін-панель", "🏠 Головне меню"])
    await state.set_state(AdminPanelState.select_queue_for_order)
    await message.answer("🔢 Оберіть чергу для зміни порядку студентів 👇", reply_markup=make_reply_keyboard(rows))


@router.message(F.text == "🔒 Відкрити/закрити запис", StateFilter("*"))
async def admin_pick_close_queue(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    sessions = queues.list_sessions(conn, ("open", "closed"))
    if not sessions:
        await message.answer("Активних черг немає.", reply_markup=get_admin_reply_menu())
        return
    rows = [[f"📋 {queue_label(subjects, s)}"] for s in sessions]
    rows.append(["🔙 В адмін-панель", "🏠 Головне меню"])
    await state.set_state(AdminPanelState.select_queue_for_close)
    await message.answer("🔒 Оберіть чергу для відкриття/закриття запису 👇", reply_markup=make_reply_keyboard(rows))


@router.message(F.text.in_({"📊 Звіти за предметами", "📊 Інший предмет"}), StateFilter("*"))
async def admin_pick_report_subject(message: Message, state: FSMContext, subjects):
    await state.clear()
    await state.set_state(AdminPanelState.select_subject_for_report)
    rows = [[f"📊 {s['name']}"] for s in subjects]
    rows.append(["🔙 В адмін-панель", "🏠 Головне меню"])
    await message.answer(
        "📊 **Формування звіту по предмету**\n"
        "Оберіть предмет зі списку нижче. Бот сформує детальну аналітику та файл таблиці (Excel/CSV) 👇",
        reply_markup=make_reply_keyboard(rows),
    )


@router.message(AdminPanelState.select_subject_for_report, F.text.startswith("📊 "))
async def admin_generate_subject_report(message: Message, state: FSMContext, conn, subjects):
    raw_name = message.text[2:].strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == raw_name.lower()), None)
    if not matched_sub:
        await message.answer("⚠️ Предмет не знайдено. Оберіть зі списку на кнопках внизу 👇")
        return

    report = queues.get_subject_report(conn, matched_sub["id"])
    teachers = ", ".join(t["name"] for t in matched_sub.get("teachers", [])) or "—"

    # Формування текстового повідомлення
    lines = [
        f"📊 **Звіт по предмету: {matched_sub['name']}**",
        f"👨‍🏫 Викладачі: {teachers}",
        f"📁 Проведено занять/черг: **{report['sessions_count']}**",
        f"✅ Всього зараховано робіт: **{report['total_passed']}**",
    ]

    if report["active_session"]:
        lines.append("⚡ Зараз триває активна черга по цьому предмету!")

    lines.append("")
    lines.append("🏆 **Рейтинг здач студентів:**")
    if not report["students"]:
        lines.append("Ще немає жодного запису чи здачі по цьому предмету.")
    else:
        for idx, s in enumerate(report["students"][:20], 1):
            pos_info = f" (👉 в черзі на місці {s['current_position']})" if s["current_position"] else ""
            lines.append(f"{idx}. {s['name']} — **{s['passed_count']}** ✅{pos_info}")
        if len(report["students"]) > 20:
            lines.append(f"... та ще {len(report['students']) - 20} студентів (повний список у файлі нижче)")

    text_msg = "\n".join(lines)

    # Формування CSV таблиці для Excel (utf-8-sig гарантує коректне відображення кирилиці)
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
        ["🔙 В адмін-панель", "🏠 Головне меню"],
    ]

    await message.answer(text_msg)
    await message.answer_document(
        file,
        caption=f"📄 Таблиця Excel/CSV: {matched_sub['name']}",
        reply_markup=make_reply_keyboard(menu_rows),
    )


# Обробка вибору черги за назвою для відповідного адмін-режиму
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
        f"📝 **Додавання студентів у чергу:**\n"
        f"{queue_label(subjects, session)}\n\n"
        "Надішліть одне прізвище та ім'я або **список студентів** (кожне з нового рядка).\n"
        "Можна вказувати номер (наприклад `3. Диновський Денис` — поставить саме на 3 місце).\n\n"
        "Якщо вказати номер — студент займе саме його, а перед ним утворяться вільні місця.",
        reply_markup=make_reply_keyboard([["🔙 Назад до черги"], ["🔙 В адмін-панель"]]),
    )


@router.message(AdminPanelState.select_queue_for_order, F.text.startswith("📋 "))
async def admin_selected_queue_order(message: Message, state: FSMContext, conn, subjects):
    session = find_session_by_label(conn, subjects, message.text)
    if not session:
        await message.answer("⚠️ Чергу не знайдено. Оберіть зі списку на кнопках внизу 👇")
        return
    await state.set_state(AdminOrderState.picking_student)
    await state.update_data(session_id=session["id"])
    await open_admin_student_list(message, conn, subjects, session["id"])


@router.message(AdminPanelState.select_queue_for_close, F.text.startswith("📋 "))
async def admin_selected_queue_close(message: Message, state: FSMContext, conn, subjects):
    session = find_session_by_label(conn, subjects, message.text)
    if not session:
        await message.answer("⚠️ Чергу не знайдено. Оберіть зі списку на кнопках внизу 👇")
        return
    make_open = session["status"] == "closed"
    queues.set_signup_open(conn, message.from_user.id, session["id"], make_open)
    note = "🔓 Запис відкрито!" if make_open else "🔒 Запис закрито!"
    await state.clear()
    await state.update_data(session_id=session["id"])
    await open_admin_queue_manager(message, conn, subjects, session["id"], note=note)


# ---------- Керування обраною чергою (Кнопки менеджера черги) ----------

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
        f"📝 **Додавання студентів у чергу:**\n"
        f"{queue_label(subjects, session)}\n\n"
        "Надішліть одне прізвище та ім'я або **список студентів** (кожне з нового рядка).\n"
        "Можна вказувати номер (наприклад `3. Диновський Денис` — поставить саме на 3 місце).\n\n"
        "Якщо вказати номер — студент займе саме його, а перед ним утворяться вільні місця.",
        reply_markup=make_reply_keyboard([["🔙 Назад до черги"], ["🔙 В адмін-панель"]]),
    )


@router.message(AdminPanelState.add_student_names, F.text)
async def receive_student_names(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if message.text in {"🔙 Назад до черги", "🔙 В адмін-панель"}:
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
    result_text = f"✅ Додано студентів: {len(added)}\n"
    if added:
        result_text += "\n".join(added[:15]) + ("\n..." if len(added) > 15 else "")
    if skipped:
        result_text += "\n\n⚠️ Пропущено (помилка або вже в черзі):\n" + "\n".join(skipped[:10])

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
    note = "🔓 Запис відкрито!" if make_open else "🔒 Запис закрито!"
    await open_admin_queue_manager(message, conn, subjects, session_id, note=note)


@router.message(F.text == "🔢 Керувати студентами", StateFilter("*"))
async def start_managing_students(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        raise BotError("Чергу не обрано.")
    await state.set_state(AdminOrderState.picking_student)
    await state.update_data(session_id=session_id)
    await open_admin_student_list(message, conn, subjects, session_id)


@router.message(AdminOrderState.picking_student, F.text.startswith("👤 "))
async def select_student_from_list(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    entries = queues.get_entries(conn, session_id)
    raw = message.text[2:].strip()
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
    await state.set_state(AdminOrderState.picking_student)
    await state.update_data(session_id=session_id)
    await open_admin_student_list(message, conn, subjects, session_id)


# ---------- Дії над обраним студентом (Нижні кнопки) ----------

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
        reply_markup=make_reply_keyboard([["🔙 Скасувати"]]),
    )


@router.message(AdminOrderState.entering_number, F.text)
async def student_receive_number(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    entry_id = data.get("entry_id")
    if message.text == "🔙 Скасувати":
        await state.set_state(AdminOrderState.managing_entry)
        await open_admin_student_manager(message, conn, subjects, entry_id)
        return
    if not message.text.strip().isdigit():
        await message.answer("Введіть число або натисніть «🔙 Скасувати»:")
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
        f"Введіть нове ПІБ для студента «{user['full_name']}» (наприклад: Шевченко Тарас):",
        reply_markup=make_reply_keyboard([["🔙 Скасувати"]]),
    )


@router.message(AdminOrderState.entering_name, F.text)
async def student_receive_rename(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    entry_id = data.get("entry_id")
    user_id = data.get("user_id")
    if message.text == "🔙 Скасувати":
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
    await open_admin_student_manager(message, conn, subjects, entry_id, note=f"✅ Ім'я змінено на «{updated_user['full_name']}».")


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
            note=f"⛔ Студента «{user['full_name']}» заблоковано та знято з усіх черг.",
        )
    else:
        await open_admin_student_manager(
            message, conn, subjects, entry_id,
            note=f"🟢 Студента «{user['full_name']}» розблоковано.",
        )


@router.message(F.text == "✅ Позначити «Здав»", StateFilter("*"))
async def student_mark_passed_by_admin(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    entry_id = data.get("entry_id")
    session_id = data.get("session_id")
    if not entry_id:
        raise BotError("Студента не обрано.")
    queues.mark_done(conn, message.from_user.id, entry_id)
    await state.set_state(AdminOrderState.picking_student)
    await open_admin_student_list(message, conn, subjects, session_id, note="✅ Позначено: здав! Запис видалено з черги.")


@router.message(F.text == "❌ Видалити з черги", StateFilter("*"))
async def student_remove_by_admin(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    entry_id = data.get("entry_id")
    session_id = data.get("session_id")
    if not entry_id:
        raise BotError("Студента не обрано.")
    queues.remove_entry(conn, message.from_user.id, entry_id)
    await state.set_state(AdminOrderState.picking_student)
    await open_admin_student_list(message, conn, subjects, session_id, note="❌ Запис видалено з черги.")


# ---------- Завершення та архівування черги ----------

@router.message(F.text == "📦 Завершити чергу", StateFilter("*"))
async def ask_finish_queue(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        raise BotError("Чергу не обрано.")
    session = get_open_session(conn, session_id)
    rows = [
        ["📦 Завершити й архівувати"],
        ["📦 Нова черга (порожня)"],
        ["🔙 Назад до черги"],
    ]
    await message.answer(
        f"Завершити заняття?\n{queue_label(subjects, session)}\n\nОберіть дію на кнопках внизу 👇",
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

@router.message(F.text.in_({"📦 Історія черг", "🔙 До списку історії"}), StateFilter("*"))
async def history_list(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    sessions = queues.list_sessions(conn, ("archived",))[:15]
    if not sessions:
        await message.answer("Історія порожня.", reply_markup=get_admin_reply_menu())
        return
    rows = [[f"📦 №{s['id']} {queue_label(subjects, s)}"] for s in sessions]
    rows.append(["🔙 В адмін-панель", "🏠 Головне меню"])
    await state.set_state(AdminManageState.history_view)
    await message.answer("Історія черг. Оберіть чергу для перегляду деталей 👇", reply_markup=make_reply_keyboard(rows))


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
    lines = [f"{r['full_name']} — {HISTORY_MARK.get(r['status'], r['status'])}" for r in queues.get_history(conn, session_id)]
    header = format_queue(subjects, session, [])
    body = "\n".join(lines) or "Записів немає."
    text = header.replace("Черга порожня.", "") + body
    rows = [["🔙 До списку історії", "🔙 В адмін-панель"]]
    await message.answer(text, reply_markup=make_reply_keyboard(rows))


# ---------- Заблоковані студенти ----------

@router.message(F.text == "⛔ Заблоковані", StateFilter("*"))
async def show_blocked_list(message: Message, state: FSMContext, conn):
    await state.clear()
    blocked = users.list_blocked_users(conn)
    if not blocked:
        await message.answer("Заблокованих користувачів немає.", reply_markup=get_admin_reply_menu())
        return
    rows = []
    for u in blocked:
        tg_id_str = f" (ID: {u['telegram_id']})" if u["telegram_id"] and u["telegram_id"] > 0 else ""
        rows.append([f"🟢 Розблокувати: {u['full_name']}{tg_id_str}"])
    rows.append(["🔙 В адмін-панель", "🏠 Головне меню"])
    await message.answer(
        "⛔ **Список заблокованих користувачів:**\nНатисніть на кнопку студента внизу, щоб розблокувати його 👇",
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
    await message.answer(f"🟢 Студента «{user['full_name']}» успішно розблоковано!")
    await show_blocked_list(message, state, conn)


# ---------- Керування адміністраторами ----------

@router.message(F.text == "👥 Адміністратори", StateFilter("*"))
async def admins_menu(message: Message, state: FSMContext):
    await state.clear()
    rows = [
        ["📋 Список адміністраторів", "➕ Додати адміністратора"],
        ["❌ Видалити адміністратора", "🔙 В адмін-панель"],
    ]
    await message.answer("Керування адміністраторами. Оберіть дію на кнопках внизу 👇", reply_markup=make_reply_keyboard(rows))


@router.message(F.text == "📋 Список адміністраторів", StateFilter("*"))
async def list_admins_message(message: Message, conn):
    adm_list = admins.list_admins(conn)
    lines = ["👥 **Адміністратори:**"]
    for r in adm_list:
        name = r["full_name"] or "без реєстрації"
        lines.append(f"• {name} (`{r['telegram_id']}`)")
    rows = [
        ["➕ Додати адміністратора", "❌ Видалити адміністратора"],
        ["🔙 В адмін-панель", "🏠 Головне меню"],
    ]
    await message.answer("\n".join(lines), reply_markup=make_reply_keyboard(rows))


@router.message(F.text == "➕ Додати адміністратора", StateFilter("*"))
async def ask_admin_id(message: Message, state: FSMContext):
    await state.set_state(AdminManageState.add_admin_id)
    rows = [["🔙 Скасувати", "🔙 В адмін-панель"]]
    await message.answer("Введіть числовий Telegram ID нового адміністратора:", reply_markup=make_reply_keyboard(rows))


@router.message(AdminManageState.add_admin_id, F.text)
async def receive_admin_id(message: Message, state: FSMContext, conn):
    if message.text in {"🔙 Скасувати", "🔙 В адмін-панель"}:
        await admins_menu(message, state)
        return
    text = message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Telegram ID — це число. Спробуйте ще раз або натисніть «🔙 Скасувати»:")
        return
    new_admin_id = int(text)
    if admins.is_admin(conn, new_admin_id):
        await message.answer("Цей користувач уже є адміністратором. Введіть інший ID:")
        return
    admins.add_admin(conn, message.from_user.id, new_admin_id)
    await state.clear()
    await message.answer(f"✅ Адміністратора з ID `{new_admin_id}` успішно додано!")
    await list_admins_message(message, conn)


@router.message(F.text == "❌ Видалити адміністратора", StateFilter("*"))
async def choose_admin_to_remove(message: Message, state: FSMContext, conn):
    adm_list = admins.list_admins(conn)
    rows = []
    for r in adm_list:
        if r["telegram_id"] == message.from_user.id:
            continue  # Не можна видалити самого себе
        name = r["full_name"] or "без реєстрації"
        rows.append([f"❌ Видалити: {name} ({r['telegram_id']})"])
    rows.append(["🔙 Скасувати", "🔙 В адмін-панель"])
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
    await message.answer(f"✅ Права адміністратора для ID `{target_id}` видалено.")
    await list_admins_message(message, conn)


@router.message(AdminManageState.select_admin_to_remove, F.text.in_({"🔙 Скасувати", "🔙 В адмін-панель"}))
async def cancel_remove_admin(message: Message, state: FSMContext):
    await state.clear()
    await admins_menu(message, state)
