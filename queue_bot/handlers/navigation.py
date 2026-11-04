"""Explicit role views; permissions remain enforced by admin services."""
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.state import State, StatesGroup

from queue_bot.handlers.common import get_reply_menu, get_student_queues_menu, get_student_more_menu
from queue_bot.services import admins, queues, users
from queue_bot.services.errors import BotError

router = Router()
router.message.filter(F.chat.type == 'private')


class AdminNavigation(StatesGroup):
    queue = State()


class StudentNavigation(StatesGroup):
    queue = State()


async def student_home(message, state, conn, note=''):
    if not users.get_user(conn, message.from_user.id):
        raise BotError('Спочатку зареєструйтесь: /start')
    await state.clear()
    await state.update_data(ui_mode='student')
    await message.answer(f'{note}👤 <b>Режим студента</b>\nГоловне меню. Оберіть дію 👇',
                         reply_markup=get_reply_menu(conn, message.from_user.id))


@router.message(F.text == 'Черги', StateFilter('*'))
async def student_queues_section(message, state, conn):
    if not users.get_user(conn, message.from_user.id):
        raise BotError('Спочатку зареєструйтесь: /start')
    await state.clear()
    await state.update_data(ui_mode='student')
    await message.answer('Черги\n«Мої черги» — ваші записи. «Усі черги» — перегляд і запис у чергу.',
                         reply_markup=get_student_queues_menu())


@router.message(F.text == 'Ще', StateFilter('*'))
async def student_more_section(message, state, conn):
    if not users.get_user(conn, message.from_user.id):
        raise BotError('Спочатку зареєструйтесь: /start')
    await state.clear()
    await state.update_data(ui_mode='student')
    await message.answer('Ще\nСтворення черги, допомога та інші можливості.',
                         reply_markup=get_student_more_menu(conn, message.from_user.id))


async def admin_home(message, state, conn):
    admins.require_admin(conn, message.from_user.id)
    from queue_bot.handlers.admin import admin_panel_open
    await admin_panel_open(message, state, conn)
    await state.update_data(ui_mode='admin')


async def switch_student(message, state, conn, subjects):
    sid = (await state.get_data()).get('session_id')
    session = queues.get_session(conn, sid) if sid else None
    if not session or session['status'] == 'archived':
        await student_home(message, state, conn, 'Черга вже недоступна.\n' if sid else '')
        return
    from queue_bot.handlers.student import open_queue_screen
    await state.clear()
    await state.set_state(StudentNavigation.queue)
    await state.update_data(session_id=sid, ui_mode='student')
    await open_queue_screen(message, conn, subjects, sid)


async def switch_admin(message, state, conn, subjects):
    admins.require_admin(conn, message.from_user.id)
    sid = (await state.get_data()).get('session_id')
    session = queues.get_session(conn, sid) if sid else None
    if not session or session['status'] == 'archived':
        if sid:
            await message.answer('Черга вже недоступна. Оберіть іншу в адмін-панелі.')
        await admin_home(message, state, conn)
        return
    from queue_bot.handlers.admin import open_admin_queue_manager
    await state.clear()
    await state.set_state(AdminNavigation.queue)
    await state.update_data(session_id=sid, ui_mode='admin')
    await open_admin_queue_manager(message, conn, subjects, sid)


@router.message(F.text.in_({'👤 Режим студента', '👤 Як студент (Моє місце)'}), StateFilter('*'))
async def student_mode(message, state, conn, subjects):
    await switch_student(message, state, conn, subjects)


@router.message(F.text.in_({'⚡ Режим адміна', '⚡ Керувати чергою (Адмін)'}), StateFilter('*'))
async def admin_mode(message, state, conn, subjects):
    await switch_admin(message, state, conn, subjects)


@router.message(F.text.in_({'⚡ Адмін-панель', '🔙 В адмін-панель'}), StateFilter('*'))
async def admin_panel(message, state, conn):
    await admin_home(message, state, conn)


@router.message(F.text.in_({'🏠 Головне меню', 'Головне меню', '🏠 Головне меню (Як студент)'}), StateFilter('*'))
async def home(message, state, conn):
    current = await state.get_state() or ''
    mode = (await state.get_data()).get('ui_mode')
    if message.text != '🏠 Головне меню (Як студент)' and (current.startswith('Admin') or mode == 'admin'):
        await admin_home(message, state, conn)
    else:
        await student_home(message, state, conn)


@router.message(AdminNavigation.queue, F.text.in_({'🔙 Назад', '🔙 До черг', '🔙 До списку черг'}))
async def back_from_admin_queue(message, state, conn):
    from queue_bot.handlers.admin import admin_category_queues
    admins.require_admin(conn, message.from_user.id)
    await admin_category_queues(message, state)
    await state.update_data(ui_mode='admin')


@router.message(AdminNavigation.queue, F.text == '🔄 Оновити')
async def refresh_admin_queue(message, state, conn, subjects):
    await switch_admin(message, state, conn, subjects)


@router.message(F.text == '🔙 Назад до черги', StateFilter('*'))
async def back_to_queue(message, state, conn, subjects):
    current = await state.get_state() or ''
    if current.startswith('Admin') or (await state.get_data()).get('ui_mode') == 'admin':
        await switch_admin(message, state, conn, subjects)
    else:
        await switch_student(message, state, conn, subjects)


@router.message(F.text == '📱 Мініапка', StateFilter('*'))
async def open_mini_app(message, conn):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    from queue_bot import config
    if not config.MINI_APP_URL:
        raise BotError('Мініапка ще не підключена. Поки користуйтесь кнопками бота.')
    # Reply web_app buttons have no signed initData; launch through an inline button.
    await message.answer('📱 Черги, розклад і твої записи — в одному місці. Кнопки бота також залишаються доступними.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='Відкрити мініапку', web_app=WebAppInfo(url=config.MINI_APP_URL))]]))
