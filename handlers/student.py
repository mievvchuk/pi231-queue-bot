from contextlib import suppress

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from handlers.common import format_queue, make_keyboard, queue_label, session_names, show
from services import admins, queues, users
from services.errors import BotError
from subjects import find_subject

router = Router()


class JoinForm(StatesGroup):
    position = State()

BACK_TO_MENU = [("Головне меню", "menu")]


def get_session_or_fail(conn, session_id: int):
    session = queues.get_session(conn, session_id)
    if session is None:
        raise BotError("Чергу не знайдено.")
    return session


# ---------- Запис до черги ----------

@router.message(F.text == "📋 Стати в чергу")
@router.callback_query(F.data == "join")
async def choose_subject(event, conn, subjects):
    open_subject_ids = {s["subject_id"] for s in queues.list_sessions(conn, ("open",))}
    rows = [[(s["name"], f"js:{s['id']}")] for s in subjects if s["id"] in open_subject_ids]
    if not rows:
        if isinstance(event, Message):
            await event.answer("Зараз немає черг із відкритим записом.", reply_markup=make_keyboard([BACK_TO_MENU]))
        else:
            await show(event, "Зараз немає черг із відкритим записом.", make_keyboard([BACK_TO_MENU]))
        return
    if isinstance(event, Message):
        await event.answer("Оберіть предмет:", reply_markup=make_keyboard(rows + [BACK_TO_MENU]))
    else:
        await show(event, "Оберіть предмет:", make_keyboard(rows + [BACK_TO_MENU]))



@router.callback_query(F.data.startswith("js:"))
async def choose_teacher(callback: CallbackQuery, conn, subjects):
    subject = find_subject(subjects, int(callback.data.split(":")[1]))
    if subject is None:
        raise BotError("Предмет не знайдено.")
    open_sessions = [
        s for s in queues.list_sessions(conn, ("open",)) if s["subject_id"] == subject["id"]
    ]
    if not open_sessions:
        raise BotError("Для цього предмета запис зараз закрито.")
    if len(subject["teachers"]) == 1:  # єдиний викладач обирається автоматично
        await show_join_info(callback, conn, subjects, open_sessions[0]["id"])
        return
    rows = [[(session_names(subjects, s)[1], f"jt:{s['id']}")] for s in open_sessions]
    await show(callback, f"{subject['name']}\nОберіть викладача:",
               make_keyboard(rows + [[("Назад", "join")]]))


@router.callback_query(F.data.startswith("jt:"))
async def teacher_chosen(callback: CallbackQuery, conn, subjects):
    await show_join_info(callback, conn, subjects, int(callback.data.split(":")[1]))


async def show_join_info(callback, conn, subjects, session_id: int):
    session = get_session_or_fail(conn, session_id)
    if session["status"] != "open":
        raise BotError("Запис до цієї черги закрито.")
    text = format_queue(subjects, session, queues.get_entries(conn, session_id), callback.from_user.id)
    text += "\n\nЯк записатися?"
    await show(callback, text, make_keyboard([
        [("В кінець черги", f"jc:{session_id}")],
        [("Обрати номер", f"jp:{session_id}")],
        [("Назад", "join")],
    ]))


def join_success(conn, subjects, session_id: int, position: int, total: int):
    subject, teacher = session_names(subjects, get_session_or_fail(conn, session_id))
    text = (f"Ви успішно записані!\nПредмет: {subject}\nВикладач: {teacher}\n"
            f"Ваш номер: {position}\nУсього студентів: {total}")
    return text, make_keyboard([[("Переглянути чергу", f"vq:{session_id}")], BACK_TO_MENU])


@router.callback_query(F.data.startswith("jc:"))
async def confirm_join(callback: CallbackQuery, conn, subjects):
    session_id = int(callback.data.split(":")[1])
    position, total = queues.join_queue(conn, callback.from_user.id, session_id)
    await show(callback, *join_success(conn, subjects, session_id, position, total))


@router.callback_query(F.data.startswith("jp:"))
async def ask_join_position(callback: CallbackQuery, conn, state: FSMContext):
    session_id = int(callback.data.split(":")[1])
    total = len(queues.get_entries(conn, session_id))
    await state.set_state(JoinForm.position)
    await state.update_data(session_id=session_id)
    await show(callback, f"Введіть бажаний номер у черзі (наприклад 1, 7 чи 15).\n"
               "Якщо перед цим номером будуть пусті місця — вони залишаться вільними для інших студентів.",
               make_keyboard([[("Скасувати", "join")]]))



