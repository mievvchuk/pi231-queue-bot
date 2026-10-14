from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from handlers.common import format_queue, make_keyboard, queue_label, session_names, show
from services import queues
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
    text = format_queue(subjects, session, queues.get_entries(conn, session_id), callback.from_user.id)
    if note:
        text = f"{note}\n\n{text}"
    rows = [
        [("Оновити список", f"vq:{session_id}"), ("Моє місце", f"mp:{session_id}")],
        [("Здав", f"sd:{session_id}"), ("Не здав", f"sn:{session_id}")],
        [("Покинути чергу", f"lv:{session_id}")],
        [("Назад", "view")],
    ]
    await show(callback, text, make_keyboard(rows))


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
