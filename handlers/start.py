from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from handlers.common import (
    get_reply_menu,
    make_keyboard,
    make_reply_keyboard,
    notify_admins_user_registered,
    show,
)
from services import admins, users
from services.errors import BotError

router = Router()
fallback_router = Router()  # підключається в bot.py останнім

HELP_MENU = make_reply_keyboard([
    ["📝 Реєстрація", "📋 Запис у чергу"],
    ["⚙️ Дії в черзі", "➕ Створення черги"],
    ["📅 Розклад та заміни", "🔙 Назад"],
])

HELP_SECTIONS = {
    "📝 Реєстрація": (
        "📝 <b>Реєстрація</b>\n\n"
        "• Введіть своє справжнє прізвище та ім'я при першому вході.\n"
        "• Бот автоматично закріпить за вами записи в чергах.\n"
        "• Для зміни або виправлення імені зверніться до старости."
    ),
    "📋 Запис у чергу": (
        "📋 <b>Запис у чергу</b>\n\n"
        "• Відкрийте чергу через «📋 Усі черги».\n"
        "• Натисніть «⚙️ Дії» ➔ «📝 Стати в кінець» або «🔢 Обрати місце».\n"
        "• При виборі номера вільні місця перед вами залишаться іншим."
    ),
    "⚙️ Дії в черзі": (
        "⚙️ <b>Дії в черзі та обмін місцями</b>\n\n"
        "• <b>Здав</b> — зарахувати здачу роботи та вийти з черги.\n"
        "• <b>Не здав</b> — стати в кінець черги на перездачу.\n"
        "• <b>⬆️ Хочу вище / ⬇️ Хочу нижче</b> — встановити статус готовності здавати раніше або пропустити когось уперед. Бот автоматично повідомить одногрупників про збіг інтересів та запропонує швидкий обмін в один клік!\n"
        "• <b>🔁 Обмінятися місцями</b> — надіслати прямий запит на обмін позиціями обраному студенту.\n"
        "• <b>🚪 Покинути чергу</b> — звільнити місце та вийти з черги."
    ),
    "➕ Створення черги": (
        "➕ <b>Створення черги</b>\n\n"
        "• Натисніть «➕ Відкрити чергу» в головному меню.\n"
        "• Оберіть тип черги: «📖 Звичайна пара» або «💬 Консультація».\n"
        "• Оберіть предмет та викладача зі списку.\n"
        "• Черга відкриється для запису всій групі."
    ),
    "📅 Розклад та заміни": (
        "📅 <b>Розклад занять та заміни</b>\n\n"
        "• <b>Кнопки в меню:</b> «📅 Розклад» та «🔄 Заміни» показують пари на сьогодні, завтра або тиждень з урахуванням підгруп і чисельника/знаменника.\n"
        "• <b>Ранковий розклад:</b> щодня о 07:00 бот автоматично надсилає актуальний розклад на день усім зареєстрованим студентам.\n"
        "• <b>Миттєві сповіщення:</b> щойно староста/адмін зберігає нові заміни, усім студентам одразу приходить сповіщення з переліком пар.\n"
        "• <b>Швидкі команди:</b> /today, /tomorrow, /week, /schedule, /replacements."
    ),
    "🗳 Опитування": (
        "🗳 <b>Опитування групи</b>\n\n"
        "• Натисніть «🗳 Опитування» у головному меню, щоб переглянути всі активні або нещодавно завершені опитування.\n"
        "• Натисніть «🗳 Надіслати мені опитування», якщо ви пропустили повідомлення з бюлетенем.\n"
        "• Опитування можуть бути анонімними або відкритими, з одним чи кількома варіантами відповіді та таймером завершення."
    ),
}


class NameForm(StatesGroup):
    registration = State()
    rename = State()
    browsing_help = State()


def main_menu(conn, telegram_id: int):
    rows = [
        [("Усі черги", "all_q")],
        [("Відкрити чергу", "nq")],
        [("Мої черги", "my")],
        [("Допомога", "help")],
    ]
    if admins.is_admin(conn, telegram_id):
        rows.append([("Адмін-панель", "admin")])
    return make_keyboard(rows)


@router.message(Command("start"), StateFilter("*"))
async def start(message: Message, state: FSMContext, conn):
    await state.clear()
    from services.maintenance import get_maintenance_end_time, is_maintenance_active
    if is_maintenance_active(conn):
        end_t = get_maintenance_end_time(conn)
        await message.answer(
            f"🛠 <b>Увага: увімкнено режим технічних робіт (до {end_t})!</b>\n"
            f"Для студентів доступ та кнопки тимчасово деактивовано.\n"
            f"Ви маєте повний доступ як адміністратор.",
            reply_markup=get_reply_menu(conn, message.from_user.id),
        )
        return
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
        "✅ Реєстрацію завершено!\n\nГоловне меню. Оберіть дію на клавіатурі внизу 👇",
        reply_markup=get_reply_menu(conn, message.from_user.id),
    )


@router.message(F.text.in_({"🏠 Головне меню", "🏠 Головне меню (Як студент)", "Головне меню"}), StateFilter("*"))
async def reply_main_menu(message: Message, state: FSMContext, conn):
    await state.clear()
    if not users.get_user(conn, message.from_user.id):
        raise BotError("Спочатку зареєструйтесь: /start")
    await message.answer(
        "Головне меню. Оберіть дію на клавіатурі внизу 👇",
        reply_markup=get_reply_menu(conn, message.from_user.id),
    )


@router.message(F.text == "❓ Допомога", StateFilter("*"))
async def reply_help(message: Message, state: FSMContext):
    await state.set_state(NameForm.browsing_help)
    await message.answer(
        "❓ <b>Довідка</b>\n\nОберіть розділ, щоб переглянути інструкцію 👇",
        reply_markup=HELP_MENU,
    )


@router.message(NameForm.browsing_help, F.text.in_(HELP_SECTIONS.keys()))
async def reply_help_topic(message: Message):
    text = HELP_SECTIONS.get(message.text, "Оберіть розділ на кнопках нижче:")
    await message.answer(text, reply_markup=HELP_MENU)


@router.message(NameForm.browsing_help, F.text == "🔙 Назад")
async def reply_help_back(message: Message, state: FSMContext, conn):
    await state.clear()
    await message.answer(
        "Головне меню. Оберіть дію на клавіатурі внизу 👇",
        reply_markup=get_reply_menu(conn, message.from_user.id),
    )


@router.callback_query(F.data == "menu")
async def open_menu(callback: CallbackQuery, state: FSMContext, conn):
    await state.clear()
    if not users.get_user(conn, callback.from_user.id):
        raise BotError("Спочатку зареєструйтесь: /start")
    await show(callback, "Головне меню:", main_menu(conn, callback.from_user.id))


@router.callback_query(F.data == "help")
async def help_screen(callback: CallbackQuery):
    await show(
        callback,
        "❓ <b>Довідка</b>\n\nСкористайтеся кнопкою «❓ Допомога» на нижній панелі для перегляду розділів.",
        make_keyboard([[("Назад", "menu")]]),
    )


@fallback_router.callback_query(StateFilter("*"))
async def stale_button(callback: CallbackQuery):
    """Спрацьовує останнім: кнопка не належить жодному обробнику (застаріла)."""
    await show(callback, "Ця кнопка застаріла. Натисніть /start, щоб відкрити меню.")
