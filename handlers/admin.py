from aiogram import BaseMiddleware, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from handlers.common import (format_entry, format_queue, make_keyboard, queue_label,
                             session_names, show)
from services import admins, queues, users
from services.errors import BotError, PermissionDenied


router = Router()

BACK_TO_PANEL = [("Адмін-панель", "admin")]
MAX_POSITION = 60

PICK_TITLES = {
    "view": "Активні черги",
    "add_student": "Додати студента в чергу",
    "order": "Змінити порядок",
    "done": "Позначити здачу",
    "transfer": "Перенести студентів",
    "close": "Закрити/відкрити запис",
    "finish": "Завершити заняття",
}



class AdminOnly(BaseMiddleware):
    """Перша лінія захисту. Сервіси додатково перевіряють роль самі."""

    async def __call__(self, handler, event, data):
        if not admins.is_admin(data["conn"], event.from_user.id):
            raise PermissionDenied("У вас немає прав адміністратора.")
        return await handler(event, data)


router.callback_query.middleware(AdminOnly())
router.message.middleware(AdminOnly())


class MoveTo(StatesGroup):
    position = State()


class Carry(StatesGroup):
    selecting = State()
    positions = State()


class AddAdmin(StatesGroup):
    telegram_id = State()


class AdminAddStudent(StatesGroup):
    names = State()



def arg(callback: CallbackQuery, index: int = 1) -> str:
    return callback.data.split(":")[index]


def sessions_for_admin(conn):
    return queues.list_sessions(conn, ("open", "closed"))


# ---------- Панель ----------

@router.callback_query(F.data == "admin")
async def panel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(callback, "Адмін-панель:", panel_keyboard())


def panel_keyboard():
    return make_keyboard([
        [("Створити чергу", "nq")],
        [("Додати студента в чергу", "pick:add_student")],
        [("Активні черги", "pick:view")],
        [("Змінити порядок", "pick:order")],
        [("Позначити здачу", "pick:done")],
        [("Перенести студентів", "pick:transfer")],
        [("Закрити запис", "pick:close")],
        [("Завершити заняття", "pick:finish")],
        [("Історія черг", "ah")],
        [("Керування адміністраторами", "adm")],
        [("Головне меню", "menu")],
    ])



@router.callback_query(F.data.startswith("pick:"))
async def pick_queue(callback: CallbackQuery, conn, subjects):
    mode = arg(callback)
    sessions = sessions_for_admin(conn)
    if not sessions:
        await show(callback, "Незавершених черг немає.", make_keyboard([BACK_TO_PANEL]))
        return
    rows = [[(queue_label(subjects, s), f"go:{mode}:{s['id']}")] for s in sessions]
    await show(callback, f"{PICK_TITLES[mode]}\nОберіть чергу:", make_keyboard(rows + [BACK_TO_PANEL]))


@router.callback_query(F.data.startswith("go:"))
async def go_mode(callback: CallbackQuery, conn, subjects, state: FSMContext):
    mode, session_id = arg(callback), int(arg(callback, 2))
    if mode == "view":
        await show_manager(callback, conn, subjects, session_id)
    elif mode == "add_student":
        await start_admin_add_student(callback, conn, subjects, state, session_id)
    elif mode in ("order", "done"):
        await show_student_list(callback, conn, subjects, session_id)
    elif mode == "close":
        await ask_toggle_signup(callback, conn, subjects, session_id)
    elif mode == "finish":
        await show_finish_menu(callback, conn, subjects, session_id)
    elif mode == "transfer":
        await start_carry(callback, conn, subjects, state, session_id)


def get_open_session(conn, session_id: int):
    session = queues.get_session(conn, session_id)
    if session is None or session["status"] == "archived":
        raise BotError("Ця черга вже завершена або не існує.")
    return session


async def show_manager(callback, conn, subjects, session_id: int):
    session = get_open_session(conn, session_id)
    text = format_queue(subjects, session, queues.get_entries(conn, session_id))
    rows = [
        [("Додати студентів у чергу", f"go:add_student:{session_id}")],
        [("Змінити порядок", f"go:order:{session_id}")],
        [("Закрити/відкрити запис", f"go:close:{session_id}")],
        [("Завершити заняття", f"go:finish:{session_id}")],
        [("Назад", "pick:view")],
    ]
    await show(callback, text, make_keyboard(rows))


