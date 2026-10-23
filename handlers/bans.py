"""Temporary-ban wizard shared by user cards and queue student cards."""
import html
from datetime import timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.state import State, StatesGroup

import config
from handlers.common import make_reply_keyboard, get_user_card_menu
from services import admins, restrictions, queues, users
from services.errors import BotError

router = Router()
router.message.filter(F.chat.type == 'private')


class AdminBanFlow(StatesGroup):
    entering_days = State()
    confirming = State()


async def restore_card(message, state, conn, note=''):
    from handlers.admin import AdminUserState, format_user_card
    data = await state.get_data()
    uid = data.get('ban_user_id')
    user = users.get_user_by_id(conn, uid) if uid else None
    await state.clear()
    if not user:
        from handlers.navigation import admin_home
        await admin_home(message, state, conn)
        return
    await state.set_state(AdminUserState.viewing_card)
    await state.update_data(current_user_id=uid, ui_mode='admin')
    if data.get('session_id'):
        await state.update_data(session_id=data['session_id'])
    await message.answer(note + format_user_card(dict(user)), reply_markup=get_user_card_menu(bool(user['is_blocked'])))


@router.message(F.text == '⏳ Бан на N днів', StateFilter('*'))
async def start_ban(message, state, conn):
    admins.require_admin(conn, message.from_user.id)
    data = await state.get_data()
    uid = data.get('current_user_id')
    if not uid and data.get('entry_id'):
        entry = queues.get_entry(conn, data['entry_id'])
        uid = entry['user_id'] if entry else None
    user = users.get_user_by_id(conn, uid) if uid else None
    if not user:
        raise BotError('Спочатку оберіть студента в списку групи або черзі.')
    if admins.is_admin(conn, user['telegram_id']) or admins.is_owner(conn, user['telegram_id']):
        raise BotError('Не можна заблокувати адміністратора. Спочатку змініть його права.')
    await state.clear()
    await state.set_state(AdminBanFlow.entering_days)
    await state.update_data(ban_user_id=uid, ui_mode='admin')
    if data.get('session_id'):
        await state.update_data(session_id=data['session_id'])
    await message.answer(f'⏳ Бан для <b>{html.escape(user["full_name"])}</b>\n'
                         'Введіть кількість днів (1 день = 24 години). Студента буде знято з активних черг.',
                         reply_markup=make_reply_keyboard([['🔙 Скасувати бан']]))


@router.message(StateFilter(AdminBanFlow.entering_days, AdminBanFlow.confirming), F.text == '🔙 Скасувати бан')
async def cancel_ban(message, state, conn):
    admins.require_admin(conn, message.from_user.id)
    await restore_card(message, state, conn, 'Скасовано.\n\n')


@router.message(AdminBanFlow.entering_days, F.text)
async def receive_days(message, state, conn):
    admins.require_admin(conn, message.from_user.id)
    try:
        days = int(message.text.strip())
        if not 1 <= days <= 36500:
            raise ValueError
    except ValueError:
        await message.answer('Введіть ціле число днів від 1 до 36500 або скасуйте бан.')
        return
    data = await state.get_data()
    user = users.get_user_by_id(conn, data.get('ban_user_id'))
    if not user:
        raise BotError('Користувача вже не існує. Поверніться до списку групи.')
    until = (restrictions.utc_now() + timedelta(days=days)).astimezone(ZoneInfo(config.TIMEZONE))
    await state.update_data(ban_days=days)
    await state.set_state(AdminBanFlow.confirming)
    await message.answer(f'Забанити <b>{html.escape(user["full_name"])}</b> на <b>{days}</b> днів?\n'
                         f'До {until:%d.%m.%Y %H:%M} за Києвом.\nСтудента буде знято з усіх активних черг.',
                         reply_markup=make_reply_keyboard([['✅ Підтвердити бан'], ['🔙 Скасувати бан']]))


@router.message(AdminBanFlow.confirming, F.text == '✅ Підтвердити бан')
async def confirm_ban(message, state, conn):
    data = await state.get_data()
    restrictions.set_restriction(conn, message.from_user.id, data['ban_user_id'], days=data['ban_days'])
    await restore_card(message, state, conn, '⛔ Бан застосовано. Студента знято з активних черг.\n\n')
