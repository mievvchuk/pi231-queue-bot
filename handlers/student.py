import html
import logging
import re
from contextlib import suppress

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from handlers.common import (
    find_session_by_label,
    format_queue,
    get_coursework_queues_menu,
    get_lost_context_menu,
    get_paginated_reply_keyboard,
    get_queue_actions_reply_menu,
    get_queue_view_reply_menu,
    get_queues_reply_menu,
    get_reply_menu,
    make_keyboard,
    make_reply_keyboard,
    notify_admins_queue_opened,
    notify_next_students,
    paginate,
    queue_label,
    session_names,
    show,
)
from services import admins, queues, users
from services.errors import BotError

log = logging.getLogger(__name__)
router = Router()


class StudentFlow(StatesGroup):
    browsing_queues = State()
    browsing_coursework_queues = State()
    browsing_my_queues = State()
    pick_position = State()
    choosing_swap_target = State()
    confirm_swap = State()
    creating_queue_type = State()
    creating_queue_subject = State()
    creating_queue_teacher = State()


def get_session_or_fail(conn, session_id: int):
    session = queues.get_session(conn, session_id)
    if session is None:
        raise BotError("Чергу не знайдено.")
    return session


async def ensure_session_id(message: Message, state: FSMContext) -> int | None:
    """Перевіряє наявність вибраної черги. Якщо контекст втрачено — просить обрати знову."""
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        await message.answer(
            "⚠️ <b>Потрібно знову обрати чергу.</b>",
            reply_markup=get_lost_context_menu(),
        )
        return None
    return session_id


async def open_queue_screen(message: Message, conn, subjects, session_id: int, note: str = ""):
    session = get_session_or_fail(conn, session_id)
    entries = queues.get_entries(conn, session_id)
    is_adm = admins.is_admin(conn, message.from_user.id)
    text = format_queue(subjects, session, entries, message.from_user.id, show_id=is_adm)
    if note:
        text = f"{note}\n\n{text}"
    markup = get_queue_view_reply_menu()
    await message.answer(text, reply_markup=markup)


async def not_admin_state_filter(message: Message, state: FSMContext) -> bool:
    current_state = await state.get_state()
    if current_state and (
        current_state.startswith("AdminPanelState")
        or current_state.startswith("AdminOrderState")
        or current_state.startswith("AdminManageState")
        or current_state.startswith("Admin")
    ):
        return False
    return True


async def student_queue_button_filter(message: Message, state: FSMContext) -> bool:
    if not message.text or not message.text.startswith("📋 "):
        return False
    if message.text in {
        "📋 Усі черги",
        "📋 Мої черги",
        "📋 Стати в чергу",
        "📋 Список адміністраторів",
        "📋 Черги",
    }:
        return False
    return await not_admin_state_filter(message, state)


# ---------- Список черг та навігація ----------

