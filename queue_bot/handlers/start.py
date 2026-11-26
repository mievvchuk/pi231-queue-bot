from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from queue_bot.handlers.common import (
    get_reply_menu,
    make_keyboard,
    make_reply_keyboard,
    notify_admins_user_registered,
    show,
)
from queue_bot.services import admins, users
from queue_bot.services.errors import BotError
from queue_bot.services.subgroups import PROMPT, subgroup_keyboard

router = Router()
router.message.filter(F.chat.type == "private")
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
        "• Відкрийте «Черги» ➔ «📋 Усі черги» та оберіть потрібну чергу.\n"
        "• Натисніть «Стати в чергу». Для вибору місця відкрийте «Інші дії».\n"
        "• При виборі номера вільні місця перед вами залишаться іншим."
    ),
    "⚙️ Дії в черзі": (
        "⚙️ <b>Дії в черзі та обмін місцями</b>\n\n"
        "• <b>Позначити здані роботи</b> — зберегти фактично здані лабораторні. Якщо здано все, запис завершується.\n"
        "• <b>Завершити підхід</b> — перейти із залишком робіт у кінець черги.\n"
        "• Присутність і готовність підтверджуються окремо. Запізнення й мовчання не забирають місце.\n"
        "• Налаштування робіт, часу й обміну зібрані в <b>«Інші дії»</b>.\n"
        "• <b>⬆️ Хочу вище / ⬇️ Хочу нижче</b> — встановити статус готовності здавати раніше або пропустити когось уперед. Бот автоматично повідомить одногрупників про збіг інтересів та запропонує швидкий обмін в один клік!\n"
        "• <b>🔁 Обмінятися місцями</b> — надіслати прямий запит на обмін позиціями обраному студенту.\n"
        "• <b>🚪 Покинути чергу</b> — звільнити місце та вийти з черги."
    ),
    "➕ Створення черги": (
        "➕ <b>Створення черги</b>\n\n"
        "• Відкрийте «Черги» ➔ «➕ Створити чергу».\n"
        "• Оберіть тип черги: «📖 Звичайна пара» або «💬 Консультація».\n"
        "• Оберіть предмет та викладача зі списку.\n"
        "• Черга відкриється для запису всій групі."
    ),
    "📅 Розклад та заміни": (
        "📅 <b>Розклад занять та заміни</b>\n\n"
        "• <b>Кнопки в меню:</b> «📅 Розклад» та «🔄 Заміни» показують пари на сьогодні, завтра або тиждень з урахуванням підгруп і чисельника/знаменника.\n"
        "• <b>Щоденний розклад:</b> щодня о 17:00 бот автоматично надсилає актуальний розклад на наступний день усім зареєстрованим студентам.\n"
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
    reporting_bug = State()
    admin_replying_bug = State()


def main_menu(conn, telegram_id: int):
    return get_reply_menu(conn, telegram_id)


@router.message(Command("start"), StateFilter("*"))
async def start(message: Message, state: FSMContext, conn):
    await state.clear()
    from queue_bot.services.maintenance import get_maintenance_end_time, is_maintenance_active
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
        "✅ Ім’я збережено!\n\n" + PROMPT,
        reply_markup=subgroup_keyboard(),
    )


@router.message(Command("subgroup"), StateFilter("*"))
@router.message(F.text == "👥 Моя підгрупа", StateFilter("*"))
async def choose_subgroup(message: Message, state: FSMContext, conn):
    user = users.get_user(conn, message.from_user.id)
    if not user:
        raise BotError("Спочатку зареєструйтесь: /start")
    await state.clear()
    current = f"Ваша підгрупа: {user['subgroup']}.\n\n" if user["subgroup"] else ""
    await message.answer(current + PROMPT, reply_markup=subgroup_keyboard())


