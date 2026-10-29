import html
import logging
import re
from contextlib import suppress

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from queue_bot import config
from queue_bot.services import subjects as subjects_module
from queue_bot.handlers.common import (
    categorize_sessions,
    find_session_by_label,
    format_category_queues_overview,
    format_my_queues_overview,
    format_queue,
    format_queue_categories_overview,
    format_queues_overview,
    get_category_queues_reply_menu,
    get_coursework_queues_menu,
    get_lost_context_menu,
    get_paginated_reply_keyboard,
    get_queue_actions_reply_menu,
    get_queue_categories_reply_menu,
    get_queue_view_reply_menu,
    get_queues_reply_menu,
    get_reply_menu,
    make_keyboard,
    make_reply_keyboard,
    notify_admins_queue_opened,
    notify_next_students,
    notify_swap_wishes,
    paginate,
    queue_label,
    session_names,
    show,
)
from queue_bot.services import admins, queues, users, swap_requests
from queue_bot.services.errors import BotError
from queue_bot.handlers.completions import CompletionFlow, begin_completion, receive_passed_labs, passed_selection_callback
from queue_bot.services import lab_catalog
from queue_bot.handlers.lab_counts import (
    LAB_COUNT_BUTTON, TIME_BUTTON, LabCountFlow, ask_lab_count, ask_planned_minutes,
    begin_lab_count, receive_lab_count, receive_planned_minutes, lab_selection_callback,
)

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")


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
    suggesting_choice = State()
    suggesting_subject_for_teacher = State()
    suggesting_teacher_name = State()
    suggesting_new_subject_name = State()
    suggesting_new_subject_teacher = State()


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


async def open_queue_screen(message: Message, conn, subjects, session_id: int, note: str = "", *, telegram_id=None):
    session = get_session_or_fail(conn, session_id)
    entries = queues.get_entries(conn, session_id)
    uid = telegram_id if telegram_id is not None else message.from_user.id
    is_adm = admins.is_admin(conn, uid)
    text = '👤 <b>Режим студента</b>\n\n' + format_queue(subjects, session, entries, uid, show_id=is_adm)
    if note:
        text = f"{note}\n\n{text}"
    in_queue = queues.get_user_position(conn, uid, session_id) is not None
    markup = get_queue_view_reply_menu(is_admin=is_adm, can_edit_labs=in_queue and session['status'] != 'archived' and bool(lab_catalog.labs_for_session(session)))
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
        "📋 Список користувачів",
        "📋 Черги",
        "📋 Черги та студенти",
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
    await state.update_data(category=None, page=1)
    lessons, consults, coursework = categorize_sessions(sessions, subjects)
    text = format_queue_categories_overview(len(lessons), len(consults), len(coursework))
    markup = get_queue_categories_reply_menu(len(lessons), len(consults), len(coursework))
    await message.answer(text, reply_markup=markup)


def is_category_button(message: Message) -> bool:
    if not message.text:
        return False
    t = message.text.strip()
    return (
        t.startswith("📖 Черги на пари")
        or t.startswith("💬 Консультації")
        or t.startswith("📑 Курсова робота")
        or t.startswith("📑 Курсові роботи")
    )


@router.message(is_category_button, not_admin_state_filter)
async def open_queue_category(message: Message, state: FSMContext, conn, subjects):
    t = message.text.lower()
    if "пари" in t:
        category = "lessons"
    elif "консультац" in t:
        category = "consultations"
    elif "курсов" in t:
        category = "coursework"
    else:
        await view_all_queues(message, state, conn, subjects)
        return

    sessions = queues.list_sessions(conn, ("open", "closed"))
    lessons, consults, coursework = categorize_sessions(sessions, subjects)
    cat_map = {
        "lessons": lessons,
        "consultations": consults,
        "coursework": coursework,
    }
    cat_sessions = cat_map.get(category, [])
    if not cat_sessions:
        await message.answer(
            "У цій категорії зараз немає активних черг.",
            reply_markup=get_queue_categories_reply_menu(len(lessons), len(consults), len(coursework)),
        )
        return

    await state.set_state(StudentFlow.browsing_queues)
    await state.update_data(category=category, page=1)
    text = format_category_queues_overview(conn, cat_sessions, subjects, category, page=1)
    markup = get_category_queues_reply_menu(cat_sessions, subjects, page=1, back_text="🔙 До категорій")
    await message.answer(text, reply_markup=markup)


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
    text = format_my_queues_overview(conn, sessions, subjects, message.from_user.id)
    await message.answer(
        text,
        reply_markup=get_queues_reply_menu(sessions, subjects, page=1, group_coursework=False),
    )