async def start_admin_add_student(callback: CallbackQuery, conn, subjects, state: FSMContext, session_id: int):
    session = get_open_session(conn, session_id)
    await state.set_state(AdminAddStudent.names)
    await state.update_data(session_id=session_id)
    text = (
        f"📝 **Додавання студентів у чергу:**\n"
        f"{queue_label(subjects, session)}\n\n"
        "Надішліть одне прізвище та ім'я або **список студентів** (кожне з нового рядка).\n"
        "Можна навіть з номерами (наприклад `1. Петренко Іван` або просто `Петренко Іван`).\n\n"
        "Вони будуть додані послідовно у чергу."
    )
    await show(callback, text, make_keyboard([[("Скасувати", "admin")]]))


@router.message(AdminAddStudent.names, F.text)
async def receive_admin_student_names(message: Message, state: FSMContext, conn, subjects):
    data = await state.get_data()
    session_id = data["session_id"]
    lines = [line.strip() for line in message.text.split("\n") if line.strip()]
    if not lines:
        await message.answer("Введіть хоча б одне прізвище та ім'я:")
        return

    added = []
    skipped = []

    for raw in lines:
        # Прибираємо порядковий номер на початку, якщо він є ("1. Петренко" -> "Петренко")
        cleaned = raw.lstrip("0123456789. -) ")
        try:
            user = users.get_or_create_user_by_name(conn, cleaned)
            pos = queues.add_student_by_admin(conn, message.from_user.id, session_id, user["id"])
            added.append(f"{pos}. {user['full_name']}")
        except Exception as e:
            skipped.append(f"{raw} ({e})")

    await state.clear()
    session = get_open_session(conn, session_id)
    result_text = f"✅ Додано студентів: {len(added)}\n"
    if added:
        result_text += "\n".join(added[:15]) + ("\n..." if len(added) > 15 else "")
    if skipped:
        result_text += "\n\n⚠️ Пропущено (помилка або вже в черзі):\n" + "\n".join(skipped[:10])

    await message.answer(
        result_text,
        reply_markup=make_keyboard([
            [("Переглянути чергу", f"go:view:{session_id}")],
            [("Додати ще", f"go:add_student:{session_id}")],
            BACK_TO_PANEL,
        ])
    )



# ---------- Відкриття / закриття запису ----------

async def ask_toggle_signup(callback, conn, subjects, session_id: int):
    session = get_open_session(conn, session_id)
    make_open = session["status"] == "closed"
    action = "Відкрити" if make_open else "Закрити"
    await show(callback, f"{action} запис до черги?\n{queue_label(subjects, session)}", make_keyboard([
        [(f"Так, {action.lower()}", f"sgy:{session_id}:{int(make_open)}")],
        [("Скасувати", "admin")],
    ]))


@router.callback_query(F.data.startswith("sgy:"))
async def toggle_signup(callback: CallbackQuery, conn, subjects):
    session_id, make_open = int(arg(callback)), arg(callback, 2) == "1"
    queues.set_signup_open(conn, callback.from_user.id, session_id, make_open)
    await show_manager(callback, conn, subjects, session_id)


# ---------- Зміна порядку ----------

async def show_student_list(callback, conn, subjects, session_id: int):
    session = get_open_session(conn, session_id)
    entries = queues.get_entries(conn, session_id)
    rows = [[(format_entry(e), f"ae:{e['id']}")] for e in entries]
    text = format_queue(subjects, session, entries) + "\n\nОберіть студента:"
    await show(callback, text, make_keyboard(rows + [[("Назад", "pick:order")]]))


def entry_screen(conn, subjects, entry_id: int):
    entry = queues.get_entry(conn, entry_id)
    if entry is None or entry["status"] != "active":
        raise BotError("Запис не знайдено або він уже неактивний.")
    session = get_open_session(conn, entry["session_id"])
    entries = queues.get_entries(conn, session["id"])
    lines = [format_entry(e, marked=e["id"] == entry_id) for e in entries]
    subject, teacher = session_names(subjects, session)
    text = f"{subject}, {teacher}\n\n" + "\n".join(lines) + "\n\nОберіть дію:"
    rows = [
        [("Перемістити вище", f"au:{entry_id}"), ("Перемістити нижче", f"ad:{entry_id}")],
        [("Перемістити на місце…", f"ag:{entry_id}")],
        [("Позначити «Здав»", f"ax:{entry_id}")],
        [("Видалити з черги", f"ar:{entry_id}")],
        [("До списку", f"go:order:{session['id']}")],
    ]
    return text, make_keyboard(rows)


@router.callback_query(F.data.startswith("ae:"))
async def open_entry(callback: CallbackQuery, conn, subjects):
    await show(callback, *entry_screen(conn, subjects, int(arg(callback))))


