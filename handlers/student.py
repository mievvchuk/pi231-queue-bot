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
    get_queue_view_reply_menu,
    get_queues_reply_menu,
    get_reply_menu,
    make_keyboard,
    make_reply_keyboard,
    queue_label,
    session_names,
    show,
)
from services import admins, queues, users
from services.errors import BotError
from subjects import find_subject

log = logging.getLogger(__name__)
router = Router()

BACK_TO_MENU = [("Головне меню", "menu")]


class StudentFlow(StatesGroup):
    pick_position = State()
    choosing_swap_target = State()
    confirm_swap = State()


def get_session_or_fail(conn, session_id: int):
    session = queues.get_session(conn, session_id)
    if session is None:
        raise BotError("Чергу не знайдено.")
    return session


async def open_queue_screen(message: Message, conn, subjects, session_id: int, note: str = ""):
    session = get_session_or_fail(conn, session_id)
    entries = queues.get_entries(conn, session_id)
    is_adm = admins.is_admin(conn, message.from_user.id)
    text = format_queue(subjects, session, entries, message.from_user.id, show_id=is_adm)
    if note:
        text = f"{note}\n\n{text}"
    markup = get_queue_view_reply_menu(conn, session_id, message.from_user.id, is_open=session["status"] == "open")
    await message.answer(text, reply_markup=markup)


# ---------- Список черг та навігація (Нижні кнопки) ----------

