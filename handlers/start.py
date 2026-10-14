from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from handlers.common import (
    get_reply_menu,
    make_keyboard,
    notify_admins_user_registered,
    show,
)
from services import admins, users
from services.errors import BotError

router = Router()
fallback_router = Router()  # підключається в bot.py останнім

HELP_TEXT = (
    "📖 *Інструкція користування ботом черг ПІ-231*\n\n"
    "Усі дії виконуються кнопками на *нижній постійній панелі* під полем вводу.\n\n"
    "1️⃣ *Реєстрація та вхід:*\n"
    "• При першому запуску (`/start`) введіть своє справжнє прізвище та ім'я.\n"
    "• Якщо староста вже вносив вас до черги вручну, бот автоматично прив'яже ваш Telegram-акаунт до ваших місць.\n\n"
    "2️⃣ *Як стати в чергу (📋 Стати в чергу):*\n"
    "• Натисніть кнопку, оберіть потрібну чергу зі списку внизу.\n"
    "• Оберіть спосіб запису:\n"
    "   — *➡️ В кінець черги* — займає найближчий вільний номер у кінці.\n"
    "   — *🔢 Обрати номер* — дозволяє ввести конкретне число (наприклад, 3 чи 7). Місця перед вами залишаться вільними для інших.\n\n"
    "3️⃣ *Перегляд черг:*\n"
    "• *🔍 Переглянути черги* — повний перелік активних черг групи.\n"
    "• *👥 Мої черги* — черги, у яких ви зараз записані.\n\n"
    "4️⃣ *Дії всередині черги:*\n"
    "• *📍 Моє місце* — показує вашу поточну позицію та загальну кількість студентів.\n"
    "• *🔄 Помінятися місцем* — обмін місцями з одногрупником. Оберіть студента на кнопках і підтвердіть. Йому надійде сповіщення: якщо він погодиться — ваші місця автоматично поміняються.\n"
    "• *✅ Здав* — позначити успішну здачу роботи (вас буде знято з черги).\n"
    "• *❌ Не здав* — переміститися в кінець черги для повторної спроби.\n"
    "• *🚪 Покинути чергу* — добровільно вийти з черги зі звільненням місця.\n"
    "• *🔄 Оновити* — оновити список черги.\n\n"
    "5️⃣ *➕ Відкрити чергу:*\n"
    "• Створення нової черги для пари (вибір предмета та викладача).\n\n"
    "6️⃣ *🔒 Статус запису:*\n"
    "• Позначка 🔒 означає, що староста закрив самостійний запис на час заняття. Для додавання або змін звертайтеся до старости.\n\n"
    "Повернутися в початкове меню завжди можна кнопкою *🏠 Головне меню*."
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
        [("Допомога", "help")],
    ]
    if admins.is_admin(conn, telegram_id):
        rows.append([("Адмін-панель", "admin")])
    return make_keyboard(rows)



@router.message(Command("start"), StateFilter("*"))
async def start(message: Message, state: FSMContext, conn):
    await state.clear()
    if users.get_user(conn, message.from_user.id):
        await message.answer(
            "Головне меню. Оберіть дію на клавіатурі внизу 👇",
            reply_markup=get_reply_menu(conn, message.from_user.id),
        )
        return
    await message.answer(
        "Вітаємо в боті електронної черги ПІ-231!\nВведіть своє прізвище та ім'я:",
        reply_markup=get_reply_menu(conn, message.from_user.id),
    )
    await state.set_state(NameForm.registration)


@router.message(NameForm.registration, F.text)
async def register(message: Message, state: FSMContext, conn):
    try:
        new_user = users.register_user(conn, message.from_user.id, message.text)
    except BotError as error:
        await message.answer(f"{error}\nСпробуйте ще раз:")
        return
    await state.clear()
    await notify_admins_user_registered(
        message.bot, conn, new_user, username=message.from_user.username
    )
    await message.answer(
        "✅ Реєстрацію завершено!\nГоловне меню. Оберіть дію на клавіатурі внизу 👇",
        reply_markup=get_reply_menu(conn, message.from_user.id),
    )


@router.message(F.text == "🏠 Головне меню", StateFilter("*"))
async def reply_main_menu(message: Message, state: FSMContext, conn):
    await state.clear()
    if not users.get_user(conn, message.from_user.id):
        raise BotError("Спочатку зареєструйтесь: /start")
    await message.answer(
        "Головне меню. Оберіть дію на клавіатурі внизу 👇",
        reply_markup=get_reply_menu(conn, message.from_user.id),
    )


@router.message(F.text == "❓ Допомога", StateFilter("*"))
async def reply_help(message: Message, state: FSMContext, conn):
    await state.clear()
    await message.answer(
        HELP_TEXT,
        reply_markup=get_reply_menu(conn, message.from_user.id),
        parse_mode="Markdown",
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
