from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from handlers.common import make_keyboard, show
from services import admins, users
from services.errors import BotError

router = Router()
fallback_router = Router()  # підключається в bot.py останнім

HELP_TEXT = (
    "Бот електронної черги ПІ-231.\n\n"
    "• Стати в чергу — оберіть предмет, викладача й підтвердіть запис.\n"
    "• Мої черги — черги, де ви записані.\n"
    "• Переглянути черги — списки учасників, ваше місце, вихід із черги.\n"
    "• Змінити ПІБ — виправити прізвище та ім'я.\n\n"
    "Чергами керують старости. Якщо запис закрито, зверніться до старости."
)


class NameForm(StatesGroup):
    registration = State()
    rename = State()


def main_menu(conn, telegram_id: int):
    rows = [
        [("Стати в чергу", "join")],
        [("Відкрити нову чергу", "nq")],
        [("Мої черги", "my")],
        [("Переглянути черги", "view")],
        [("Змінити ПІБ", "rename")],
        [("Допомога", "help")],
    ]
    if admins.is_admin(conn, telegram_id):
        rows.append([("Адмін-панель", "admin")])
    return make_keyboard(rows)


@router.message(Command("start"))
async def start(message: Message, state: FSMContext, conn):
    await state.clear()
    if users.get_user(conn, message.from_user.id):
        await message.answer("Головне меню:", reply_markup=main_menu(conn, message.from_user.id))
        return
    await message.answer("Вітаємо в боті електронної черги ПІ-231!\nВведіть своє прізвище та ім'я:")
    await state.set_state(NameForm.registration)


@router.message(NameForm.registration, F.text)
async def register(message: Message, state: FSMContext, conn):
    try:
        users.register_user(conn, message.from_user.id, message.text)
    except BotError as error:
        await message.answer(f"{error}\nСпробуйте ще раз:")
        return
    await state.clear()
    await message.answer(
        "Реєстрацію завершено!\nГоловне меню:", reply_markup=main_menu(conn, message.from_user.id)
    )


@router.callback_query(F.data == "rename")
async def ask_rename(callback: CallbackQuery, state: FSMContext):
    await state.set_state(NameForm.rename)
    await show(callback, "Введіть нове прізвище та ім'я:", make_keyboard([[("Скасувати", "menu")]]))


@router.message(NameForm.rename, F.text)
async def rename(message: Message, state: FSMContext, conn):
    try:
        user = users.rename_user(conn, message.from_user.id, message.text)
    except BotError as error:
        await message.answer(f"{error}\nСпробуйте ще раз:")
        return
    await state.clear()
    await message.answer(
        f"ПІБ змінено: {user['full_name']}", reply_markup=main_menu(conn, message.from_user.id)
    )


@router.callback_query(F.data == "menu")
async def open_menu(callback: CallbackQuery, state: FSMContext, conn):
    await state.clear()
    if not users.get_user(conn, callback.from_user.id):
        raise BotError("Спочатку зареєструйтесь: /start")
    await show(callback, "Головне меню:", main_menu(conn, callback.from_user.id))


@router.callback_query(F.data == "help")
async def help_screen(callback: CallbackQuery):
    await show(callback, HELP_TEXT, make_keyboard([[("Назад", "menu")]]))


@fallback_router.callback_query(StateFilter("*"))
async def stale_button(callback: CallbackQuery):
    """Спрацьовує останнім: кнопка не належить жодному обробнику (застаріла)."""
    await show(callback, "Ця кнопка застаріла. Натисніть /start, щоб відкрити меню.")