@router.callback_query(F.data.startswith("au:"))
async def entry_up(callback: CallbackQuery, conn, subjects):
    entry_id = int(arg(callback))
    queues.move_up(conn, callback.from_user.id, entry_id)
    await show(callback, *entry_screen(conn, subjects, entry_id))


@router.callback_query(F.data.startswith("ad:"))
async def entry_down(callback: CallbackQuery, conn, subjects):
    entry_id = int(arg(callback))
    queues.move_down(conn, callback.from_user.id, entry_id)
    await show(callback, *entry_screen(conn, subjects, entry_id))


@router.callback_query(F.data.startswith("ag:"))
async def ask_position(callback: CallbackQuery, state: FSMContext, conn):
    entry_id = int(arg(callback))
    entry = queues.get_entry(conn, entry_id)
    if entry is None or entry["status"] != "active":
        raise BotError("Запис не знайдено або він уже неактивний.")
    await state.set_state(MoveTo.position)
    await state.update_data(entry_id=entry_id)
    await show(callback, f"Введіть нову позицію для студента (1–100):\n"
               f"Якщо вказати номер більший за поточний список — перед ним утворяться вільні місця.",
               make_keyboard([[("Скасувати", f"ae:{entry_id}")]]))



@router.message(MoveTo.position, F.text)
async def move_to_position(message: Message, state: FSMContext, conn, subjects):
    if not message.text.strip().isdigit():
        await message.answer("Введіть число, наприклад 3:")
        return
    entry_id = (await state.get_data())["entry_id"]
    try:
        queues.move_entry(conn, message.from_user.id, entry_id, int(message.text))
    except BotError as error:
        await message.answer(f"{error}\nСпробуйте ще раз:")
        return
    await state.clear()
    text, markup = entry_screen(conn, subjects, entry_id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ax:"))
async def ask_done(callback: CallbackQuery, conn):
    entry_id = int(arg(callback))
    await show(callback, "Позначити студента як такого, що здав роботу? Він зникне з активної черги.",
               make_keyboard([[("Так, здав", f"axy:{entry_id}")], [("Скасувати", f"ae:{entry_id}")]]))


@router.callback_query(F.data.startswith("axy:"))
async def confirm_done(callback: CallbackQuery, conn, subjects):
    entry_id = int(arg(callback))
    session_id = queues.get_entry(conn, entry_id)["session_id"]
    queues.mark_done(conn, callback.from_user.id, entry_id)
    await show_student_list(callback, conn, subjects, session_id)


@router.callback_query(F.data.startswith("ar:"))
async def ask_remove(callback: CallbackQuery):
    entry_id = int(arg(callback))
    await show(callback, "Видалити запис з черги? Дію не можна скасувати.",
               make_keyboard([[("Так, видалити", f"ary:{entry_id}")], [("Скасувати", f"ae:{entry_id}")]]))


@router.callback_query(F.data.startswith("ary:"))
async def confirm_remove(callback: CallbackQuery, conn, subjects):
    entry_id = int(arg(callback))
    session_id = queues.get_entry(conn, entry_id)["session_id"]
    queues.remove_entry(conn, callback.from_user.id, entry_id)
    await show_student_list(callback, conn, subjects, session_id)


# ---------- Завершення заняття ----------

async def show_finish_menu(callback, conn, subjects, session_id: int):
    session = get_open_session(conn, session_id)
    await show(callback, f"Завершити заняття?\n{queue_label(subjects, session)}", make_keyboard([
        [("Завершити й архівувати", f"fx:a:{session_id}")],
        [("Створити нову чергу", f"fx:n:{session_id}")],
        [("Створити нову чергу з перенесенням студентів", f"fc:{session_id}")],
        [("Скасувати", "admin")],
    ]))


@router.callback_query(F.data.startswith("fx:"))
async def ask_finish(callback: CallbackQuery, conn):
    mode, session_id = arg(callback), int(arg(callback, 2))
    get_open_session(conn, session_id)
    text = ("Архівувати чергу? Усі, хто не здав, залишаться в історії без перенесення."
            if mode == "a" else
            "Архівувати чергу й створити нову порожню чергу?")
    await show(callback, text, make_keyboard([
        [("Підтвердити", f"fxy:{mode}:{session_id}")], [("Скасувати", "admin")],
    ]))


@router.callback_query(F.data.startswith("fxy:"))
async def confirm_finish(callback: CallbackQuery, conn):
    mode, session_id = arg(callback), int(arg(callback, 2))
    new_id = queues.finish_session(conn, callback.from_user.id, session_id, create_new=mode == "n")
    text = "Чергу архівовано." if new_id is None else f"Чергу архівовано. Нову чергу створено (№{new_id})."
    await show(callback, text, make_keyboard([BACK_TO_PANEL]))


# ---------- Перенесення студентів ----------

@router.callback_query(F.data.startswith("fc:"))
async def start_carry_from_menu(callback: CallbackQuery, conn, subjects, state: FSMContext):
    await start_carry(callback, conn, subjects, state, int(arg(callback)))


async def start_carry(callback, conn, subjects, state: FSMContext, session_id: int):
    get_open_session(conn, session_id)
    await state.set_state(Carry.selecting)
    await state.update_data(session_id=session_id, selected=[], assigned={})
    await show_carry_selection(callback, conn, state)


async def show_carry_selection(callback, conn, state: FSMContext):
    data = await state.get_data()
    entries = [e for e in queues.get_entries(conn, data["session_id"]) if e["user_id"]]
    rows = [
        [(("✅ " if e["id"] in data["selected"] else "") + format_entry(e), f"cs:{e['id']}")]
        for e in entries
    ]
    rows += [[("Далі", "cn")], [("Скасувати", "admin")]]
    await show(callback, "Оберіть студентів, яких потрібно перенести на наступне заняття:",
               make_keyboard(rows))


@router.callback_query(Carry.selecting, F.data.startswith("cs:"))
async def toggle_carry(callback: CallbackQuery, conn, state: FSMContext):
    entry_id = int(arg(callback))
    selected = (await state.get_data())["selected"]
    selected = [i for i in selected if i != entry_id] if entry_id in selected else selected + [entry_id]
    await state.update_data(selected=selected)
    await show_carry_selection(callback, conn, state)


@router.callback_query(Carry.selecting, F.data == "cn")
async def carry_next(callback: CallbackQuery, conn, state: FSMContext):
    if not (await state.get_data())["selected"]:
        raise BotError("Оберіть хоча б одного студента.")
    await state.set_state(Carry.positions)
    await ask_next_position(callback.bot, callback.from_user.id, conn, state)


def student_name(conn, session_id: int, entry_id: int) -> str:
    entries = queues.get_entries(conn, session_id)
    return next(e["full_name"] for e in entries if e["id"] == entry_id)


async def ask_next_position(bot, chat_id: int, conn, state: FSMContext):
    data = await state.get_data()
    pending = [i for i in data["selected"] if str(i) not in data["assigned"]]
    if not pending:
        await show_carry_preview(bot, chat_id, conn, state)
        return
    name = student_name(conn, data["session_id"], pending[0])
    await bot.send_message(
        chat_id,
        f"Введіть позицію для «{name}» у новій черзі (1–{MAX_POSITION}).\n"
        "Вільні місця між перенесеними залишаться для нових студентів.",
        reply_markup=make_keyboard([[("Скасувати", "admin")]]),
    )


@router.message(Carry.positions, F.text)
async def receive_position(message: Message, conn, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    if not text.isdigit() or not 1 <= int(text) <= MAX_POSITION:
        await message.answer(f"Введіть число від 1 до {MAX_POSITION}:")
        return
    if int(text) in data["assigned"].values():
        await message.answer("Ця позиція вже зайнята іншим перенесеним студентом. Оберіть іншу:")
        return
    pending = [i for i in data["selected"] if str(i) not in data["assigned"]]
    data["assigned"][str(pending[0])] = int(text)  # ключі JSON-сховища — рядки
    await state.update_data(assigned=data["assigned"])
    await ask_next_position(message.bot, message.chat.id, conn, state)


async def show_carry_preview(bot, chat_id: int, conn, state: FSMContext):
    data = await state.get_data()
    by_position = {pos: int(eid) for eid, pos in data["assigned"].items()}
    lines = []
    for position in range(1, max(by_position) + 1):
        entry_id = by_position.get(position)
        name = student_name(conn, data["session_id"], entry_id) if entry_id else "Новий студент"
        lines.append(f"{position}. {name}")
    await bot.send_message(
        chat_id,
        "Нова черга буде такою:\n" + "\n".join(lines) + "\n\nАрхівувати стару чергу й створити нову?",
        reply_markup=make_keyboard([[("Підтвердити", "cy")], [("Скасувати", "admin")]]),
    )


@router.callback_query(Carry.positions, F.data == "cy")
async def confirm_carry(callback: CallbackQuery, conn, state: FSMContext):
    data = await state.get_data()
    carry = {int(eid): pos for eid, pos in data["assigned"].items()}
    new_id = queues.finish_session(
        conn, callback.from_user.id, data["session_id"], create_new=True, carry=carry
    )
    await state.clear()
    await show(callback, f"Готово. Стару чергу архівовано, нову створено (№{new_id}).",
               make_keyboard([[("Відкрити нову чергу", f"go:view:{new_id}")], BACK_TO_PANEL]))


# ---------- Історія ----------

@router.callback_query(F.data == "ah")
async def history_list(callback: CallbackQuery, conn, subjects):
    sessions = queues.list_sessions(conn, ("archived",))[:15]
    if not sessions:
        await show(callback, "Історія порожня.", make_keyboard([BACK_TO_PANEL]))
        return
    rows = [[(f"№{s['id']} {queue_label(subjects, s)} ({s['closed_at'][:10]})", f"ahs:{s['id']}")]
            for s in sessions]
    await show(callback, "Історія черг:", make_keyboard(rows + [BACK_TO_PANEL]))


HISTORY_MARK = {"completed": "✅ здав", "transferred": "↪ перенесено", "removed": "✖ не здав / видалено",
                "left": "вийшов із черги"}


@router.callback_query(F.data.startswith("ahs:"))
async def history_details(callback: CallbackQuery, conn, subjects):
    session = queues.get_session(conn, int(arg(callback)))
    if session is None:
        raise BotError("Чергу не знайдено.")
    lines = [f"{r['full_name']} — {HISTORY_MARK[r['status']]}" for r in queues.get_history(conn, session["id"])]
    header = format_queue(subjects, session, [])
    body = "\n".join(lines) or "Записів немає."
    await show(callback, header.replace("Черга порожня.", "") + body, make_keyboard([[("Назад", "ah")]]))


# ---------- Керування адміністраторами ----------

@router.callback_query(F.data == "adm")
async def admins_menu(callback: CallbackQuery):
    await show(callback, "Керування адміністраторами:", make_keyboard([
        [("Список адміністраторів", "adl")],
        [("Додати адміністратора", "ada")],
        [("Видалити адміністратора", "adr")],
        [BACK_TO_PANEL[0]],
    ]))


def admin_label(row) -> str:
    return f"{row['full_name'] or 'без реєстрації'} ({row['telegram_id']})"


@router.callback_query(F.data == "adl")
async def admins_list(callback: CallbackQuery, conn):
    text = "Адміністратори:\n" + "\n".join(admin_label(r) for r in admins.list_admins(conn))
    await show(callback, text, make_keyboard([[("Назад", "adm")]]))


@router.callback_query(F.data == "ada")
async def ask_admin_id(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddAdmin.telegram_id)
    await show(callback, "Введіть числовий Telegram ID нового адміністратора:",
               make_keyboard([[("Скасувати", "adm")]]))


@router.message(AddAdmin.telegram_id, F.text)
async def receive_admin_id(message: Message, state: FSMContext, conn):
    text = message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Telegram ID — це число. Спробуйте ще раз:")
        return
    if admins.is_admin(conn, int(text)):
        await message.answer("Цей користувач уже є адміністратором. Введіть інший ID:")
        return
    await state.clear()
    await message.answer(f"Додати адміністратора з ID {text}?", reply_markup=make_keyboard([
        [("Підтвердити", f"ady:{text}")], [("Скасувати", "adm")],
    ]))


@router.callback_query(F.data.startswith("ady:"))
async def confirm_add_admin(callback: CallbackQuery, conn):
    admins.add_admin(conn, callback.from_user.id, int(arg(callback)))
    await show(callback, "Адміністратора додано.", make_keyboard([[("Назад", "adm")]]))


@router.callback_query(F.data == "adr")
async def choose_admin_to_remove(callback: CallbackQuery, conn):
    rows = [[(admin_label(r), f"adx:{r['telegram_id']}")] for r in admins.list_admins(conn)]
    await show(callback, "Кого видалити з адміністраторів?", make_keyboard(rows + [[("Назад", "adm")]]))


@router.callback_query(F.data.startswith("adx:"))
async def ask_remove_admin(callback: CallbackQuery):
    target = int(arg(callback))
    await show(callback, f"Забрати права адміністратора в {target}?", make_keyboard([
        [("Так, видалити", f"adxy:{target}")], [("Скасувати", "adm")],
    ]))


@router.callback_query(F.data.startswith("adxy:"))
async def confirm_remove_admin(callback: CallbackQuery, conn):
    admins.remove_admin(conn, callback.from_user.id, int(arg(callback)))
    await show(callback, "Права адміністратора видалено.", make_keyboard([[("Назад", "adm")]]))