@router.message(F.text.in_({"📋 Усі черги", "🔍 Переглянути черги", "📋 Стати в чергу"}), StateFilter("*"))
async def view_all_queues(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    sessions = queues.list_sessions(conn, ("open", "closed"))
    if not sessions:
        await message.answer(
            "Активних черг зараз немає.",
            reply_markup=get_reply_menu(conn, message.from_user.id),
        )
        return
    await state.set_state(StudentFlow.browsing_queues)
    await state.update_data(page=1)
    await message.answer(
        "Оберіть чергу на кнопках внизу 👇",
        reply_markup=get_queues_reply_menu(sessions, subjects, page=1),
    )


@router.message(F.text == "👥 Мої черги", StateFilter("*"))
async def my_queues(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    sessions = queues.list_user_sessions(conn, message.from_user.id)
    if not sessions:
        await message.answer(
            "Ви ще не записані в жодну чергу.",
            reply_markup=get_reply_menu(conn, message.from_user.id),
        )
        return
    await state.set_state(StudentFlow.browsing_my_queues)
    await state.update_data(page=1)
    await message.answer(
        "Ваші черги. Оберіть чергу на кнопках внизу 👇",
        reply_markup=get_queues_reply_menu(sessions, subjects, page=1, group_coursework=False),
    )


@router.message(StudentFlow.browsing_queues, F.text.in_({"⬅️", "➡️"}))
async def paginate_all_queues(message: Message, state: FSMContext, conn, subjects):
    sessions = queues.list_sessions(conn, ("open", "closed"))
    data = await state.get_data()
    page = data.get("page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await state.update_data(page=page)
    await message.answer(
        "Оберіть чергу на кнопках внизу 👇",
        reply_markup=get_queues_reply_menu(sessions, subjects, page=page, group_coursework=True),
    )


@router.message(StudentFlow.browsing_my_queues, F.text.in_({"⬅️", "➡️"}))
async def paginate_my_queues(message: Message, state: FSMContext, conn, subjects):
    sessions = queues.list_user_sessions(conn, message.from_user.id)
    data = await state.get_data()
    page = data.get("page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await state.update_data(page=page)
    await message.answer(
        "Ваші черги. Оберіть чергу на кнопках внизу 👇",
        reply_markup=get_queues_reply_menu(sessions, subjects, page=page, group_coursework=False),
    )


@router.message(F.text.in_({"🔄 Оновити список", "🔄 Оновити"}), StudentFlow.browsing_queues)
async def refresh_all_queues_list(message: Message, state: FSMContext, conn, subjects):
    sessions = queues.list_sessions(conn, ("open", "closed"))
    data = await state.get_data()
    page = data.get("page", 1)
    if not sessions:
        await message.answer("Активних черг немає.", reply_markup=get_reply_menu(conn, message.from_user.id))
        return
    await message.answer(
        "Список черг оновлено 👇",
        reply_markup=get_queues_reply_menu(sessions, subjects, page=page, group_coursework=True),
    )


@router.message(F.text.in_({"🔄 Оновити список", "🔄 Оновити"}), StudentFlow.browsing_my_queues)
async def refresh_my_queues_list(message: Message, state: FSMContext, conn, subjects):
    sessions = queues.list_user_sessions(conn, message.from_user.id)
    data = await state.get_data()
    page = data.get("page", 1)
    if not sessions:
        await message.answer("Ви не записані в жодну чергу.", reply_markup=get_reply_menu(conn, message.from_user.id))
        return
    await message.answer(
        "Ваші черги оновлено 👇",
        reply_markup=get_queues_reply_menu(sessions, subjects, page=page, group_coursework=False),
    )


def is_coursework_group_button(message: Message) -> bool:
    if not message.text:
        return False
    t = message.text.strip()
    if t.startswith("📋 ") or t.startswith("➕ ") or t.startswith("👨‍🏫 "):
        return False
    if t.startswith("📑") or t.startswith("🎓") or t.startswith("📚"):
        return True
    return "курсов" in t.lower()


@router.message(is_coursework_group_button, not_admin_state_filter)
async def open_coursework_group(message: Message, state: FSMContext, conn, subjects):
    raw = message.text
    for prefix in ("📑", "🎓", "📚"):
        raw = raw.removeprefix(prefix)
    cw_name = raw.strip()

    sessions = queues.list_sessions(conn, ("open", "closed"))
    cw_sessions = [s for s in sessions if session_names(subjects, s)[0].lower() == cw_name.lower()]
    if not cw_sessions:
        cw_sessions = [s for s in sessions if "курсов" in session_names(subjects, s)[0].lower()]
        if cw_sessions:
            cw_name = session_names(subjects, cw_sessions[0])[0]

    if not cw_sessions:
        await message.answer(
            f"Зараз немає активних черг для курсових робіт.",
            reply_markup=get_queues_reply_menu(sessions, subjects, group_coursework=True),
        )
        return

    await state.set_state(StudentFlow.browsing_coursework_queues)
    await state.update_data(coursework_name=cw_name, page=1)
    await message.answer(
        f"📚 <b>{html.escape(cw_name)}</b>\n\n"
        "Оберіть викладача для перегляду черги 👇",
        reply_markup=get_coursework_queues_menu(sessions, subjects, cw_name, page=1),
    )


@router.message(StudentFlow.browsing_coursework_queues, F.text.in_({"⬅️", "➡️"}))
async def paginate_coursework_queues(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    cw_name = data.get("coursework_name", "Курсова робота з КПЗ")
    sessions = queues.list_sessions(conn, ("open", "closed"))
    page = data.get("page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await state.update_data(page=page)
    await message.answer(
        f"Черги для «{html.escape(cw_name)}». Оберіть викладача 👇",
        reply_markup=get_coursework_queues_menu(sessions, subjects, cw_name, page=page),
    )


@router.message(F.text.in_({"🔄 Оновити список", "🔄 Оновити"}), StudentFlow.browsing_coursework_queues)
async def refresh_coursework_queues_list(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    cw_name = data.get("coursework_name", "Курсова робота з КПЗ")
    sessions = queues.list_sessions(conn, ("open", "closed"))
    page = data.get("page", 1)
    await message.answer(
        f"Список черг для «{html.escape(cw_name)}» оновлено 👇",
        reply_markup=get_coursework_queues_menu(sessions, subjects, cw_name, page=page),
    )


@router.message(F.text.in_({"🔙 До черг", "🔙 До списку черг"}), not_admin_state_filter)
async def back_to_queues_list(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    await view_all_queues(message, state, conn, subjects)


@router.message(student_queue_button_filter)
async def select_queue_by_button(message: Message, state: FSMContext, conn, subjects):
    session = find_session_by_label(conn, subjects, message.text)
    if not session:
        sessions = queues.list_sessions(conn, ("open", "closed"))
        await message.answer(
            "⚠️ Чергу не знайдено або її закрито. Оберіть зі списку активних черг 👇",
            reply_markup=get_queues_reply_menu(sessions, subjects),
        )
        return
    await state.clear()
    await state.update_data(session_id=session["id"])
    await open_queue_screen(message, conn, subjects, session["id"])


# ---------- Дії всередині черги ----------

@router.message(F.text == "🔄 Оновити", not_admin_state_filter)
async def refresh_current_queue(message: Message, state: FSMContext, conn, subjects):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    await open_queue_screen(message, conn, subjects, session_id)


@router.message(F.text == "📍 Моє місце", not_admin_state_filter)
async def show_my_place(message: Message, state: FSMContext, conn, subjects):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    place = queues.get_user_position(conn, message.from_user.id, session_id)
    total = len(queues.get_entries(conn, session_id))
    note = f"📍 Ваше місце: <b>{place}</b> із {total}." if place else "Ви не записані в цю чергу."
    await open_queue_screen(message, conn, subjects, session_id, note=note)


@router.message(F.text == "⚙️ Дії", not_admin_state_filter)
async def show_queue_actions(message: Message, state: FSMContext, conn):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    session = get_session_or_fail(conn, session_id)
    is_in_queue = queues.get_user_position(conn, message.from_user.id, session_id) is not None
    is_open = session["status"] == "open"
    can_pick = not queues.are_first_10_occupied(conn, session_id)
    markup = get_queue_actions_reply_menu(is_in_queue, is_open=is_open, can_pick_number=can_pick)
    await message.answer("⚙️ <b>Оберіть дію:</b>", reply_markup=markup)


@router.message(F.text.in_({"🔙 Назад до черги", "🔙 Назад"}), not_admin_state_filter)
async def back_to_current_queue(message: Message, state: FSMContext, conn, subjects):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    await state.clear()
    await state.update_data(session_id=session_id)
    await open_queue_screen(message, conn, subjects, session_id)


@router.message(F.text.in_({"✅ Здав", "здав", "здала"}), not_admin_state_filter)
async def mark_passed(message: Message, state: FSMContext, conn, subjects):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    my_pos = queues.get_user_position(conn, message.from_user.id, session_id)
    queues.finish_own_entry(conn, message.from_user.id, session_id, passed=True)
    await notify_next_students(message.bot, conn, subjects, session_id, removed_position=my_pos)
    await open_queue_screen(
        message, conn, subjects, session_id,
        note="✅ <b>Здачу зараховано</b>\n\nВас знято з черги.",
    )


@router.message(F.text.in_({"❌ Не здав", "не здав", "не здала"}), not_admin_state_filter)
async def mark_not_passed(message: Message, state: FSMContext, conn, subjects):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    my_pos = queues.get_user_position(conn, message.from_user.id, session_id)
    new_place = queues.finish_own_entry(conn, message.from_user.id, session_id, passed=False)
    await notify_next_students(message.bot, conn, subjects, session_id, removed_position=my_pos)
    await open_queue_screen(
        message, conn, subjects, session_id,
        note=f"❌ <b>Позначено: не здав</b>\n\nВас переміщено в кінець черги на місце <b>{new_place}</b>.",
    )


@router.message(F.text == "🚪 Покинути чергу", not_admin_state_filter)
async def ask_leave_queue(message: Message, state: FSMContext, conn):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    if queues.get_user_position(conn, message.from_user.id, session_id) is None:
        raise BotError("Ви не записані в цю чергу.")
    markup = make_reply_keyboard([["⚠️ Підтвердити вихід"], ["🔙 Назад до черги"]])
    await message.answer("🚪 <b>Покинути чергу?</b>\n\nВаше місце буде втрачено.", reply_markup=markup)


@router.message(F.text == "⚠️ Підтвердити вихід", not_admin_state_filter)
async def confirm_leave_queue(message: Message, state: FSMContext, conn, subjects):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    my_pos = queues.get_user_position(conn, message.from_user.id, session_id)
    queues.leave_queue(conn, message.from_user.id, session_id)
    await notify_next_students(message.bot, conn, subjects, session_id, removed_position=my_pos)
    await open_queue_screen(message, conn, subjects, session_id, note="🚪 <b>Ви вийшли з черги.</b>")


# ---------- Запис у чергу ----------

@router.message(F.text.in_({"📝 Стати в кінець", "➡️ В кінець черги"}), not_admin_state_filter)
async def join_queue_end(message: Message, state: FSMContext, conn, subjects):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    session = get_session_or_fail(conn, session_id)
    if session["status"] != "open":
        raise BotError("Запис у цю чергу закрито.")
    position, total = queues.join_queue(conn, message.from_user.id, session_id)
    await open_queue_screen(
        message, conn, subjects, session_id,
        note=f"✅ <b>Ви записалися</b>\n\nВаше місце: <b>{position}</b> із {total}.",
    )


@router.message(F.text.in_({"🔢 Обрати місце", "🔢 Обрати номер"}), not_admin_state_filter)
async def ask_specific_number(message: Message, state: FSMContext, conn, subjects):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    session = get_session_or_fail(conn, session_id)
    if session["status"] != "open":
        raise BotError("Запис у цю чергу закрито.")
    if queues.are_first_10_occupied(conn, session_id):
        position, total = queues.join_queue(conn, message.from_user.id, session_id)
        await open_queue_screen(
            message, conn, subjects, session_id,
            note=f"ℹ️ <b>Перші 10 місць уже зайняті.</b>\nВас автоматично записано в кінець черги на місце <b>{position}</b> із {total}.",
        )
        return
    await state.set_state(StudentFlow.pick_position)
    await state.update_data(session_id=session_id)
    await message.answer(
        "🔢 <b>Вибір місця</b>\n\nВведіть бажаний вільний номер від 1 до 10 (наприклад 3 чи 7):",
        reply_markup=make_reply_keyboard([["🔙 Назад до черги"]]),
    )


@router.message(StudentFlow.pick_position, F.text)
async def handle_join_number(message: Message, state: FSMContext, conn, subjects):
    if message.text in {"🔙 Назад до черги", "🔙 Назад"}:
        data = await state.get_data()
        session_id = data.get("session_id")
        await state.clear()
        if session_id:
            await state.update_data(session_id=session_id)
            await open_queue_screen(message, conn, subjects, session_id)
        return
    if not message.text.strip().isdigit():
        await message.answer("Введіть число (наприклад 3) або натисніть «🔙 Назад до черги»:")
        return
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        await message.answer("⚠️ <b>Потрібно знову обрати чергу.</b>", reply_markup=get_lost_context_menu())
        return
    pos = int(message.text.strip())
    try:
        position, total = queues.join_queue(conn, message.from_user.id, session_id, pos)
    except BotError as err:
        await message.answer(f"{err}\nСпробуйте інший номер або натисніть «🔙 Назад до черги»:")
        return
    await state.clear()
    await state.update_data(session_id=session_id)
    await open_queue_screen(
        message, conn, subjects, session_id,
        note=f"✅ <b>Ви записалися</b>\n\nВаше місце: <b>{position}</b> із {total}.",
    )


# ---------- Обмін місцями між студентами ----------

@router.message(F.text.in_({"🔁 Обмінятися місцями", "🔄 Помінятися місцем"}), not_admin_state_filter)
async def start_swap_flow(message: Message, state: FSMContext, conn, subjects):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    my_pos = queues.get_user_position(conn, message.from_user.id, session_id)
    if my_pos is None:
        raise BotError("Ви не записані в цю чергу.")
    entries = queues.get_entries(conn, session_id)
    candidates = [
        e for e in entries
        if e["user_id"] and e["telegram_id"] and e["telegram_id"] > 0 and e["telegram_id"] != message.from_user.id
    ]
    if not candidates:
        await open_queue_screen(
            message, conn, subjects, session_id,
            note="У цій черзі немає інших студентів, з якими можна помінятися.",
        )
        return

    await state.set_state(StudentFlow.choosing_swap_target)
    await state.update_data(session_id=session_id, swap_page=1)
    page_candidates, cur_page, total_pages = paginate(candidates, 1, per_page=8)
    rows = [[f"🔄 {e['position']}. {e['full_name']}"] for e in page_candidates]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад до черги")
    await message.answer("🔁 <b>Обмін місцями</b>\n\nОберіть студента на кнопках внизу 👇", reply_markup=markup)


@router.message(StudentFlow.choosing_swap_target, F.text.in_({"⬅️", "➡️"}))
async def paginate_swap_targets(message: Message, state: FSMContext, conn):
    data = await state.get_data()
    session_id = data.get("session_id")
    page = data.get("swap_page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await state.update_data(swap_page=page)

    entries = queues.get_entries(conn, session_id)
    candidates = [
        e for e in entries
        if e["user_id"] and e["telegram_id"] and e["telegram_id"] > 0 and e["telegram_id"] != message.from_user.id
    ]
    page_candidates, cur_page, total_pages = paginate(candidates, page, per_page=8)
    rows = [[f"🔄 {e['position']}. {e['full_name']}"] for e in page_candidates]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад до черги")
    await message.answer("Оберіть студента для обміну місцями 👇", reply_markup=markup)


@router.message(StudentFlow.choosing_swap_target, F.text)
async def swap_target_chosen(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if message.text in {"🔙 Назад до черги", "🔙 Назад"}:
        await state.clear()
        if session_id:
            await state.update_data(session_id=session_id)
            await open_queue_screen(message, conn, subjects, session_id)
        return
    entries = queues.get_entries(conn, session_id)
    cleaned = message.text.replace("🔄", "").strip()
    match = re.match(r"^(\d+)[\.\)\-\s]+(.+)$", cleaned)
    target = None
    if match:
        pos = int(match.group(1))
        for e in entries:
            if e["position"] == pos:
                target = e
                break
    if not target:
        for e in entries:
            if e["full_name"] and e["full_name"] in cleaned:
                target = e
                break
    if not target or not target["telegram_id"] or target["telegram_id"] <= 0:
        await message.answer("Не вдалося знайти цього студента. Оберіть зі списку на кнопках:")
        return

    my_pos = queues.get_user_position(conn, message.from_user.id, session_id)
    target_user = conn.execute("SELECT full_name FROM users WHERE id = ?", (target["user_id"],)).fetchone()
    target_name = target_user["full_name"] if target_user else "Студент"

    await state.set_state(StudentFlow.confirm_swap)
    await state.update_data(session_id=session_id, target_entry_id=target["id"])
    text = (
        f"🔁 <b>Запропонувати обмін місцями?</b>\n\n"
        f"👤 Студент: <b>{html.escape(target_name)}</b> (місце {target['position']})\n"
        f"Ваше поточне місце: <b>{my_pos}</b>\n\n"
        f"Студенту надійде сповіщення. Якщо він погодиться — місця оновляться автоматично."
    )
    markup = make_reply_keyboard([["✅ Підтвердити обмін"], ["🔙 Назад до черги"]])
    await message.answer(text, reply_markup=markup)


@router.message(StudentFlow.confirm_swap, F.text)
async def swap_confirm_action(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    target_entry_id = data.get("target_entry_id")
    if message.text in {"🔙 Назад до черги", "🔙 Назад"}:
        await state.clear()
        if session_id:
            await state.update_data(session_id=session_id)
            await open_queue_screen(message, conn, subjects, session_id)
        return
    if message.text != "✅ Підтвердити обмін":
        return

    session = get_session_or_fail(conn, session_id)
    my_entry = queues._own_active_entry(conn, message.from_user.id, session_id)
    target = queues.get_entry(conn, target_entry_id)
    if not target or target["status"] != "active":
        raise BotError("Цього студента вже немає в активній черзі.")
    target_user = conn.execute("SELECT * FROM users WHERE id = ?", (target["user_id"],)).fetchone()
    if not target_user or not target_user["telegram_id"] or target_user["telegram_id"] <= 0:
        raise BotError("Цей студент ще не підключив свій Telegram.")
    sender_user = users.get_user(conn, message.from_user.id)
    sender_name = sender_user["full_name"] if sender_user else "Студент"
    subject, teacher = session_names(subjects, session)

    invite_text = (
        f"🔁 <b>Пропозиція обміну місцями</b>\n\n"
        f"Студент <b>{html.escape(sender_name)}</b> (місце {my_entry['position']}) пропонує помінятися місцями:\n"
        f"📚 <b>{html.escape(subject)} — {html.escape(teacher)}</b>\n\n"
        f"• Ваше поточне місце: <b>{target['position']}</b>\n"
        f"• Нове місце після обміну: <b>{my_entry['position']}</b>"
    )
    invite_keyboard = make_keyboard([
        [
            ("✅ Погодитися", f"sw_yes:{my_entry['id']}:{target['id']}"),
            ("❌ Відхилити", f"sw_no:{my_entry['id']}:{target['id']}")
        ]
    ])
    try:
        await message.bot.send_message(target_user["telegram_id"], invite_text, reply_markup=invite_keyboard)
    except Exception:
        raise BotError("Не вдалося надіслати запит студенту.")

    await state.clear()
    await state.update_data(session_id=session_id)
    await open_queue_screen(
        message, conn, subjects, session_id,
        note=f"✅ <b>Запит на обмін надіслано</b> студенту {html.escape(target_user['full_name'])}.",
    )


@router.callback_query(F.data.startswith("sw_yes:"))
async def swap_accept(callback: CallbackQuery, conn, subjects):
    _, sender_entry_id, target_entry_id = callback.data.split(":")
    sender_entry_id, target_entry_id = int(sender_entry_id), int(target_entry_id)

    target_entry = queues.get_entry(conn, target_entry_id)
    if not target_entry or target_entry["status"] != "active":
        raise BotError("Цей запис уже неактивний або чергу змінено.")

    target_user = conn.execute("SELECT * FROM users WHERE id = ?", (target_entry["user_id"],)).fetchone()
    if not target_user or target_user["telegram_id"] != callback.from_user.id:
        raise BotError("Ця пропозиція призначена для іншого студента.")

    sender_entry = queues.get_entry(conn, sender_entry_id)
    if not sender_entry or sender_entry["status"] != "active":
        raise BotError("Студент, який надсилав запит, уже змінив позицію або вийшов.")

    session = get_session_or_fail(conn, target_entry["session_id"])
    subject, teacher = session_names(subjects, session)
    sender_user = conn.execute("SELECT * FROM users WHERE id = ?", (sender_entry["user_id"],)).fetchone()

    new_sender_pos, new_target_pos = queues.swap_entries(conn, sender_entry_id, target_entry_id)

    await show(
        callback,
        f"✅ <b>Обмін успішний</b>\n\n"
        f"Ви обмінялися місцями з <b>{html.escape(sender_user['full_name'])}</b>.\n"
        f"Ваше нове місце: <b>{new_target_pos}</b>."
    )

    if sender_user and sender_user["telegram_id"] and sender_user["telegram_id"] > 0:
        with suppress(Exception):
            await callback.bot.send_message(
                sender_user["telegram_id"],
                f"✅ <b>Обмін успішний</b>\n\n"
                f"Студент <b>{html.escape(target_user['full_name'])}</b> погодився на обмін.\n"
                f"Ваше нове місце: <b>{new_sender_pos}</b>.",
                reply_markup=get_reply_menu(conn, sender_user["telegram_id"]),
            )


@router.callback_query(F.data.startswith("sw_no:"))
async def swap_decline(callback: CallbackQuery, conn, subjects):
    _, sender_entry_id, target_entry_id = callback.data.split(":")
    sender_entry_id, target_entry_id = int(sender_entry_id), int(target_entry_id)

    target_entry = queues.get_entry(conn, target_entry_id)
    target_user = conn.execute("SELECT * FROM users WHERE id = ?", (target_entry["user_id"],)).fetchone() if target_entry else None
    if target_user and target_user["telegram_id"] != callback.from_user.id:
        raise BotError("Ця пропозиція призначена для іншого студента.")

    sender_entry = queues.get_entry(conn, sender_entry_id)
    sender_user = conn.execute("SELECT * FROM users WHERE id = ?", (sender_entry["user_id"],)).fetchone() if sender_entry else None

    await show(callback, "❌ <b>Ви відхилили пропозицію обміну місцями.</b>")

    if sender_user and sender_user["telegram_id"] and sender_user["telegram_id"] > 0:
        target_name = target_user["full_name"] if target_user else "Студент"
        with suppress(Exception):
            await callback.bot.send_message(
                sender_user["telegram_id"],
                f"❌ Студент <b>{html.escape(target_name)}</b> відхилив пропозицію обміну місцями."
            )


# ---------- Створення черги ----------

@router.message(F.text.in_({"➕ Відкрити чергу", "➕ Створити чергу"}), not_admin_state_filter)
async def start_open_queue(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    await state.set_state(StudentFlow.creating_queue_type)
    keyboard = make_reply_keyboard([
        ["📖 Звичайна пара", "💬 Консультація"],
        ["🏠 Головне меню"],
    ])
    await message.answer("Оберіть тип черги, яку бажаєте відкрити 👇", reply_markup=keyboard)


@router.message(StudentFlow.creating_queue_type, F.text.in_({"📖 Звичайна пара", "💬 Консультація"}))
async def queue_type_selected(message: Message, state: FSMContext, subjects):
    q_type = "consultation" if "Консультація" in message.text else "lesson"
    await state.set_state(StudentFlow.creating_queue_subject)
    await state.update_data(queue_type=q_type)
    type_name = "консультації" if q_type == "consultation" else "пари"
    rows = [[f"➕ {s['name']}"] for s in subjects]
    rows.append(["🔙 Назад", "🏠 Головне меню"])
    await message.answer(
        f"Оберіть предмет для {type_name} на кнопках внизу 👇",
        reply_markup=make_reply_keyboard(rows),
    )


@router.message(StudentFlow.creating_queue_subject, F.text == "🔙 Назад")
async def back_to_queue_type(message: Message, state: FSMContext, conn, subjects):
    await start_open_queue(message, state, conn, subjects)


@router.message(StudentFlow.creating_queue_subject, F.text.startswith("➕"))
async def new_queue_subject_selected(message: Message, state: FSMContext, conn, subjects):
    if message.text in {"➕ Додати адміністратора", "➕ Додати студента", "➕ Додати студентів", "➕ Створити чергу", "➕ Відкрити чергу"}:
        return
    raw_name = message.text.removeprefix("➕").strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == raw_name.lower()), None)
    if not matched_sub:
        return

    data = await state.get_data()
    q_type = data.get("queue_type", "lesson")
    type_label = "консультацію" if q_type == "consultation" else "пару"
    type_badge = " [Консультація]" if q_type == "consultation" else ""

    if len(matched_sub["teachers"]) == 1:
        teacher = matched_sub["teachers"][0]
        existing = queues.find_active_session_by_subject_teacher(conn, matched_sub["id"], teacher["id"], queue_type=q_type)
        if existing:
            await state.clear()
            status_text = "відкрита для запису" if existing["status"] == "open" else "закрита для запису"
            await open_queue_screen(
                message, conn, subjects, existing["id"],
                note=f"ℹ️ Черга на {type_label} для предмета <b>{matched_sub['name']}</b> ({teacher['name']}) вже створена ({status_text})!"
            )
            return

        try:
            session_id = queues.create_queue(conn, message.from_user.id, matched_sub["id"], teacher["id"], queue_type=q_type)
            await state.clear()
            await state.update_data(session_id=session_id)
            user = users.get_user(conn, message.from_user.id)
            creator_name = user["full_name"] if user else (message.from_user.full_name or "Студент")
            await notify_admins_queue_opened(
                message.bot, conn, message.from_user.id, creator_name,
                matched_sub["name"] + type_badge, teacher["name"]
            )
            await open_queue_screen(message, conn, subjects, session_id, note=f"🎉 <b>Чергу на {type_label} успішно створено!</b>")
        except BotError as err:
            await state.clear()
            await message.answer(f"{err}", reply_markup=get_reply_menu(conn, message.from_user.id))
        return

    await state.set_state(StudentFlow.creating_queue_teacher)
    rows = [[f"👨‍🏫 {matched_sub['name']} — {t['name']}"] for t in matched_sub["teachers"]]
    rows.append(["🔙 Назад", "🏠 Головне меню"])
    await message.answer(f"Оберіть викладача для предмета «{matched_sub['name']}» 👇", reply_markup=make_reply_keyboard(rows))


@router.message(StudentFlow.creating_queue_teacher, F.text == "🔙 Назад")
async def back_to_queue_subject(message: Message, state: FSMContext, subjects):
    data = await state.get_data()
    q_type = data.get("queue_type", "lesson")
    type_name = "консультації" if q_type == "consultation" else "пари"
    await state.set_state(StudentFlow.creating_queue_subject)
    rows = [[f"➕ {s['name']}"] for s in subjects]
    rows.append(["🔙 Назад", "🏠 Головне меню"])
    await message.answer(
        f"Оберіть предмет для {type_name} на кнопках внизу 👇",
        reply_markup=make_reply_keyboard(rows),
    )


@router.message(StudentFlow.creating_queue_teacher, F.text.startswith("👨‍🏫"))
async def new_queue_teacher_selected(message: Message, state: FSMContext, conn, subjects):
    raw = message.text.removeprefix("👨‍🏫").strip()
    if " — " in raw:
        parts = raw.split(" — ", 1)
    elif " - " in raw:
        parts = raw.split(" - ", 1)
    elif "–" in raw:
        parts = raw.split("–", 1)
    elif "—" in raw:
        parts = raw.split("—", 1)
    else:
        parts = raw.split("-", 1)
    if len(parts) != 2:
        return
    sub_name, teacher_name = parts[0].strip(), parts[1].strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == sub_name.lower()), None)
    if not matched_sub:
        return
    matched_teacher = next((t for t in matched_sub["teachers"] if t["name"].lower() == teacher_name.lower()), None)
    if not matched_teacher:
        return

    data = await state.get_data()
    q_type = data.get("queue_type", "lesson")
    type_label = "консультацію" if q_type == "consultation" else "пару"
    type_badge = " [Консультація]" if q_type == "consultation" else ""

    existing = queues.find_active_session_by_subject_teacher(conn, matched_sub["id"], matched_teacher["id"], queue_type=q_type)
    if existing:
        await state.clear()
        status_text = "відкрита для запису" if existing["status"] == "open" else "закрита для запису"
        await open_queue_screen(
            message, conn, subjects, existing["id"],
            note=f"ℹ️ Черга на {type_label} для предмета <b>{matched_sub['name']}</b> ({matched_teacher['name']}) вже створена ({status_text})!"
        )
        return

    try:
        session_id = queues.create_queue(conn, message.from_user.id, matched_sub["id"], matched_teacher["id"], queue_type=q_type)
        await state.clear()
        await state.update_data(session_id=session_id)
        user = users.get_user(conn, message.from_user.id)
        creator_name = user["full_name"] if user else (message.from_user.full_name or "Студент")
        await notify_admins_queue_opened(
            message.bot, conn, message.from_user.id, creator_name,
            matched_sub["name"] + type_badge, matched_teacher["name"]
        )
        await open_queue_screen(message, conn, subjects, session_id, note=f"🎉 <b>Чергу на {type_label} успішно створено!</b>")
    except BotError as err:
        await state.clear()
        await message.answer(f"{err}", reply_markup=get_reply_menu(conn, message.from_user.id))


# ---------- Зворотна сумісність ----------

@router.callback_query(F.data.startswith("vq:"))
async def legacy_view_queue(callback: CallbackQuery, conn, subjects, state: FSMContext):
    session_id = int(callback.data.split(":")[1])
    await state.clear()
    await state.update_data(session_id=session_id)
    with suppress(Exception):
        await callback.message.delete()
    await open_queue_screen(callback.message, conn, subjects, session_id)


@router.callback_query(F.data.startswith(("sd:", "sn:")))
async def legacy_button_finish(callback: CallbackQuery, conn, subjects):
    prefix, session_id = callback.data.split(":")
    session_id = int(session_id)
    passed = prefix == "sd"
    queues.finish_own_entry(conn, callback.from_user.id, session_id, passed)
    note = "✅ <b>Здачу зараховано</b>\n\nВас знято з черги." if passed else "❌ <b>Позначено: не здав</b>"
    with suppress(Exception):
        await callback.message.delete()
    await open_queue_screen(callback.message, conn, subjects, session_id, note=note)


@router.callback_query(F.data.startswith("lv:"))
async def legacy_ask_leave(callback: CallbackQuery, conn):
    session_id = int(callback.data.split(":")[1])
    queues.leave_queue(conn, callback.from_user.id, session_id)
    await show(callback, "🚪 <b>Ви вийшли з черги.</b>")