@router.message(JoinForm.position, F.text)
async def receive_join_position(message: Message, conn, subjects, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("Введіть число, наприклад 3:")
        return
    session_id = (await state.get_data())["session_id"]
    try:
        position, total = queues.join_queue(conn, message.from_user.id, session_id,
                                            int(message.text))
    except BotError as error:
        await message.answer(f"{error}\nСпробуйте ще раз:")
        return
    await state.clear()
    text, markup = join_success(conn, subjects, session_id, position, total)
    await message.answer(text, reply_markup=markup)


# ---------- «Здав» / «Не здав» ----------

PASSED_WORDS = {"здав", "здала"}
NOT_PASSED_WORDS = {"не здав", "не здала"}


@router.message(StateFilter(None), F.text.func(lambda t: t.strip().lower() in PASSED_WORDS | NOT_PASSED_WORDS))
async def text_finish(message: Message, conn, subjects):
    passed = message.text.strip().lower() in PASSED_WORDS
    sessions = queues.list_user_sessions(conn, message.from_user.id)
    if not sessions:
        raise BotError("Ви не записані в жодну чергу.")
    if len(sessions) == 1:
        await message.answer(apply_finish(conn, message.from_user.id, sessions[0]["id"], passed))
        return
    prefix = "sd" if passed else "sn"
    rows = [[(queue_label(subjects, s), f"{prefix}:{s['id']}")] for s in sessions]
    await message.answer("Оберіть чергу:", reply_markup=make_keyboard(rows))


def apply_finish(conn, telegram_id: int, session_id: int, passed: bool) -> str:
    new_place = queues.finish_own_entry(conn, telegram_id, session_id, passed)
    if passed:
        return "Позначено: здав. Вас знято з черги."
    return f"Позначено: не здав. Ви переміщені в кінець черги (місце {new_place})."


@router.callback_query(F.data.startswith(("sd:", "sn:")))
async def button_finish(callback: CallbackQuery, conn):
    prefix, session_id = callback.data.split(":")
    text = apply_finish(conn, callback.from_user.id, int(session_id), prefix == "sd")
    await show(callback, text, make_keyboard([BACK_TO_MENU]))


# ---------- Перегляд черг ----------

@router.message(F.text == "👥 Мої черги")
@router.callback_query(F.data == "my")
async def my_queues(event, conn, subjects):
    sessions = queues.list_user_sessions(conn, event.from_user.id)
    if not sessions:
        text = "Ви ще не записані в жодну чергу."
        if isinstance(event, Message):
            await event.answer(text, reply_markup=make_keyboard([BACK_TO_MENU]))
        else:
            await show(event, text, make_keyboard([BACK_TO_MENU]))
        return
    rows = [[(queue_label(subjects, s), f"vq:{s['id']}")] for s in sessions]
    if isinstance(event, Message):
        await event.answer("Мої черги:", reply_markup=make_keyboard(rows + [BACK_TO_MENU]))
    else:
        await show(event, "Мої черги:", make_keyboard(rows + [BACK_TO_MENU]))


@router.message(F.text == "🔍 Переглянути черги")
@router.callback_query(F.data == "view")
async def all_queues(event, conn, subjects):
    sessions = queues.list_sessions(conn, ("open", "closed"))
    if not sessions:
        text = "Активних черг немає."
        if isinstance(event, Message):
            await event.answer(text, reply_markup=make_keyboard([BACK_TO_MENU]))
        else:
            await show(event, text, make_keyboard([BACK_TO_MENU]))
        return
    rows = [[(queue_label(subjects, s), f"vq:{s['id']}")] for s in sessions]
    if isinstance(event, Message):
        await event.answer("Оберіть чергу:", reply_markup=make_keyboard(rows + [BACK_TO_MENU]))
    else:
        await show(event, "Оберіть чергу:", make_keyboard(rows + [BACK_TO_MENU]))



@router.callback_query(F.data.startswith("vq:"))
async def view_queue(callback: CallbackQuery, conn, subjects):
    await show_queue(callback, conn, subjects, int(callback.data.split(":")[1]))


@router.callback_query(F.data.startswith("mp:"))
async def my_place(callback: CallbackQuery, conn, subjects):
    session_id = int(callback.data.split(":")[1])
    place = queues.get_user_position(conn, callback.from_user.id, session_id)
    total = len(queues.get_entries(conn, session_id))
    note = f"Ваше місце: {place} з {total}" if place else "Ви не записані в цю чергу."
    await show_queue(callback, conn, subjects, session_id, note)


async def show_queue(callback, conn, subjects, session_id: int, note: str = ""):
    session = get_session_or_fail(conn, session_id)
    is_adm = admins.is_admin(conn, callback.from_user.id)
    text = format_queue(subjects, session, queues.get_entries(conn, session_id), callback.from_user.id, show_id=is_adm)
    if note:
        text = f"{note}\n\n{text}"
    rows = []
    if is_adm:
        rows.append([("⚡ Керувати чергою / Студенти", f"go:order:{session_id}")])

    in_queue = queues.get_user_position(conn, callback.from_user.id, session_id) is not None
    if in_queue:
        rows.append([("🔄 Помінятися місцем", f"sw_start:{session_id}")])

    rows.extend([
        [("Оновити список", f"vq:{session_id}"), ("Моє місце", f"mp:{session_id}")],
        [("Здав", f"sd:{session_id}"), ("Не здав", f"sn:{session_id}")],
        [("Покинути чергу", f"lv:{session_id}")],
        [("Назад", "view")],
    ])
    await show(callback, text, make_keyboard(rows))


# ---------- Обмін місцями між студентами ----------

@router.callback_query(F.data.startswith("sw_start:"))
async def swap_choose_student(callback: CallbackQuery, conn, subjects):
    session_id = int(callback.data.split(":")[1])
    my_pos = queues.get_user_position(conn, callback.from_user.id, session_id)
    if my_pos is None:
        raise BotError("Ви не записані в цю чергу.")

    entries = queues.get_entries(conn, session_id)
    # Тільки інші реальні студенти з Telegram ID
    candidates = [
        e for e in entries
        if e["user_id"] and e["telegram_id"] and e["telegram_id"] > 0 and e["telegram_id"] != callback.from_user.id
    ]
    if not candidates:
        await show(
            callback,
            "У цій черзі немає інших зареєстрованих студентів, з якими можна помінятися.",
            make_keyboard([[("Назад", f"vq:{session_id}")]])
        )
        return

    rows = [[(f"{e['position']}. {e['full_name']}", f"sw_ask:{session_id}:{e['id']}")] for e in candidates]
    rows.append([("Назад", f"vq:{session_id}")])
    await show(callback, "Оберіть студента, якому запропонувати обмін місцями:", make_keyboard(rows))


@router.callback_query(F.data.startswith("sw_ask:"))
async def swap_confirm_prompt(callback: CallbackQuery, conn, subjects):
    _, session_id, target_entry_id = callback.data.split(":")
    session_id, target_entry_id = int(session_id), int(target_entry_id)

    my_pos = queues.get_user_position(conn, callback.from_user.id, session_id)
    if my_pos is None:
        raise BotError("Ви не записані в цю чергу.")

    target = queues.get_entry(conn, target_entry_id)
    if not target or target["status"] != "active":
        raise BotError("Цього студента вже немає в активній черзі.")

    target_user = conn.execute("SELECT full_name FROM users WHERE id = ?", (target["user_id"],)).fetchone()
    target_name = target_user["full_name"] if target_user else "Студент"

    text = (
        f"🔄 Запропонувати обмін місцями?\n\n"
        f"👤 Студент: **{target_name}** (місце {target['position']})\n"
        f"Ваше поточне місце: **{my_pos}**\n\n"
        f"Студенту надійде сповіщення в боті. Якщо він погодиться — ваші місця автоматично поміняються."
    )
    rows = [
        [("Так, надіслати запит", f"sw_send:{session_id}:{target_entry_id}")],
        [("Скасувати", f"vq:{session_id}")],
    ]
    await show(callback, text, make_keyboard(rows))


@router.callback_query(F.data.startswith("sw_send:"))
async def swap_send_request(callback: CallbackQuery, conn, subjects):
    _, session_id, target_entry_id = callback.data.split(":")
    session_id, target_entry_id = int(session_id), int(target_entry_id)

    session = get_session_or_fail(conn, session_id)
    my_entry = queues._own_active_entry(conn, callback.from_user.id, session_id)
    target = queues.get_entry(conn, target_entry_id)
    if not target or target["status"] != "active":
        raise BotError("Цього студента вже немає в активній черзі.")

    target_user = conn.execute("SELECT * FROM users WHERE id = ?", (target["user_id"],)).fetchone()
    if not target_user or not target_user["telegram_id"] or target_user["telegram_id"] <= 0:
        raise BotError("Цей студент ще не підключив свій Telegram.")

    sender_user = users.get_user(conn, callback.from_user.id)
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
        await callback.bot.send_message(target_user["telegram_id"], invite_text, reply_markup=invite_keyboard)
    except Exception:
        raise BotError("Не вдалося надіслати запит студенту (можливо, він заблокував бота).")

    await show(
        callback,
        f"✅ Запит надіслано студенту {target_user['full_name']}!\nЯк тільки він відповість, ви отримаєте сповіщення.",
        make_keyboard([[("Повернутися до черги", f"vq:{session_id}")]])
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
        raise BotError("Студент, який надсилав запит, уже вийшов з черги або змінив позицію.")

    session = get_session_or_fail(conn, target_entry["session_id"])
    subject, teacher = session_names(subjects, session)
    sender_user = conn.execute("SELECT * FROM users WHERE id = ?", (sender_entry["user_id"],)).fetchone()

    # Виконуємо обмін у базі
    new_sender_pos, new_target_pos = queues.swap_entries(conn, sender_entry_id, target_entry_id)

    # Оновлюємо екран для студента, що погодився
    await show(
        callback,
        f"🎉 Ви успішно обмінялися місцями з **{sender_user['full_name']}**!\n"
        f"📚 {subject} — {teacher}\n"
        f"Ваше нове місце: **{new_target_pos}**",
        make_keyboard([[("Переглянути чергу", f"vq:{session['id']}")], BACK_TO_MENU])
    )

    # Надсилаємо сповіщення студенту, який пропонував обмін
    if sender_user and sender_user["telegram_id"] and sender_user["telegram_id"] > 0:
        with suppress(Exception):
            await callback.bot.send_message(
                sender_user["telegram_id"],
                f"🎉 Студент **{target_user['full_name']}** погодився на обмін місцями!\n"
                f"📚 {subject} — {teacher}\n"
                f"Ваше нове місце: **{new_sender_pos}**",
                reply_markup=make_keyboard([[("Переглянути чергу", f"vq:{session['id']}")], BACK_TO_MENU])
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


@router.callback_query(F.data.startswith("lv:"))
async def ask_leave(callback: CallbackQuery, conn):
    session_id = int(callback.data.split(":")[1])
    if queues.get_user_position(conn, callback.from_user.id, session_id) is None:
        raise BotError("Ви не записані в цю чергу.")
    await show(callback, "Покинути чергу? Ваше місце буде втрачено.", make_keyboard([
        [("Так, покинути", f"lvy:{session_id}")],
        [("Ні", f"vq:{session_id}")],
    ]))


@router.callback_query(F.data.startswith("lvy:"))
async def leave(callback: CallbackQuery, conn):
    session_id = int(callback.data.split(":")[1])
    queues.leave_queue(conn, callback.from_user.id, session_id)
    await show(callback, "Ви покинули чергу.", make_keyboard([BACK_TO_MENU]))


# ---------- Створення черги студентом ----------

@router.message(F.text == "➕ Відкрити чергу")
@router.callback_query(F.data == "nq")
async def new_queue_subject(event, subjects):
    rows = [[(s["name"], f"nqs:{s['id']}")] for s in subjects]
    if isinstance(event, Message):
        await event.answer("Оберіть предмет для нової черги:", reply_markup=make_keyboard(rows + [BACK_TO_MENU]))
    else:
        await show(event, "Оберіть предмет для нової черги:", make_keyboard(rows + [BACK_TO_MENU]))



@router.callback_query(F.data.startswith("nqs:"))
async def new_queue_teacher(callback: CallbackQuery, conn, subjects):
    subject = find_subject(subjects, int(callback.data.split(":")[1]))
    if subject is None:
        raise BotError("Предмет не знайдено.")
    if len(subject["teachers"]) == 1:
        await create_new_queue(callback, conn, subject["id"], subject["teachers"][0]["id"])
        return
    rows = [[(t["name"], f"nqt:{subject['id']}:{t['id']}")] for t in subject["teachers"]]
    await show(callback, f"{subject['name']}\nОберіть викладача:",
               make_keyboard(rows + [[("Назад", "nq")]]))


@router.callback_query(F.data.startswith("nqt:"))
async def new_queue_teacher_chosen(callback: CallbackQuery, conn):
    _, subject_id, teacher_id = callback.data.split(":")
    await create_new_queue(callback, conn, int(subject_id), int(teacher_id))


async def create_new_queue(callback, conn, subject_id: int, teacher_id: int):
    try:
        session_id = queues.create_queue(conn, callback.from_user.id, subject_id, teacher_id)
    except BotError as error:
        await show(callback, f"{error}\nЗапишіться в наявну чергу через «Стати в чергу».",
                   make_keyboard([BACK_TO_MENU]))
        return
    await show(callback, "Чергу відкрито! Тепер у неї можна записуватися.",
               make_keyboard([[("Стати в цю чергу", f"jt:{session_id}")], BACK_TO_MENU]))
