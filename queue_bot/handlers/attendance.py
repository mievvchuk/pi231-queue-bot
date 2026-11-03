"""Attendance controls independent of navigation/FSM screens."""
import html
from aiogram import Router, F
from aiogram.fsm.state import State, StatesGroup

from queue_bot.services import attendance, queues
from queue_bot.services.admins import require_admin
from queue_bot.services.errors import BotError
from queue_bot.services.lesson_notifier import keyboard, late_keyboard, deliver

router = Router(name='attendance')


class LateTime(StatesGroup):
    minutes = State()


def lesson_summary(conn, sid, uid):
    data = attendance.view(conn, sid, uid)
    if not data:
        return '\n\nРежим здачі на парі не запущено.'
    lines = ['\n\nЗдача на парі активна. На відповіді — 10 хвилин.']
    if data['current_name']:
        lines.append('Викликано / зараз здає: ' + html.escape(data['current_name']))
    if data['own']:
        lines.append('Ваш стан: ' + attendance.LABELS[data['own']['state']])
    rows = attendance.members(conn, data['id'])
    ready = sum(r['state'] in ('present', 'called', 'serving') for r in rows)
    late = sum(r['state'] == 'late' for r in rows)
    lines.append(f'Готові: {ready}. Запізнюються: {late}. Решту тимчасово пропускаємо до підтвердження.')
    return '\n'.join(lines)


@router.message(F.text == 'Почати здачу на парі')
async def begin_lesson(message, state, conn):
    require_admin(conn, message.from_user.id)
    sid = (await state.get_data()).get('session_id')
    if not sid:
        raise BotError('Спочатку відкрийте потрібну чергу.')
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    await message.answer('Оберіть тривалість здачі на цій парі. Після її завершення автоматичні виклики зупиняться. На обидві відповіді даємо 10 хвилин.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f'{n} хв', callback_data=f'lesson:{sid}:{n}') for n in (40, 80, 90)]]))


@router.callback_query(F.data.startswith('lesson:'))
async def start_lesson(callback, conn, subjects):
    try:
        _, sid, minutes = callback.data.split(':')
        attendance.start(conn, callback.from_user.id, int(sid), minutes=int(minutes))
    except (ValueError, BotError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer('Здачу запущено')
    await deliver(callback.bot, conn, subjects)
    await callback.message.answer('Здачу запущено. Порядок збережено, студентам надіслано запит присутності. На відповідь — 10 хвилин.')


@router.message(F.text == 'Зупинити здачу на парі')
async def stop_lesson(message, state, conn):
    sid = (await state.get_data()).get('session_id')
    attendance.stop(conn, message.from_user.id, sid)
    await message.answer('Здачу на цій парі зупинено. Місця збережено.')


async def show_controls(message, conn, sid, uid):
    data = attendance.view(conn, sid, uid)
    if data and data['own']:
        await message.answer('Присутність і готовність на цій парі. На відповіді — 10 хвилин.', reply_markup=keyboard(data['own']))
    entry = next((r for r in queues.get_entries(conn, sid) if r['telegram_id'] == uid), None)
    from queue_bot.services import lab_catalog
    session = queues.get_session(conn, sid)
    if entry and lab_catalog.labs_for_session(session) and (not data or data['own'] and data['own']['state'] == 'serving'):
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        await message.answer('Позначайте здані роботи кнопкою «Здав». Коли більше не здаєте зараз, завершіть підхід: решта робіт перейде в кінець черги.',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text='Завершити підхід з рештою робіт', callback_data=f'turn:{entry["id"]}:{entry["entry_revision"]}')]]))