@router.message(StudentFlow.browsing_queues, F.text.in_({"⬅️", "➡️"}))
async def paginate_all_queues(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    category = data.get("category")
    sessions = queues.list_sessions(conn, ("open", "closed"))
    if not category:
        lessons, consults, coursework = categorize_sessions(sessions, subjects)
        text = format_queue_categories_overview(len(lessons), len(consults), len(coursework))
        markup = get_queue_categories_reply_menu(len(lessons), len(consults), len(coursework))
        await message.answer(text, reply_markup=markup)
        return

    lessons, consults, coursework = categorize_sessions(sessions, subjects)
    cat_map = {"lessons": lessons, "consultations": consults, "coursework": coursework}
    cat_sessions = cat_map.get(category, [])
    if not cat_sessions:
        await view_all_queues(message, state, conn, subjects)
        return

    page = data.get("page", 1)
    total_pages = (len(cat_sessions) + 7) // 8 or 1
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    page = max(1, min(page, total_pages))
    await state.update_data(page=page)

    text = format_category_queues_overview(conn, cat_sessions, subjects, category, page=page, total_pages=total_pages)
    markup = get_category_queues_reply_menu(cat_sessions, subjects, page=page, back_text="🔙 До категорій")
    await message.answer(text, reply_markup=markup)


@router.message(StudentFlow.browsing_my_queues, F.text.in_({"⬅️", "➡️"}))
async def paginate_my_queues(message: Message, state: FSMContext, conn, subjects):
    sessions = queues.list_user_sessions(conn, message.from_user.id)
    data = await state.get_data()
    page = data.get("page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await state.update_data(page=page)
    text = format_my_queues_overview(conn, sessions, subjects, message.from_user.id)
    await message.answer(
        text,
        reply_markup=get_queues_reply_menu(sessions, subjects, page=page, group_coursework=False),
    )


@router.message(F.text.in_({"🔄 Оновити список", "🔄 Оновити"}), StudentFlow.browsing_queues)
async def refresh_all_queues_list(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    category = data.get("category")
    sessions = queues.list_sessions(conn, ("open", "closed"))
    if not sessions:
        await state.clear()
        await message.answer("Активних черг немає.", reply_markup=get_reply_menu(conn, message.from_user.id))
        return

    lessons, consults, coursework = categorize_sessions(sessions, subjects)

    if category:
        cat_map = {"lessons": lessons, "consultations": consults, "coursework": coursework}
        cat_sessions = cat_map.get(category, [])
        if not cat_sessions:
            await message.answer("У цій категорії більше немає активних черг.")
            await view_all_queues(message, state, conn, subjects)
            return

        page = data.get("page", 1)
        total_pages = (len(cat_sessions) + 7) // 8 or 1
        page = min(page, total_pages)
        await state.update_data(page=page)
        text = format_category_queues_overview(conn, cat_sessions, subjects, category, page=page, total_pages=total_pages)
        markup = get_category_queues_reply_menu(cat_sessions, subjects, page=page, back_text="🔙 До категорій")
        await message.answer(text, reply_markup=markup)
    else:
        text = format_queue_categories_overview(len(lessons), len(consults), len(coursework))
        markup = get_queue_categories_reply_menu(len(lessons), len(consults), len(coursework))
        await message.answer(text, reply_markup=markup)


@router.message(F.text.in_({"🔄 Оновити список", "🔄 Оновити"}), StudentFlow.browsing_my_queues)
async def refresh_my_queues_list(message: Message, state: FSMContext, conn, subjects):
    sessions = queues.list_user_sessions(conn, message.from_user.id)
    data = await state.get_data()
    page = data.get("page", 1)
    if not sessions:
        await message.answer("Ви не записані в жодну чергу.", reply_markup=get_reply_menu(conn, message.from_user.id))
        return
    text = format_my_queues_overview(conn, sessions, subjects, message.from_user.id)
    await message.answer(
        text,
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

    cw_lines = [f"📚 <b>{html.escape(cw_name)}</b>\n", "Список черг за керівниками:"]
    for s in cw_sessions:
        _, teacher = session_names(subjects, s)
        entries = queues.get_entries(conn, s["id"])
        count = len(entries)
        st_text = "🟢 відкрито" if s["status"] == "open" else "🔴 закрито"
        cw_lines.append(f"• 👨‍🏫 <b>{html.escape(teacher)}</b> — {st_text} (у черзі: <b>{count}</b>)")
    cw_lines.append("\nОберіть керівника на кнопках внизу 👇")

    await state.set_state(StudentFlow.browsing_coursework_queues)
    await state.update_data(coursework_name=cw_name, page=1)
    await message.answer(
        "\n".join(cw_lines),
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


@router.message(F.text.in_({"🔙 До категорій", "🔙 До черг", "🔙 До списку черг"}), not_admin_state_filter)
async def back_to_queues_list(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    await view_all_queues(message, state, conn, subjects)


@router.message(student_queue_button_filter)
async def select_queue_by_button(message: Message, state: FSMContext, conn, subjects):
    session = find_session_by_label(conn, subjects, message.text)
    if not session:
        sessions = queues.list_sessions(conn, ("open", "closed"))
        lessons, consults, coursework = categorize_sessions(sessions, subjects)
        await message.answer(
            "⚠️ Чергу не знайдено або її закрито. Оберіть категорію активних черг 👇",
            reply_markup=get_queue_categories_reply_menu(len(lessons), len(consults), len(coursework)),
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
    can_pick = not queues.are_first_10_occupied(conn, session_id) or queues.has_free_slots(conn, session_id)
    swap_wish = queues.get_swap_wish(conn, message.from_user.id, session_id) if is_in_queue else None
    ready_to_swap = queues.is_ready_to_swap(conn, message.from_user.id, session_id) if is_in_queue else False
    markup = get_queue_actions_reply_menu(
        is_in_queue, is_open=is_open, can_pick_number=can_pick, swap_wish=swap_wish, ready_to_swap=ready_to_swap,
        labs_available=bool(lab_catalog.labs_for_session(session)),
        consultation=session['queue_type'] == 'consultation' or session['subject_id'] == 7,
    )
    await message.answer("⚙️ <b>Оберіть дію:</b>", reply_markup=markup)


@router.message(F.text.in_({"⬆️ Хочу вище", "⏸ Зняти «Хочу вище»"}), not_admin_state_filter)
async def handle_swap_wish_higher(message: Message, state: FSMContext, conn, subjects):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    pos = queues.get_user_position(conn, message.from_user.id, session_id)
    if pos is None:
        raise BotError("Ви не записані в цю чергу.")
    if pos == 1 and message.text == "⬆️ Хочу вище":
        raise BotError("Ви вже на 1-му місці в черзі! Якщо не встигаєте підготуватися — оберіть «⬇️ Хочу нижче».")

    new_wish = queues.set_swap_wish(conn, message.from_user.id, session_id, "higher")
    if new_wish == "higher":
        note = (
            "⬆️ <b>Статус оновлено:</b> ви позначені як <b>«хочу вище»</b>.\n"
            "Студенти перед вами бачитимуть це і зможуть запропонувати вам своє місце, якщо їм потрібен додатковий час."
        )
        matches = await notify_swap_wishes(message.bot, conn, subjects, session_id, message.from_user.id, "higher")
        if matches:
            match_names = ", ".join(
                f"<b>{html.escape(m['full_name'])}</b> (місце {m['position']})"
                for m in matches
                if m.get("full_name")
            )
            note += (
                f"\n\n💡 <b>Знайдено збіг!</b> Студенти перед вами, які хочуть опуститися нижче: {match_names}. "
                "Ви можете запропонувати їм обмін через <b>«⚙️ Дії» ➡️ «🔁 Обмінятися місцями»</b>."
            )
    else:
        note = "⏸ <b>Статус «Хочу вище» знято.</b>"
    await open_queue_screen(message, conn, subjects, session_id, note=note)


@router.message(F.text.in_({"⬇️ Хочу нижче", "⏸ Зняти «Хочу нижче»"}), not_admin_state_filter)
async def handle_swap_wish_lower(message: Message, state: FSMContext, conn, subjects):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    pos = queues.get_user_position(conn, message.from_user.id, session_id)
    if pos is None:
        raise BotError("Ви не записані в цю чергу.")
    total = len(queues.get_entries(conn, session_id))
    if total > 1 and pos == total and message.text == "⬇️ Хочу нижче":
        raise BotError("Ви вже останній у черзі! Якщо готові здавати раніше — оберіть «⬆️ Хочу вище».")

    new_wish = queues.set_swap_wish(conn, message.from_user.id, session_id, "lower")
    if new_wish == "lower":
        note = (
            "⬇️ <b>Статус оновлено:</b> ви позначені як <b>«хочу нижче»</b>.\n"
            "Студенти після вас бачитимуть це і зможуть запропонувати обмін, щоб піти здавати раніше."
        )
        matches = await notify_swap_wishes(message.bot, conn, subjects, session_id, message.from_user.id, "lower")
        if matches:
            match_names = ", ".join(
                f"<b>{html.escape(m['full_name'])}</b> (місце {m['position']})"
                for m in matches
                if m.get("full_name")
            )
            note += (
                f"\n\n💡 <b>Знайдено збіг!</b> Студенти після вас, які хочуть піднятися вище: {match_names}. "
                "Ви можете запропонувати їм обмін через <b>«⚙️ Дії» ➡️ «🔁 Обмінятися місцями»</b>."
            )
    else:
        note = "⏸ <b>Статус «Хочу нижче» знято.</b>"
    await open_queue_screen(message, conn, subjects, session_id, note=note)


@router.message(F.text.in_({"🔄 Готовий до обміну", "⏸ Не готовий до обміну"}), not_admin_state_filter)
async def toggle_swap_readiness(message: Message, state: FSMContext, conn, subjects):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    if queues.get_user_position(conn, message.from_user.id, session_id) is None:
        raise BotError("Ви не записані в цю чергу.")
    new_status = queues.toggle_ready_to_swap(conn, message.from_user.id, session_id)
    note = (
        "🔄 <b>Статус оновлено:</b> ви позначені як <b>готовий до обміну місцями</b>.\n"
        "Інші студенти бачитимуть статус у черзі та бачитимуть вас першими у списку для обміну."
        if new_status
        else "⏸ <b>Статус оновлено:</b> готовність до обміну вимкнено."
    )
    await open_queue_screen(message, conn, subjects, session_id, note=note)


@router.message(F.text.in_({"🔙 Назад до черги", "🔙 Назад"}), not_admin_state_filter)
async def back_to_current_queue(message: Message, state: FSMContext, conn, subjects):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    await state.clear()
    await state.update_data(session_id=session_id)
    await open_queue_screen(message, conn, subjects, session_id)


@router.message(F.text.in_({"✅ Здав", "здав", "здала", '✅ Завершити консультацію'}), not_admin_state_filter)
async def mark_passed(message: Message, state: FSMContext, conn, subjects):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    await begin_completion(message, state, conn, session_id, subjects=subjects)


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
    await begin_lab_count(message, state, conn, session_id, subjects=subjects)


@router.message(F.text.in_({"🔢 Обрати місце", "🔢 Обрати номер"}), not_admin_state_filter)
async def ask_specific_number(message: Message, state: FSMContext, conn, subjects):
    session_id = await ensure_session_id(message, state)
    if not session_id:
        return
    session = get_session_or_fail(conn, session_id)
    if session["status"] != "open":
        raise BotError("Запис у цю чергу закрито.")
    if queues.are_first_10_occupied(conn, session_id) and not queues.has_free_slots(conn, session_id):
        await message.answer('ℹ️ Перші 10 місць уже зайняті. Записуємо в кінець черги.')
        await begin_lab_count(message, state, conn, session_id, subjects=subjects)
        return
    await state.set_state(StudentFlow.pick_position)
    await state.update_data(session_id=session_id)
    prompt = (
        "🔢 <b>Вибір місця</b>\n\nВведіть номер вільного місця (наприклад 3 чи 7):"
        if queues.has_free_slots(conn, session_id)
        else "🔢 <b>Вибір місця</b>\n\nВведіть бажаний вільний номер від 1 до 10 (наприклад 3 чи 7):"
    )
    await message.answer(
        prompt,
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
    if not 1 <= pos <= 100:
        await message.answer('Оберіть вільне місце від 1 до 10 або наявне вільне місце в черзі.')
        return
    try:
        await begin_lab_count(message, state, conn, session_id, position=pos, subjects=subjects)
    except BotError as err:
        await message.answer(f"{err}\nСпробуйте інший номер або натисніть «🔙 Назад до черги»:")
        return


router.message(F.text.in_({LAB_COUNT_BUTTON, '🧪 Кількість лабораторних'}), not_admin_state_filter)(ask_lab_count)
router.message(F.text == TIME_BUTTON, not_admin_state_filter)(ask_planned_minutes)


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

    def candidate_sort_key(e):
        wish = e.get("swap_wish")
        c_pos = e["position"]
        if c_pos < my_pos and wish == "lower":
            return (0, c_pos)
        if c_pos > my_pos and wish == "higher":
            return (0, c_pos)
        if wish in ("higher", "lower") or bool(e.get("ready_to_swap")):
            return (1, c_pos)
        return (2, c_pos)

    candidates.sort(key=candidate_sort_key)
    page_candidates, cur_page, total_pages = paginate(candidates, 1, per_page=8)
    rows = []
    for e in page_candidates:
        wish = e.get("swap_wish")
        if wish == "higher":
            badge = " ⬆️ [хоче вище]"
        elif wish == "lower":
            badge = " ⬇️ [хоче нижче]"
        elif bool(e.get("ready_to_swap")):
            badge = " 🟢 [готовий]"
        else:
            badge = ""
        rows.append([f"🔄 {e['position']}. {e['full_name']}{badge}"])
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад до черги")
    has_matches = any(candidate_sort_key(e)[0] == 0 for e in candidates)
    has_wishes = any(e.get("swap_wish") for e in candidates)
    if has_matches:
        info_note = "\n\n🟢 <i>Студенти, чиї бажання («хочу вище/нижче») співпадають з вашим обміном, виділені першими!</i>"
    elif has_wishes:
        info_note = "\n\n🟢 <i>Студенти з позначками «хочу вище» або «хочу нижче» виділені першими.</i>"
    else:
        info_note = ""
    await message.answer(f"🔁 <b>Обмін місцями</b>\n\nОберіть студента на кнопках внизу 👇{info_note}", reply_markup=markup)


@router.message(StudentFlow.choosing_swap_target, F.text.in_({"⬅️", "➡️"}))
async def paginate_swap_targets(message: Message, state: FSMContext, conn):
    data = await state.get_data()
    session_id = data.get("session_id")
    page = data.get("swap_page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await state.update_data(swap_page=page)

    my_pos = queues.get_user_position(conn, message.from_user.id, session_id) or 0
    entries = queues.get_entries(conn, session_id)
    candidates = [
        e for e in entries
        if e["user_id"] and e["telegram_id"] and e["telegram_id"] > 0 and e["telegram_id"] != message.from_user.id
    ]

    def candidate_sort_key(e):
        wish = e.get("swap_wish")
        c_pos = e["position"]
        if c_pos < my_pos and wish == "lower":
            return (0, c_pos)
        if c_pos > my_pos and wish == "higher":
            return (0, c_pos)
        if wish in ("higher", "lower") or bool(e.get("ready_to_swap")):
            return (1, c_pos)
        return (2, c_pos)

    candidates.sort(key=candidate_sort_key)
    page_candidates, cur_page, total_pages = paginate(candidates, page, per_page=8)
    rows = []
    for e in page_candidates:
        wish = e.get("swap_wish")
        if wish == "higher":
            badge = " ⬆️ [хоче вище]"
        elif wish == "lower":
            badge = " ⬇️ [хоче нижче]"
        elif bool(e.get("ready_to_swap")):
            badge = " 🟢 [готовий]"
        else:
            badge = ""
        rows.append([f"🔄 {e['position']}. {e['full_name']}{badge}"])
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
    cleaned = (
        message.text.replace("🔄", "")
        .replace("⬆️", "")
        .replace("⬇️", "")
        .replace("🟢", "")
        .replace("[хоче вище]", "")
        .replace("[хоче нижче]", "")
        .replace("[готовий]", "")
        .strip()
    )
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
    target_wish = target.get("swap_wish")
    if target_wish == "lower":
        ready_info = "\n🟢 <i>Студент зазначив, що хоче опуститися нижче в черзі («⬇️ Хочу нижче»)!</i>"
    elif target_wish == "higher":
        ready_info = "\n🟢 <i>Студент зазначив, що хоче піднятися вище в черзі («⬆️ Хочу вище»)!</i>"
    elif bool(target.get("ready_to_swap")):
        ready_info = "\n🟢 <i>Студент зазначив, що готовий помінятися!</i>"
    else:
        ready_info = ""
    text = (
        f"🔁 <b>Запропонувати обмін місцями?</b>\n\n"
        f"👤 Студент: <b>{html.escape(target_name)}</b> (місце {target['position']}){ready_info}\n"
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

    request = swap_requests.create(conn, message.from_user.id, my_entry["id"], target["id"])
    invite_text = (
        f"🔁 <b>Пропозиція обміну місцями</b>\n\n"
        f"Студент <b>{html.escape(sender_name)}</b> (місце {my_entry['position']}) пропонує помінятися місцями:\n"
        f"📚 <b>{html.escape(subject)} — {html.escape(teacher)}</b>\n\n"
        f"• Ваше поточне місце: <b>{target['position']}</b>\n"
        f"• Нове місце після обміну: <b>{my_entry['position']}</b>\n\n"
        "⏳ На відповідь є 10 хв. Потім запит закриється."
    )
    invite_keyboard = make_keyboard([
        [
            ("✅ Погодитися", f"sw_yes:{request['token']}"),
            ("❌ Відхилити", f"sw_no:{request['token']}")
        ]
    ])
    try:
        await message.bot.send_message(target_user["telegram_id"], invite_text, reply_markup=invite_keyboard)
    except Exception:
        swap_requests.cancel_delivery(conn, request["token"])
        raise BotError("Не вдалося надіслати запит студенту.")

    await state.clear()
    await state.update_data(session_id=session_id)
    await open_queue_screen(
        message, conn, subjects, session_id,
        note=f"✅ <b>Запит на обмін надіслано</b> студенту {html.escape(target_user['full_name'])}.",
    )


@router.callback_query(F.data.startswith("sw_yes:"))
async def swap_accept(callback: CallbackQuery, conn, subjects):
    token = callback.data.removeprefix("sw_yes:")
    request, positions = swap_requests.respond(conn, callback.from_user.id, token, accept=True)
    sender_user = conn.execute("SELECT * FROM users WHERE id = ?", (request["sender_user_id"],)).fetchone()
    target_user = conn.execute("SELECT * FROM users WHERE id = ?", (request["target_user_id"],)).fetchone()
    new_sender_pos, new_target_pos = positions

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
    token = callback.data.removeprefix("sw_no:")
    request, _ = swap_requests.respond(conn, callback.from_user.id, token, accept=False)
    target_user = conn.execute("SELECT * FROM users WHERE id = ?", (request["target_user_id"],)).fetchone()
    sender_user = conn.execute("SELECT * FROM users WHERE id = ?", (request["sender_user_id"],)).fetchone()

    await show(callback, "❌ <b>Ви відхилили пропозицію обміну місцями.</b>")

    if sender_user and sender_user["telegram_id"] and sender_user["telegram_id"] > 0:
        target_name = target_user["full_name"] if target_user else "Студент"
        with suppress(Exception):
            await callback.bot.send_message(
                sender_user["telegram_id"],
                f"❌ Студент <b>{html.escape(target_name)}</b> відхилив пропозицію обміну місцями."
            )


@router.callback_query(F.data.startswith("sw_req:"))
async def swap_request_from_notification(callback: CallbackQuery, conn, subjects):
    parts = callback.data.split(":")
    if len(parts) != 3:
        raise BotError("Невірні дані запиту.")
    initiator_entry_id = int(parts[1])
    receiver_entry_id = int(parts[2])

    initiator_entry = queues.get_entry(conn, initiator_entry_id)
    if not initiator_entry or initiator_entry["status"] != "active":
        raise BotError("Цей запис уже неактивний або чергу було змінено.")

    initiator_user = conn.execute("SELECT * FROM users WHERE id = ?", (initiator_entry["user_id"],)).fetchone()
    if not initiator_user or initiator_user["telegram_id"] != callback.from_user.id:
        raise BotError("Ця кнопка призначена для іншого студента.")

    receiver_entry = queues.get_entry(conn, receiver_entry_id)
    if not receiver_entry or receiver_entry["status"] != "active":
        raise BotError("Студент уже змінив позицію або вийшов із черги.")

    receiver_user = conn.execute("SELECT * FROM users WHERE id = ?", (receiver_entry["user_id"],)).fetchone()
    if not receiver_user or not receiver_user["telegram_id"] or receiver_user["telegram_id"] <= 0:
        raise BotError("Студент ще не підключив свій Telegram.")

    session = get_session_or_fail(conn, initiator_entry["session_id"])
    subject, teacher = session_names(subjects, session)

    request = swap_requests.create(conn, callback.from_user.id, initiator_entry_id, receiver_entry_id)
    invite_text = (
        f"🔁 <b>Пропозиція обміну місцями</b>\n\n"
        f"Студент <b>{html.escape(initiator_user['full_name'])}</b> (місце {initiator_entry['position']}) пропонує помінятися місцями:\n"
        f"📚 <b>{html.escape(subject)} — {html.escape(teacher)}</b>\n\n"
        f"• Ваше поточне місце: <b>{receiver_entry['position']}</b>\n"
        f"• Нове місце після обміну: <b>{initiator_entry['position']}</b>\n\n"
        "⏳ На відповідь є 10 хв. Потім запит закриється."
    )
    invite_keyboard = make_keyboard([
        [
            ("✅ Погодитися", f"sw_yes:{request['token']}"),
            ("❌ Відхилити", f"sw_no:{request['token']}"),
        ]
    ])

    try:
        await callback.bot.send_message(receiver_user["telegram_id"], invite_text, reply_markup=invite_keyboard)
    except Exception:
        swap_requests.cancel_delivery(conn, request["token"])
        raise BotError("Не вдалося доставити пропозицію студенту.")

    await show(
        callback,
        f"✅ <b>Запит на обмін надіслано!</b>\n\n"
        f"Студенту <b>{html.escape(receiver_user['full_name'])}</b> надіслано пропозицію обміну місцями "
        f"(з місця {initiator_entry['position']} на місце {receiver_entry['position']}).\n"
        f"Щойно він натисне «Погодитися» — ваші місця оновляться автоматично.",
    )


# ---------- Створення черги ----------

@router.message(F.text.in_({"➕ Відкрити чергу", "➕ Створити чергу"}), StateFilter("*"))
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
    available_subjects = [s for s in subjects if "курсов" not in s["name"].lower()] if q_type == "lesson" else subjects
    rows = [[f"➕ {s['name']}"] for s in available_subjects]
    rows.append(["💡 Додати предмет / викладача"])
    rows.append(["🔙 Назад", "🏠 Головне меню"])
    await message.answer(
        f"Оберіть предмет для {type_name} на кнопках внизу 👇",
        reply_markup=make_reply_keyboard(rows),
    )


@router.message(StudentFlow.creating_queue_subject, F.text == "🔙 Назад")
async def back_to_queue_type(message: Message, state: FSMContext, conn, subjects):
    await start_open_queue(message, state, conn, subjects)


@router.message(StudentFlow.creating_queue_subject, F.text == "💡 Додати предмет / викладача")
@router.message(StudentFlow.creating_queue_teacher, F.text == "💡 Додати предмет / викладача")
async def prompt_student_subject_proposal(message: Message, state: FSMContext):
    await state.set_state(StudentFlow.suggesting_choice)
    keyboard = make_reply_keyboard([
        ["👨‍🏫 Додати викладача до предмету"],
        ["📚 Запропонувати новий предмет"],
        ["🔙 Скасувати"],
    ])
    await message.answer(
        "💡 <b>Запропонувати викладача або предмет</b>\n\n"
        "Оберіть, що саме ви хочете додати. Ваша заявка буде перевірена власником бота, "
        "після чого предмет/викладач з'явиться в меню для всієї групи 👇",
        reply_markup=keyboard,
    )


@router.message(StudentFlow.suggesting_choice, F.text == "👨‍🏫 Додати викладача до предмету")
async def student_suggest_teacher_pick_subject(message: Message, state: FSMContext, subjects):
    await state.set_state(StudentFlow.suggesting_subject_for_teacher)
    rows = [[f"📚 {s['name']}"] for s in subjects]
    rows.append(["🔙 Скасувати"])
    await message.answer(
        "Оберіть предмет, до якого потрібно додати викладача 👇",
        reply_markup=make_reply_keyboard(rows),
    )


@router.message(StudentFlow.suggesting_subject_for_teacher, F.text.startswith("📚 "))
async def student_suggest_teacher_subject_selected(message: Message, state: FSMContext, subjects):
    sub_name = message.text.removeprefix("📚 ").strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == sub_name.lower()), None)
    if not matched_sub:
        await message.answer("Оберіть предмет на кнопках внизу:")
        return

    await state.set_state(StudentFlow.suggesting_teacher_name)
    await state.update_data(suggest_sub_id=matched_sub["id"], suggest_sub_name=matched_sub["name"])

    # Показуємо кнопки вже відомих викладачів групи для швидкого вибору в 1 клік
    all_teachers = subjects_module.list_all_teachers(subjects)
    curr_ids = {t["id"] for t in matched_sub.get("teachers", [])}
    other_teachers = [t["name"] for t in all_teachers if t["id"] not in curr_ids]

    rows = []
    for i in range(0, len(other_teachers), 2):
        rows.append(other_teachers[i:i+2])
    rows.append(["🔙 Скасувати"])

    await message.answer(
        f"👨‍🏫 <b>Додавання викладача до предмета «{matched_sub['name']}»</b>\n\n"
        "Оберіть викладача на кнопках нижче або <b>напишіть ПІБ текстом</b> (наприклад: <code>Барабаш Д.С.</code>) 👇",
        reply_markup=make_reply_keyboard(rows),
    )


@router.message(StudentFlow.suggesting_teacher_name, F.text)
async def student_suggest_teacher_name_received(message: Message, state: FSMContext, conn, subjects):
    if message.text in {"🔙 Скасувати", "↩️ Скасувати", "🔙 Назад"}:
        await state.clear()
        await message.answer("Скасовано. Головне меню 👇", reply_markup=get_reply_menu(conn, message.from_user.id))
        return

    teacher_name = message.text.strip()
    if len(teacher_name) < 3 or len(teacher_name) > 60:
        await message.answer("ПІБ викладача має бути від 3 до 60 символів (наприклад: Барабаш Д.С.). Спробуйте ще раз:")
        return

    data = await state.get_data()
    sub_id = data.get("suggest_sub_id")
    sub_name = data.get("suggest_sub_name", "Предмет")
    user = users.get_user(conn, message.from_user.id)
    user_name = user["full_name"] if user else (message.from_user.full_name or "Студент")

    req = subjects_module.create_subject_request(
        conn=conn,
        user_telegram_id=message.from_user.id,
        user_name=user_name,
        req_type="add_teacher",
        subject_id=sub_id,
        subject_name=sub_name,
        teacher_name=teacher_name,
    )
    await state.clear()

    # Сповіщення власнику бота для підтвердження в 1 клік
    owner_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Додати в бот", callback_data=f"subreq_ok:{req['id']}"),
                InlineKeyboardButton(text="❌ Відхилити", callback_data=f"subreq_no:{req['id']}"),
            ]
        ]
    )
    owner_id = config.OWNER_ID
    uname_str = f" (@{html.escape(message.from_user.username)})" if message.from_user.username else ""
    with suppress(Exception):
        await message.bot.send_message(
            chat_id=owner_id,
            text=(
                f"💡 <b>Заявка на викладача від студента {html.escape(user_name)}</b>{uname_str}\n\n"
                f"📌 Дія: <b>Додати викладача до предмету</b>\n"
                f"📚 Предмет: <b>{html.escape(sub_name)}</b>\n"
                f"👨‍🏫 Викладач: <b>{html.escape(teacher_name)}</b>\n\n"
                "Підтвердити додавання в бот?"
            ),
            reply_markup=owner_kb,
        )

    await message.answer(
        f"✅ <b>Заявку успішно надіслано!</b>\n\n"
        f"Викладача <b>{html.escape(teacher_name)}</b> запропоновано для предмета <b>{html.escape(sub_name)}</b>.\n"
        "Власник бота отримав сповіщення. Щойно заявку буде підтверджено — викладач з'явиться в меню і вам надійде повідомлення.",
        reply_markup=get_reply_menu(conn, message.from_user.id),
    )


@router.message(StudentFlow.suggesting_choice, F.text == "📚 Запропонувати новий предмет")
async def student_suggest_new_subject_start(message: Message, state: FSMContext, conn):
    await state.set_state(StudentFlow.suggesting_new_subject_name)
    await message.answer(
        "📚 <b>Новий предмет</b>\n\nВведіть повну назву предмета (наприклад: <code>Програмування мовою Rust</code>):",
        reply_markup=make_reply_keyboard([["🔙 Скасувати"]]),
    )


@router.message(StudentFlow.suggesting_new_subject_name, F.text)
async def student_suggest_new_subject_name_received(message: Message, state: FSMContext, conn, subjects):
    if message.text in {"🔙 Скасувати", "↩️ Скасувати", "🔙 Назад"}:
        await state.clear()
        await message.answer("Скасовано. Головне меню 👇", reply_markup=get_reply_menu(conn, message.from_user.id))
        return

    sub_name = message.text.strip()
    if len(sub_name) < 2 or len(sub_name) > 80:
        await message.answer("Назва предмета має бути від 2 до 80 символів. Спробуйте ще раз:")
        return

    await state.set_state(StudentFlow.suggesting_new_subject_teacher)
    await state.update_data(suggest_new_sub_name=sub_name)

    all_teachers = subjects_module.list_all_teachers(subjects)
    rows = []
    for i in range(0, len(all_teachers), 2):
        names = [t["name"] for t in all_teachers[i:i+2]]
        rows.append(names)
    rows.append(["🔙 Скасувати"])

    await message.answer(
        f"Тепер вкажіть викладача для предмета «<b>{html.escape(sub_name)}</b>».\n\n"
        "Оберіть викладача на кнопках або <b>введіть ПІБ текстом</b> 👇",
        reply_markup=make_reply_keyboard(rows),
    )


@router.message(StudentFlow.suggesting_new_subject_teacher, F.text)
async def student_suggest_new_subject_teacher_received(message: Message, state: FSMContext, conn):
    if message.text in {"🔙 Скасувати", "↩️ Скасувати", "🔙 Назад"}:
        await state.clear()
        await message.answer("Скасовано. Головне меню 👇", reply_markup=get_reply_menu(conn, message.from_user.id))
        return

    teacher_name = message.text.strip()
    if len(teacher_name) < 3 or len(teacher_name) > 60:
        await message.answer("ПІБ викладача має бути від 3 до 60 символів. Спробуйте ще раз:")
        return

    data = await state.get_data()
    sub_name = data.get("suggest_new_sub_name", "Новий предмет")
    user = users.get_user(conn, message.from_user.id)
    user_name = user["full_name"] if user else (message.from_user.full_name or "Студент")

    req = subjects_module.create_subject_request(
        conn=conn,
        user_telegram_id=message.from_user.id,
        user_name=user_name,
        req_type="add_subject",
        subject_id=None,
        subject_name=sub_name,
        teacher_name=teacher_name,
    )
    await state.clear()

    owner_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Додати в бот", callback_data=f"subreq_ok:{req['id']}"),
                InlineKeyboardButton(text="❌ Відхилити", callback_data=f"subreq_no:{req['id']}"),
            ]
        ]
    )
    owner_id = config.OWNER_ID
    uname_str = f" (@{html.escape(message.from_user.username)})" if message.from_user.username else ""
    with suppress(Exception):
        await message.bot.send_message(
            chat_id=owner_id,
            text=(
                f"💡 <b>Заявка на новий предмет від студента {html.escape(user_name)}</b>{uname_str}\n\n"
                f"📌 Дія: <b>Додати новий предмет</b>\n"
                f"📚 Назва: <b>{html.escape(sub_name)}</b>\n"
                f"👨‍🏫 Викладач: <b>{html.escape(teacher_name)}</b>\n\n"
                "Підтвердити додавання в бот?"
            ),
            reply_markup=owner_kb,
        )

    await message.answer(
        f"✅ <b>Заявку успішно надіслано!</b>\n\n"
        f"Предмет <b>«{html.escape(sub_name)}»</b> з викладачем <b>{html.escape(teacher_name)}</b> передано на модерацію власнику бота.\n"
        "Щойно заявку буде підтверджено — вона з'явиться для всієї групи!",
        reply_markup=get_reply_menu(conn, message.from_user.id),
    )


@router.message(StudentFlow.suggesting_choice, F.text.in_({"🔙 Скасувати", "↩️ Скасувати", "🔙 Назад"}))
@router.message(StudentFlow.suggesting_subject_for_teacher, F.text.in_({"🔙 Скасувати", "↩️ Скасувати", "🔙 Назад"}))
async def cancel_student_suggest(message: Message, state: FSMContext, conn):
    await state.clear()
    await message.answer("Скасовано. Головне меню 👇", reply_markup=get_reply_menu(conn, message.from_user.id))


# ---------- Обробка заявок студентів власником бота ----------

@router.callback_query(F.data.startswith("subreq_ok:"))
async def owner_approve_subject_request(callback: CallbackQuery, conn, subjects):
    if not admins.is_owner(conn, callback.from_user.id):
        await callback.answer("Тільки для власника бота.", show_alert=True)
        return

    req_id = callback.data.split(":")[1]
    req = subjects_module.get_subject_request(conn, req_id)
    if not req or req.get("status") != "pending":
        await callback.answer("Заявка вже оброблена або застаріла.", show_alert=True)
        return

    try:
        if req["type"] == "add_teacher":
            subjects_module.add_teacher_to_subject(
                conn, subjects, req["subject_id"], req["teacher_name"], path=config.SUBJECTS_PATH
            )
            item_desc = f"викладача <b>{html.escape(req['teacher_name'])}</b> до предмета <b>{html.escape(req['subject_name'])}</b>"
        else:
            subjects_module.add_new_subject(
                conn, subjects, req["subject_name"], req["teacher_name"], path=config.SUBJECTS_PATH
            )
            item_desc = f"предмет <b>«{html.escape(req['subject_name'])}»</b> ({html.escape(req['teacher_name'])})"
    except Exception as e:
        await callback.answer(f"Помилка: {e}", show_alert=True)
        return

    subjects_module.update_subject_request_status(conn, req_id, "approved")
    await callback.answer("Схвалено та додано в бот!")
    await callback.message.edit_text(f"✅ <b>Заявку #{req_id} схвалено!</b>\nУспішно додано {item_desc}.", reply_markup=None)

    # Сповіщаємо студента
    with suppress(Exception):
        await callback.bot.send_message(
            chat_id=req["user_telegram_id"],
            text=(
                f"🎉 <b>Вашу заявку схвалено!</b>\n\n"
                f"Успішно додано {item_desc}.\n"
                "Тепер ви можете створити чергу через «➕ Відкрити чергу»!"
            ),
        )


@router.callback_query(F.data.startswith("subreq_no:"))
async def owner_reject_subject_request(callback: CallbackQuery, conn):
    if not admins.is_owner(conn, callback.from_user.id):
        await callback.answer("Тільки для власника бота.", show_alert=True)
        return

    req_id = callback.data.split(":")[1]
    req = subjects_module.get_subject_request(conn, req_id)
    if not req or req.get("status") != "pending":
        await callback.answer("Заявка вже оброблена або застаріла.", show_alert=True)
        return

    subjects_module.update_subject_request_status(conn, req_id, "rejected")
    await callback.answer("Заявку відхилено.")
    await callback.message.edit_text(f"❌ <b>Заявку #{req_id} відхилено.</b>", reply_markup=None)

    with suppress(Exception):
        await callback.bot.send_message(
            chat_id=req["user_telegram_id"],
            text=f"❌ Вашу заявку на додавання «{html.escape(req['subject_name'])}» було відхилено адміністратором.",
        )


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
    if q_type == "lesson" and "курсов" in matched_sub["name"].lower():
        raise BotError("Черга на курсову роботу відкривається через «💬 Консультація».")

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
    rows.append(["💡 Додати предмет / викладача"])
    rows.append(["🔙 Назад", "🏠 Головне меню"])
    await message.answer(f"Оберіть викладача для предмета «{matched_sub['name']}» 👇", reply_markup=make_reply_keyboard(rows))


@router.message(StudentFlow.creating_queue_teacher, F.text == "🔙 Назад")
async def back_to_queue_subject(message: Message, state: FSMContext, subjects):
    data = await state.get_data()
    q_type = data.get("queue_type", "lesson")
    type_name = "консультації" if q_type == "consultation" else "пари"
    await state.set_state(StudentFlow.creating_queue_subject)
    available_subjects = [s for s in subjects if "курсов" not in s["name"].lower()] if q_type == "lesson" else subjects
    rows = [[f"➕ {s['name']}"] for s in available_subjects]
    rows.append(["💡 Додати предмет / викладача"])
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
async def legacy_button_finish(callback: CallbackQuery, conn, subjects, state: FSMContext):
    prefix, session_id = callback.data.split(":")
    session_id = int(session_id)
    passed = prefix == "sd"
    if passed:
        await begin_completion(callback.message, state, conn, session_id, telegram_id=callback.from_user.id, subjects=subjects)
        return
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


# Specific navigation/actions must win over the quantity text fallback.
router.message(LabCountFlow.entering, F.text)(receive_lab_count)
router.message(LabCountFlow.minutes, F.text)(receive_planned_minutes)
router.message(CompletionFlow.count, F.text)(receive_passed_labs)
router.callback_query(F.data.startswith('labpick:'))(lab_selection_callback)
router.callback_query(F.data.startswith('labdone:'))(passed_selection_callback)