@router.message(F.text == "📋 Стати в чергу", StateFilter("*"))
async def choose_open_queue(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    open_sessions = queues.list_sessions(conn, ("open",))
    if not open_sessions:
        await message.answer(
            "Зараз немає черг із відкритим записом.",
            reply_markup=get_reply_menu(conn, message.from_user.id),
        )
        return
    rows = [[f"📋 {queue_label(subjects, s)}"] for s in open_sessions]
    rows.append(["🏠 Головне меню"])
    await message.answer(
        "Оберіть чергу для запису на кнопках внизу 👇",
        reply_markup=make_reply_keyboard(rows),
    )


@router.message(F.text == "🔍 Переглянути черги", StateFilter("*"))
async def view_all_queues(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    sessions = queues.list_sessions(conn, ("open", "closed"))
    if not sessions:
        await message.answer(
            "Активних черг зараз немає.",
            reply_markup=get_reply_menu(conn, message.from_user.id),
        )
        return
    await message.answer(
        "Оберіть чергу на кнопках внизу 👇",
        reply_markup=get_queues_reply_menu(sessions, subjects),
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
    await message.answer(
        "Ваші черги. Оберіть чергу на кнопках внизу 👇",
        reply_markup=get_queues_reply_menu(sessions, subjects),
    )


@router.message(F.text == "🔄 Оновити список", StateFilter("*"))
async def refresh_queues_list(message: Message, state: FSMContext, conn, subjects):
    sessions = queues.list_sessions(conn, ("open", "closed"))
    if not sessions:
        await message.answer("Активних черг немає.", reply_markup=get_reply_menu(conn, message.from_user.id))
        return
    await message.answer(
        "Список черг оновлено 👇",
        reply_markup=get_queues_reply_menu(sessions, subjects),
    )


@router.message(F.text == "🔙 До списку черг", StateFilter("*"))
async def back_to_queues_list(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    sessions = queues.list_sessions(conn, ("open", "closed"))
    if not sessions:
        await message.answer("Активних черг немає.", reply_markup=get_reply_menu(conn, message.from_user.id))
        return
    await message.answer(
        "Оберіть чергу на кнопках внизу 👇",
        reply_markup=get_queues_reply_menu(sessions, subjects),
    )


@router.message(F.text.startswith("📋 "), StateFilter("*"))
async def select_queue_by_button(message: Message, state: FSMContext, conn, subjects):
    current_state = await state.get_state()
    # Якщо адмін обирає чергу в адмін-режимі, пропускаємо до admin.py
    if current_state and current_state.startswith("AdminPanelState"):
        return
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


# ---------- Дії всередині черги (Нижні кнопки) ----------

@router.message(F.text == "🔄 Оновити", StateFilter("*"))
async def refresh_current_queue(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        sessions = queues.list_user_sessions(conn, message.from_user.id)
        if sessions:
            session_id = sessions[0]["id"]
            await state.update_data(session_id=session_id)
        else:
            await message.answer("Оберіть чергу на кнопках внизу 👇", reply_markup=get_reply_menu(conn, message.from_user.id))
            return
    await open_queue_screen(message, conn, subjects, session_id)


@router.message(F.text == "📍 Моє місце", StateFilter("*"))
async def show_my_place(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        sessions = queues.list_user_sessions(conn, message.from_user.id)
        if sessions:
            session_id = sessions[0]["id"]
            await state.update_data(session_id=session_id)
        else:
            await message.answer("Ви не записані в цю чергу.", reply_markup=get_reply_menu(conn, message.from_user.id))
            return
    place = queues.get_user_position(conn, message.from_user.id, session_id)
    total = len(queues.get_entries(conn, session_id))
    note = f"📍 Ваше місце в черзі: {place} з {total}" if place else "Ви не записані в цю чергу."
    await open_queue_screen(message, conn, subjects, session_id, note=note)


@router.message(F.text.in_({"✅ Здав", "здав", "здала"}), StateFilter("*"))
async def mark_passed(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        sessions = queues.list_user_sessions(conn, message.from_user.id)
        if len(sessions) == 1:
            session_id = sessions[0]["id"]
            await state.update_data(session_id=session_id)
        elif not sessions:
            raise BotError("Ви не записані в жодну чергу.")
        else:
            rows = [[f"📋 {queue_label(subjects, s)}"] for s in sessions]
            rows.append(["🏠 Головне меню"])
            await message.answer("Оберіть чергу, де ви здали 👇", reply_markup=make_reply_keyboard(rows))
            return
    queues.finish_own_entry(conn, message.from_user.id, session_id, passed=True)
    await open_queue_screen(message, conn, subjects, session_id, note="✅ Позначено: здав! Вас знято з черги.")


@router.message(F.text.in_({"❌ Не здав", "не здав", "не здала"}), StateFilter("*"))
async def mark_not_passed(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        sessions = queues.list_user_sessions(conn, message.from_user.id)
        if len(sessions) == 1:
            session_id = sessions[0]["id"]
            await state.update_data(session_id=session_id)
        elif not sessions:
            raise BotError("Ви не записані в жодну чергу.")
        else:
            rows = [[f"📋 {queue_label(subjects, s)}"] for s in sessions]
            rows.append(["🏠 Головне меню"])
            await message.answer("Оберіть чергу, де ви не здали 👇", reply_markup=make_reply_keyboard(rows))
            return
    new_place = queues.finish_own_entry(conn, message.from_user.id, session_id, passed=False)
    await open_queue_screen(message, conn, subjects, session_id, note=f"❌ Позначено: не здав. Вас переміщено в кінець черги (місце {new_place}).")


@router.message(F.text == "🚪 Покинути чергу", StateFilter("*"))
async def ask_leave_queue(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        raise BotError("Оберіть чергу.")
    if queues.get_user_position(conn, message.from_user.id, session_id) is None:
        raise BotError("Ви не записані в цю чергу.")
    markup = make_reply_keyboard([["⚠️ Підтвердити вихід"], ["🔙 Назад до черги"]])
    await message.answer("Покинути чергу? Ваше місце буде втрачено.", reply_markup=markup)


@router.message(F.text == "⚠️ Підтвердити вихід", StateFilter("*"))
async def confirm_leave_queue(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        raise BotError("Оберіть чергу.")
    queues.leave_queue(conn, message.from_user.id, session_id)
    await open_queue_screen(message, conn, subjects, session_id, note="🚪 Ви покинули чергу.")


@router.message(F.text == "🔙 Назад до черги", StateFilter("*"))
async def back_to_current_queue(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        await message.answer("Головне меню 👇", reply_markup=get_reply_menu(conn, message.from_user.id))
        return
    await state.clear()
    await state.update_data(session_id=session_id)
    await open_queue_screen(message, conn, subjects, session_id)


# ---------- Запис у чергу (Нижні кнопки) ----------

@router.message(F.text == "📝 Записатися в чергу", StateFilter("*"))
async def ask_join_options(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        raise BotError("Оберіть чергу.")
    markup = make_reply_keyboard([
        ["➡️ В кінець черги"],
        ["🔢 Обрати номер"],
        ["🔙 Назад до черги"],
    ])
    await message.answer("Як ви бажаєте записатися в чергу?", reply_markup=markup)


@router.message(F.text == "➡️ В кінець черги", StateFilter("*"))
async def join_queue_end(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        raise BotError("Оберіть чергу.")
    position, total = queues.join_queue(conn, message.from_user.id, session_id)
    await open_queue_screen(
        message, conn, subjects, session_id,
        note=f"🎉 Ви успішно записані! Ваш номер: {position} (всього: {total})",
    )


@router.message(F.text == "🔢 Обрати номер", StateFilter("*"))
async def ask_specific_number(message: Message, state: FSMContext):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        raise BotError("Оберіть чергу.")
    await state.set_state(StudentFlow.pick_position)
    await state.update_data(session_id=session_id)
    await message.answer(
        "Введіть бажаний номер у черзі (наприклад 1, 7 чи 15):\n"
        "Якщо перед цим номером будуть пусті місця — вони залишаться вільними для інших студентів.",
        reply_markup=make_reply_keyboard([["🔙 Назад до черги"]]),
    )


@router.message(StudentFlow.pick_position, F.text)
async def handle_join_number(message: Message, state: FSMContext, conn, subjects):
    if message.text == "🔙 Назад до черги":
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
    session_id = data["session_id"]
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
        note=f"🎉 Ви успішно зайняли номер {position}!",
    )


# ---------- Обмін місцями між студентами (Нижні кнопки) ----------

@router.message(F.text == "🔄 Помінятися місцем", StateFilter("*"))
async def start_swap_flow(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        raise BotError("Оберіть чергу.")
    my_pos = queues.get_user_position(conn, message.from_user.id, session_id)
    if my_pos is None:
        raise BotError("Ви не записані в цю чергу.")
    entries = queues.get_entries(conn, session_id)
    candidates = [
        e for e in entries
        if e["user_id"] and e["telegram_id"] and e["telegram_id"] > 0 and e["telegram_id"] != message.from_user.id
    ]
    if not candidates:
        await message.answer("У цій черзі немає інших зареєстрованих студентів, з якими можна помінятися.")
        await open_queue_screen(message, conn, subjects, session_id)
        return
    rows = [[f"🔄 {e['position']}. {e['full_name']}"] for e in candidates]
    rows.append(["🔙 Назад до черги"])
    await state.set_state(StudentFlow.choosing_swap_target)
    await state.update_data(session_id=session_id)
    await message.answer("Оберіть студента на кнопках внизу, з яким хочете помінятися місцями 👇", reply_markup=make_reply_keyboard(rows))


@router.message(StudentFlow.choosing_swap_target, F.text)
async def swap_target_chosen(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    if message.text == "🔙 Назад до черги":
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
        f"🔄 **Запропонувати обмін місцями?**\n\n"
        f"👤 Студент: **{target_name}** (місце {target['position']})\n"
        f"Ваше поточне місце: **{my_pos}**\n\n"
        f"Студенту надійде сповіщення в боті. Якщо він погодиться — ваші місця автоматично поміняються."
    )
    markup = make_reply_keyboard([["✅ Підтвердити обмін"], ["🔙 Назад до черги"]])
    await message.answer(text, reply_markup=markup)


@router.message(StudentFlow.confirm_swap, F.text)
async def swap_confirm_action(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data.get("session_id")
    target_entry_id = data.get("target_entry_id")
    if message.text == "🔙 Назад до черги":
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
        f"🔄 **Пропозиція обміну місцями в черзі!**\n\n"
        f"Студент **{sender_name}** (місце {my_entry['position']}) пропонує помінятися з вами місцями:\n"
        f"📚 **{subject} — {teacher}**\n\n"
        f"• Ваше поточне місце: **{target['position']}**\n"
        f"• Нове місце після обміну: **{my_entry['position']}**\n\n"
        f"Погодитися на обмін?"
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
        raise BotError("Не вдалося надіслати запит студенту (можливо, він заблокував бота).")

    await state.clear()
    await state.update_data(session_id=session_id)
    await open_queue_screen(
        message, conn, subjects, session_id,
        note=f"✅ Запит надіслано студенту {target_user['full_name']}!\nЯк тільки він відповість, ви отримаєте сповіщення."
    )


# Обробники кнопок у повідомленні пропозиції обміну (для отримувача)
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
        raise BotError("Студент, який надсилав запит, уже вийшов з черги або змінив позицію.")

    session = get_session_or_fail(conn, target_entry["session_id"])
    subject, teacher = session_names(subjects, session)
    sender_user = conn.execute("SELECT * FROM users WHERE id = ?", (sender_entry["user_id"],)).fetchone()

    new_sender_pos, new_target_pos = queues.swap_entries(conn, sender_entry_id, target_entry_id)

    await show(
        callback,
        f"🎉 Ви успішно обмінялися місцями з **{sender_user['full_name']}**!\n"
        f"📚 {subject} — {teacher}\n"
        f"Ваше нове місце: **{new_target_pos}**"
    )

    if sender_user and sender_user["telegram_id"] and sender_user["telegram_id"] > 0:
        with suppress(Exception):
            await callback.bot.send_message(
                sender_user["telegram_id"],
                f"🎉 Студент **{target_user['full_name']}** погодився на обмін місцями!\n"
                f"📚 {subject} — {teacher}\n"
                f"Ваше нове місце: **{new_sender_pos}**",
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

    await show(callback, "❌ Ви відхилили пропозицію обміну місцями.")

    if sender_user and sender_user["telegram_id"] and sender_user["telegram_id"] > 0:
        target_name = target_user["full_name"] if target_user else "Студент"
        with suppress(Exception):
            await callback.bot.send_message(
                sender_user["telegram_id"],
                f"❌ Студент **{target_name}** відхилив вашу пропозицію обміну місцями."
            )


# ---------- Створення черги (Нижні кнопки) ----------

@router.message(F.text.in_({"➕ Відкрити чергу", "➕ Створити чергу"}), StateFilter("*"))
async def start_open_queue(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    rows = [[f"➕ {s['name']}"] for s in subjects]
    rows.append(["🏠 Головне меню"])
    await message.answer("Оберіть предмет для нової черги на кнопках внизу 👇", reply_markup=make_reply_keyboard(rows))


@router.message(F.text.startswith("➕ "), StateFilter("*"))
async def new_queue_subject_selected(message: Message, state: FSMContext, conn, subjects):
    raw_name = message.text[2:].strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == raw_name.lower()), None)
    if not matched_sub:
        return
    if len(matched_sub["teachers"]) == 1:
        try:
            session_id = queues.create_queue(conn, message.from_user.id, matched_sub["id"], matched_sub["teachers"][0]["id"])
            await state.clear()
            await state.update_data(session_id=session_id)
            await open_queue_screen(message, conn, subjects, session_id, note="🎉 Чергу успішно створено!")
        except BotError as err:
            await message.answer(f"{err}", reply_markup=get_reply_menu(conn, message.from_user.id))
        return

    rows = [[f"👨‍🏫 {matched_sub['name']} — {t['name']}"] for t in matched_sub["teachers"]]
    rows.append(["🏠 Головне меню"])
    await message.answer(f"Оберіть викладача для предмета «{matched_sub['name']}» 👇", reply_markup=make_reply_keyboard(rows))


@router.message(F.text.startswith("👨‍🏫 "), StateFilter("*"))
async def new_queue_teacher_selected(message: Message, state: FSMContext, conn, subjects):
    raw = message.text[2:].strip()
    parts = raw.split(" — ")
    if len(parts) != 2:
        return
    sub_name, teacher_name = parts[0].strip(), parts[1].strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == sub_name.lower()), None)
    if not matched_sub:
        return
    matched_teacher = next((t for t in matched_sub["teachers"] if t["name"].lower() == teacher_name.lower()), None)
    if not matched_teacher:
        return
    try:
        session_id = queues.create_queue(conn, message.from_user.id, matched_sub["id"], matched_teacher["id"])
        await state.clear()
        await state.update_data(session_id=session_id)
        await open_queue_screen(message, conn, subjects, session_id, note="🎉 Чергу успішно створено!")
    except BotError as err:
        await message.answer(f"{err}", reply_markup=get_reply_menu(conn, message.from_user.id))


# ---------- Зворотна сумісність для застарілих inline-кнопок ----------

@router.callback_query(F.data.startswith("vq:"))
async def legacy_view_queue(callback: CallbackQuery, conn, subjects, state: FSMContext):
    session_id = int(callback.data.split(":")[1])
    await state.clear()
    await state.update_data(session_id=session_id)
    await callback.message.delete()
    await open_queue_screen(callback.message, conn, subjects, session_id)


@router.callback_query(F.data.startswith(("sd:", "sn:")))
async def legacy_button_finish(callback: CallbackQuery, conn, subjects):
    prefix, session_id = callback.data.split(":")
    session_id = int(session_id)
    passed = prefix == "sd"
    queues.finish_own_entry(conn, callback.from_user.id, session_id, passed)
    note = "Позначено: здав." if passed else "Позначено: не здав."
    await callback.message.delete()
    await open_queue_screen(callback.message, conn, subjects, session_id, note=note)


@router.callback_query(F.data.startswith("lv:"))
async def legacy_ask_leave(callback: CallbackQuery, conn):
    session_id = int(callback.data.split(":")[1])
    queues.leave_queue(conn, callback.from_user.id, session_id)
    await show(callback, "Ви покинули чергу.")