@router.callback_query(F.data.startswith('att:'))
async def attendance_answer(callback, state, conn, subjects):
    try:
        _, token, action = callback.data.split(':')
        if action in ('late', 'custom'):
            row = conn.execute('SELECT r.session_id FROM lesson_attendance a JOIN lesson_runs r ON r.id = a.run_id WHERE a.token = ?', (token,)).fetchone()
            data = attendance.view(conn, row['session_id'], callback.from_user.id) if row else None
            if not data or not data['own'] or data['own']['token'] != token:
                raise BotError('Ця кнопка вже неактуальна.')
            if action == 'custom':
                await state.set_state(LateTime.minutes)
                await state.update_data(attendance_token=token)
                await callback.message.answer('Через скільки хвилин будете? Введіть число від 1 до 240 або /cancel.')
            else:
                await callback.message.answer('Через скільки хвилин будете?', reply_markup=late_keyboard(token))
            await callback.answer()
            return
        if action == 'finish':
            row = conn.execute('SELECT a.entry_id, a.user_id, r.session_id FROM lesson_attendance a JOIN lesson_runs r ON r.id = a.run_id WHERE a.token = ?', (token,)).fetchone()
            data = attendance.view(conn, row['session_id'], callback.from_user.id) if row else None
            if not data or not data['own'] or data['own']['token'] != token:
                raise BotError('Ця кнопка застаріла.')
            from queue_bot.services import lab_catalog
            session = queues.get_session(conn, row['session_id'])
            queues.finish_own_entry(conn, callback.from_user.id, row['session_id'], passed=not bool(lab_catalog.labs_for_session(session)))
            attendance.tick(conn)
            sid = row['session_id']
        else:
            minutes = int(action[4:]) if action.startswith('late') else None
            sid = attendance.respond(conn, callback.from_user.id, token, 'late' if minutes else action, minutes=minutes)
        await callback.answer('Відповідь збережено')
        await deliver(callback.bot, conn, subjects)
        from queue_bot.handlers.student import open_queue_screen
        await open_queue_screen(callback.message, conn, subjects, sid, telegram_id=callback.from_user.id)
    except (ValueError, BotError) as exc:
        await callback.answer(str(exc), show_alert=True)


@router.message(LateTime.minutes)
async def custom_late_time(message, state, conn, subjects):
    if message.text == '/cancel':
        await state.clear()
        await message.answer('Скасовано. Статус не змінено.')
        return
    try:
        minutes = int(message.text or '')
        sid = attendance.respond(conn, message.from_user.id, (await state.get_data()).get('attendance_token'), 'late', minutes=minutes)
    except (ValueError, BotError) as exc:
        await message.answer(str(exc) if isinstance(exc, BotError) else 'Введіть ціле число хвилин або /cancel.')
        return
    await state.clear()
    await state.update_data(session_id=sid)
    await deliver(message.bot, conn, subjects)
    await message.answer('Запізнення підтверджено. Місце збережене; поки інші можуть здавати.')


@router.callback_query(F.data.startswith('turn:'))
async def finish_turn(callback, conn, subjects):
    try:
        _, eid, revision = callback.data.split(':')
        entry = queues._require_active_entry(conn, int(eid))
        owner = conn.execute('SELECT telegram_id FROM users WHERE id = ?', (entry['user_id'],)).fetchone()
        if not owner or owner['telegram_id'] != callback.from_user.id:
            raise BotError('Ця кнопка призначена іншому студенту.')
        queues._check_revision(entry, int(eid), int(revision))
        queues.finish_own_entry(conn, callback.from_user.id, entry['session_id'], passed=False)
        from queue_bot.handlers.common import notify_next_students
        await notify_next_students(callback.bot, conn, subjects, entry['session_id'], removed_position=entry['position'])
        await callback.answer('Решта робіт у кінці черги')
        from queue_bot.handlers.student import open_queue_screen
        await open_queue_screen(callback.message, conn, subjects, entry['session_id'], telegram_id=callback.from_user.id)
    except (ValueError, BotError) as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data.startswith('attadmin:'))
async def correct_attendance(callback, conn, subjects):
    try:
        _, eid, run_id, action = callback.data.split(':')
        attendance.correct(conn, callback.from_user.id, int(eid), action, run_id=int(run_id))
        await callback.answer('Стан оновлено')
        await deliver(callback.bot, conn, subjects)
    except (ValueError, BotError) as exc:
        await callback.answer(str(exc), show_alert=True)