@router.callback_query(F.data.in_({"subgroup:1", "subgroup:2"}), StateFilter("*"))
async def save_subgroup(callback: CallbackQuery, state: FSMContext, conn):
    subgroup = int(callback.data.split(":")[1])
    users.set_subgroup(conn, callback.from_user.id, subgroup)
    await state.clear()
    await show(callback, f"✅ Збережено: {subgroup} підгрупа.")
    await callback.bot.send_message(
        callback.from_user.id,
        "Головне меню. Оберіть дію на клавіатурі внизу 👇",
        reply_markup=get_reply_menu(conn, callback.from_user.id),
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


async def remember_support_home(state):
    current = await state.get_state() or ''
    data = await state.get_data()
    await state.update_data(support_mode='admin' if str(current).startswith('Admin') or data.get('ui_mode') == 'admin' else 'student')


async def return_from_support(message, state, conn):
    from queue_bot.handlers.navigation import admin_home, student_home
    if (await state.get_data()).get('support_mode') == 'admin':
        await admin_home(message, state, conn)
    else:
        await student_home(message, state, conn)


@router.message(F.text == "❓ Допомога", StateFilter("*"))
async def reply_help(message: Message, state: FSMContext):
    await remember_support_home(state)
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
    await return_from_support(message, state, conn)


@router.callback_query(F.data == "menu")
async def open_menu(callback: CallbackQuery, state: FSMContext, conn):
    await state.clear()
    if not users.get_user(conn, callback.from_user.id):
        raise BotError("Спочатку зареєструйтесь: /start")
    await callback.answer()
    await callback.message.answer("Головне меню:", reply_markup=main_menu(conn, callback.from_user.id))


@router.callback_query(F.data == "help")
async def help_screen(callback: CallbackQuery):
    await show(
        callback,
        "❓ <b>Довідка</b>\n\nВідкрийте «❓ Допомога» в головному меню для перегляду розділів.",
        make_keyboard([[("Назад", "menu")]]),
    )


# ---------- Повідомлення про баги (Баг-репорти) ----------

@router.message(Command("bug"), StateFilter("*"))
@router.message(F.text.in_({"🐞 Повідомити про баг", "🐞 Баг-репорт"}), StateFilter("*"))
async def start_bug_report(message: Message, state: FSMContext, conn):
    if not users.get_user(conn, message.from_user.id):
        raise BotError("Спочатку зареєструйтесь: /start")
    await remember_support_home(state)
    await state.set_state(NameForm.reporting_bug)
    await message.answer(
        "🐞 <b>Повідомлення про помилку або пропозиція</b>\n\n"
        "Опишіть, що саме працює некоректно (на які кнопки натискали та що сталося), "
        "або надішліть <b>скріншот із описом</b> прямо сюди в чат 👇",
        reply_markup=make_reply_keyboard([["🔙 Скасувати"]]),
    )


@router.message(NameForm.reporting_bug, F.text.in_({"🔙 Скасувати", "↩️ Скасувати", "🔙 Назад"}))
async def cancel_bug_report(message: Message, state: FSMContext, conn):
    await return_from_support(message, state, conn)


@router.message(NameForm.reporting_bug)
async def receive_bug_report(message: Message, state: FSMContext, conn):
    from queue_bot.services import bugs

    photo_id = message.photo[-1].file_id if message.photo else None
    text = (message.text or message.caption or "").strip()

    try:
        report_id = bugs.create_bug_report(
            conn,
            telegram_id=message.from_user.id,
            text=text,
            username=message.from_user.username,
            photo_file_id=photo_id,
            fallback_name=message.from_user.full_name or "Студент",
        )
    except BotError as err:
        await message.answer(
            f"⚠️ {err}\nНадішліть текст опису або натисніть «🔙 Скасувати»:",
            reply_markup=make_reply_keyboard([["🔙 Скасувати"]]),
        )
        return

    await bugs.notify_admins_new_bug(message.bot, conn, report_id)
    await message.answer(
        f"✅ <b>Дякуємо! Ваш баг-репорт #{report_id} передано адміністраторам.</b>\n\n"
        "Ми перевіримо це якнайшвидше та за потреби надішлемо відповідь у цей чат.",
    )
    await return_from_support(message, state, conn)


@router.message(F.text == "🐞 Баг-репорти", StateFilter("*"))
async def admin_view_bug_reports(message: Message, state: FSMContext, conn):
    if not admins.is_owner(conn, message.from_user.id):
        await message.answer("⛔ Цей розділ доступний лише власнику бота.")
        return
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from queue_bot.handlers.common import get_admin_settings_menu
    from queue_bot.services import bugs
    from queue_bot.services.maintenance import is_maintenance_active

    open_reports = bugs.list_bug_reports(conn, status="open", limit=10)
    if not open_reports:
        await message.answer(
            "🐞 <b>Баг-репорти</b>\n\n🎉 Наразі нових нерозглянутих баг-репортів немає!",
            reply_markup=get_admin_settings_menu(is_maintenance=is_maintenance_active(conn)),
        )
        return

    await message.answer(
        f"🐞 <b>Відкриті баг-репорти ({len(open_reports)}):</b>",
        reply_markup=get_admin_settings_menu(is_maintenance=is_maintenance_active(conn)),
    )
    for rep in open_reports[:5]:
        card_text = bugs.format_bug_report_card(rep)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="💬 Відповісти студенту", callback_data=f"bug_reply:{rep['id']}"),
                    InlineKeyboardButton(text="✅ Вирішено", callback_data=f"bug_resolve:{rep['id']}"),
                ]
            ]
        )
        if rep["photo_file_id"]:
            await message.answer_photo(rep["photo_file_id"], caption=card_text, reply_markup=kb)
        else:
            await message.answer(card_text, reply_markup=kb)


@router.callback_query(F.data.startswith("bug_resolve:"))
async def admin_resolve_bug_callback(callback: CallbackQuery, conn):
    if not admins.is_owner(conn, callback.from_user.id):
        await callback.answer("Тільки для власника бота.", show_alert=True)
        return
    from contextlib import suppress
    from queue_bot.services import bugs

    report_id = int(callback.data.split(":")[1])
    report = bugs.get_bug_report(conn, report_id)
    if not report:
        await callback.answer("Баг-репорт не знайдено.", show_alert=True)
        return

    bugs.resolve_bug_report(conn, report_id)
    await callback.answer("Позначено як вирішений!")

    updated_card = bugs.format_bug_report_card(bugs.get_bug_report(conn, report_id))
    with suppress(Exception):
        if callback.message.photo:
            await callback.message.edit_caption(caption=updated_card, reply_markup=None)
        else:
            await callback.message.edit_text(text=updated_card, reply_markup=None)

    with suppress(Exception):
        await callback.bot.send_message(
            report["user_telegram_id"],
            f"✅ <b>Ваш баг-репорт #{report_id} розглянуто та позначено як вирішений!</b>\n"
            "Дякуємо, що допомагаєте робити бота кращим.",
        )


@router.callback_query(F.data.startswith("bug_reply:"))
async def admin_reply_bug_callback(callback: CallbackQuery, state: FSMContext, conn):
    if not admins.is_owner(conn, callback.from_user.id):
        await callback.answer("Тільки для власника бота.", show_alert=True)
        return
    from queue_bot.services import bugs

    report_id = int(callback.data.split(":")[1])
    report = bugs.get_bug_report(conn, report_id)
    if not report:
        await callback.answer("Баг-репорт не знайдено.", show_alert=True)
        return

    await state.set_state(NameForm.admin_replying_bug)
    await state.update_data(reply_bug_id=report_id)
    await callback.answer()
    await callback.message.answer(
        f"💬 <b>Відповідь на баг-репорт #{report_id}</b> (студент: <b>{report['user_name']}</b>)\n\n"
        "Напишіть текст відповіді в чат — бот надішле її студенту та позначить звернення вирішеним 👇",
        reply_markup=make_reply_keyboard([["🔙 Скасувати"]]),
    )


@router.message(NameForm.admin_replying_bug, F.text)
async def admin_send_bug_reply(message: Message, state: FSMContext, conn):
    if not admins.is_owner(conn, message.from_user.id):
        await state.clear()
        return
    from contextlib import suppress
    from queue_bot.handlers.common import get_admin_reply_menu
    from queue_bot.services import bugs

    if message.text in {"🔙 Скасувати", "↩️ Скасувати", "🔙 Назад"}:
        await state.clear()
        await message.answer("Скасовано.", reply_markup=get_admin_reply_menu())
        return

    data = await state.get_data()
    report_id = data.get("reply_bug_id")
    report = bugs.get_bug_report(conn, report_id) if report_id else None
    if not report:
        await state.clear()
        await message.answer("Баг-репорт не знайдено.", reply_markup=get_admin_reply_menu())
        return

    reply_text = message.text.strip()
    bugs.resolve_bug_report(conn, report_id, admin_reply=reply_text)
    await state.clear()

    delivered = False
    with suppress(Exception):
        await message.bot.send_message(
            report["user_telegram_id"],
            f"💬 <b>Відповідь адміністратора на ваш баг-репорт #{report_id}:</b>\n\n"
            f"{reply_text}",
        )
        delivered = True

    status_note = "доставлено студенту" if delivered else "збережено (не вдалося доставити в ПП)"
    await message.answer(
        f"✅ <b>Відповідь на баг-репорт #{report_id} ({status_note})!</b>",
        reply_markup=get_admin_reply_menu(),
    )


@fallback_router.callback_query(StateFilter("*"))
async def stale_button(callback: CallbackQuery):
    """Спрацьовує останнім: кнопка не належить жодному обробнику (застаріла)."""
    await show(callback, "Ця кнопка застаріла. Натисніть /start, щоб відкрити меню.")
