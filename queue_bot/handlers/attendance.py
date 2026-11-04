"""Attendance controls independent of navigation/FSM screens."""
import html
from aiogram import Router, F
from aiogram.fsm.state import State, StatesGroup

from queue_bot.services import attendance, queues
from queue_bot.services.admins import require_admin
from queue_bot.services.errors import BotError
from queue_bot.services.lesson_notifier import keyboard, deliver

router = Router(name='attendance')


class LateTime(StatesGroup):
    minutes = State()


class TurnTime(StatesGroup):
    minutes = State()


def lesson_summary(conn, sid, uid):
    data = attendance.view(conn, sid, uid)
    if not data:
        return ''
    lines = ['\n\nЗдача на парі активна. На відповіді — 10 хвилин.']
    if data['collecting']:
        from datetime import datetime
        from queue_bot.services.schedule import UK_TZ
        until = datetime.fromtimestamp(data['check_in_until'], UK_TZ).strftime('%H:%M')
        lines.append(f'Збираємо присутність до {until} або до відповіді всіх студентів. Перший виклик — за порядком черги.')
    if data['current_name']:
        lines.append('Викликано / зараз здає: ' + html.escape(data['current_name']))
    if data['own']:
        lines.append('Ваш стан: ' + attendance.LABELS[data['own']['state']])
    rows = attendance.members(conn, data['id'])
    ready = sum(r['state'] in ('present', 'called', 'serving') for r in rows)
    late = sum(r['state'] == 'late' for r in rows)
    not_ready = sum(r['state'] == 'not_ready' for r in rows)
    lines.append(f'На місці / здають: {ready}. Запізнюються: {late}. Не готові: {not_ready}.')
    lines.append('Перед підходом бот окремо запитає готовність. «Не готовий» одразу пропускає до наступного; повернутися можна кнопкою «Вже готовий».')
    return '\n'.join(lines)


@router.message(F.text.in_({'Почати здачу на парі', 'Зупинити здачу на парі'}))
async def automatic_lesson_notice(message, state, conn, subjects):
    require_admin(conn, message.from_user.id)
    await message.answer('Здача запускається автоматично через 5 хвилин після початку пари та зупиняється за розкладом. Черга і результати зберігаються.')
    from queue_bot.handlers.admin.presentation import open_admin_queue_manager
    sid = (await state.get_data()).get('session_id')
    if sid and queues.get_session(conn, sid):
        await open_admin_queue_manager(message, conn, subjects, sid, state=state)


@router.callback_query(F.data.startswith('lesson:'))
async def automatic_lesson_callback(callback, conn):
    require_admin(conn, callback.from_user.id)
    await callback.answer('Здача працює автоматично за розкладом. Ця кнопка застаріла.', show_alert=True)


def screen_keyboard(conn, sid, uid):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    data = attendance.view(conn, sid, uid)
    entry = next((r for r in queues.get_entries(conn, sid) if r['telegram_id'] == uid), None)
    session = queues.get_session(conn, sid)
    rows = []
    if session['status'] != 'archived':
        if data and data['own']:
            rows.extend(keyboard(data['own'], session_id=sid).inline_keyboard)
        elif entry:
            rows.append([InlineKeyboardButton(text='Позначити здані роботи', callback_data=f'queue:{sid}:complete')])
        elif session['status'] != 'archived':
            rows.append([InlineKeyboardButton(text='Стати в чергу', callback_data=f'queue:{sid}:join')])
    rows.append([InlineKeyboardButton(text='Інші дії', callback_data=f'queue:{sid}:more'),
                 InlineKeyboardButton(text='Оновити', callback_data=f'queue:{sid}:refresh')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_controls(message, conn, sid, uid):
    # Kept for integrations; the queue screen itself sends one contextual keyboard.
    await message.answer('Дії для цієї черги', reply_markup=screen_keyboard(conn, sid, uid))


async def select_queue(state, sid):
    await state.clear()
    await state.update_data(session_id=sid)


@router.message(F.text == 'Присутність на парі')
async def presence_settings(message, state, conn):
    sid = (await state.get_data()).get('session_id')
    data = attendance.view(conn, sid, message.from_user.id) if sid else None
    if not data or not data['own']:
        await message.answer('Запит присутності доступний під час активної пари у своїй черзі.')
        return
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    row = data['own']
    if row['state'] in ('called', 'serving'):
        markup = keyboard(row, session_id=sid)
    else:
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=text, callback_data=f'qa:{sid}:{row["token"]}:{action}')]
            for text, action in [('Я вже на місці', 'present'), ('Запізнююсь', 'late'), ('На цій парі не буду', 'absent')]])
    await message.answer('Присутність на поточній парі', reply_markup=markup)


@router.callback_query(F.data.startswith('queue:'))
async def queue_action(callback, state, conn, subjects):
    from queue_bot.handlers.student import open_queue_screen
    try:
        _, raw_sid, action = callback.data.split(':')
        sid = int(raw_sid)
        session = queues._require_session(conn, sid)
        await select_queue(state, sid)
        uid = callback.from_user.id
        if action == 'complete':
            from queue_bot.handlers.completions import begin_completion
            await begin_completion(callback.message, state, conn, sid, telegram_id=uid, subjects=subjects)
        elif action == 'join':
            from queue_bot.handlers.lab_counts import begin_lab_count
            await begin_lab_count(callback.message, state, conn, sid, telegram_id=uid, subjects=subjects)
        elif action == 'more':
            from queue_bot.handlers.common import get_queue_actions_reply_menu
            entry = next((r for r in queues.get_entries(conn, sid) if r['telegram_id'] == uid), None)
            from queue_bot.services import lab_catalog
            await callback.message.answer('Інші дії для цієї черги', reply_markup=get_queue_actions_reply_menu(
                bool(entry), is_open=session['status'] != 'archived',
                can_pick_number=not queues.are_first_10_occupied(conn, sid) or queues.has_free_slots(conn, sid),
                swap_wish=entry['swap_wish'] if entry else None,
                labs_available=bool(lab_catalog.labs_for_session(session)),
                consultation=session['queue_type'] == 'consultation' or session['subject_id'] == 7))
        elif action == 'refresh':
            await open_queue_screen(callback.message, conn, subjects, sid, telegram_id=uid,
                                    refresh=True, edit_message=callback.message)
        else:
            raise BotError('Оновіть чергу й оберіть дію ще раз.')
        await callback.answer()
    except (ValueError, BotError) as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data.startswith('att:'))
@router.callback_query(F.data.startswith('qa:'))
async def attendance_answer(callback, state, conn, subjects):
    sid = None
    try:
        parts = callback.data.split(':')
        if parts[0] == 'qa':
            _, raw_sid, token, action = parts
            sid = int(raw_sid)
        else:
            _, token, action = parts
        found = conn.execute('SELECT r.session_id FROM lesson_attendance a JOIN lesson_runs r ON r.id = a.run_id WHERE a.token = ?', (token,)).fetchone()
        sid = sid if sid is not None else found['session_id'] if found else None
        data = attendance.view(conn, sid, callback.from_user.id) if sid is not None else None
        if not found or found['session_id'] != sid or not data or not data['own'] or data['own']['token'] != token:
            if sid is not None:
                await select_queue(state, sid)
                from queue_bot.handlers.student import open_queue_screen
                await open_queue_screen(callback.message, conn, subjects, sid,
                    note='Стан черги змінився. Показую актуальні дії; попередню дію не виконано.', telegram_id=callback.from_user.id)
                await callback.answer('Показано актуальний стан')
            else:
                await callback.answer('Це старе повідомлення. Відкрийте свою чергу через «Мої черги».')
            return
        await select_queue(state, sid)
        if action in ('ready', 'extend', 'review', 'done'):
            own = data['own']
            entry = queues.get_entry(conn, own['entry_id'])
            if action in ('extend', 'review', 'done'):
                attendance.require_turn(conn, entry)
            if action == 'extend' or action == 'ready' and entry['planned_minutes'] is None:
                await state.set_state(TurnTime.minutes)
                await state.update_data(turn_token=token, turn_action=action)
                prompt = 'Скільки додаткових хвилин потрібно?' if action == 'extend' else 'Скільки хвилин плануєте здавати?'
                await callback.message.answer(prompt + ' Введіть число від 1 до 240 або /cancel.')
                await callback.answer()
                return
            if action == 'review':
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                await callback.message.answer('Позначте фактично здані роботи або завершіть підхід із незданими. Поки результат не підтверджено, жодну роботу не зараховано.',
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text='Позначити здані роботи', callback_data=f'qa:{sid}:{token}:done')],
                        [InlineKeyboardButton(text='Завершити з незданими роботами', callback_data=f'qa:{sid}:{token}:finish')],
                        [InlineKeyboardButton(text='Ще здаю — додати час', callback_data=f'qa:{sid}:{token}:extend')]]))
                await callback.answer()
                return
            if action == 'done':
                from queue_bot.handlers.completions import begin_completion
                await begin_completion(callback.message, state, conn, sid, telegram_id=callback.from_user.id, subjects=subjects)
                await callback.answer()
                return
        if action in ('late', 'custom'):
            if data['own']['state'] == 'serving':
                raise BotError('Спочатку завершіть поточний підхід.')
            await state.set_state(LateTime.minutes)
            await state.update_data(attendance_token=token)
            await callback.message.answer('Через скільки хвилин будете? Введіть число від 1 до 240 або /cancel.')
            await callback.answer()
            return
        if action == 'finish':
            from queue_bot.services import lab_catalog
            session = queues.get_session(conn, sid)
            queues.finish_own_entry(conn, callback.from_user.id, sid, passed=not bool(lab_catalog.labs_for_session(session)))
            attendance.tick(conn)
        else:
            minutes = int(action[4:]) if action.startswith('late') else None
            sid = attendance.respond(conn, callback.from_user.id, token, 'late' if minutes else action, minutes=minutes)
        await callback.answer('Відповідь збережено')
        await state.update_data(session_id=sid)
        await deliver(callback.bot, conn, subjects)
        from queue_bot.handlers.student import open_queue_screen
        await open_queue_screen(callback.message, conn, subjects, sid, telegram_id=callback.from_user.id)
    except (ValueError, BotError) as exc:
        await callback.answer(str(exc), show_alert=True)


@router.message(LateTime.minutes, F.text.regexp(r'^(?:/cancel|[+-]?\d+(?:[.,]\d+)?)$'))
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


@router.message(TurnTime.minutes, F.text.regexp(r'^(?:/cancel|[+-]?\d+(?:[.,]\d+)?)$'))
async def turn_time(message, state, conn, subjects):
    if message.text == '/cancel':
        await state.clear()
        await message.answer('Скасовано. Час підходу не змінено.')
        return
    try:
        minutes = int(message.text or '')
        if not 1 <= minutes <= 240:
            raise ValueError
        data = await state.get_data()
        sid = attendance.respond(conn, message.from_user.id, data.get('turn_token'), data.get('turn_action'), minutes=minutes)
    except (ValueError, BotError) as exc:
        await message.answer(str(exc) if isinstance(exc, BotError) else 'Введіть число від 1 до 240 або /cancel.')
        return
    await state.clear()
    await state.update_data(session_id=sid)
    await deliver(message.bot, conn, subjects)
    await message.answer(f'Час підтверджено: {minutes} хв. Після нього бот нагадає про завершення. На відповідь після нагадування — 10 хвилин.')


@router.callback_query(F.data.startswith('turn:'))
async def finish_turn(callback, conn, subjects, state=None):
    try:
        _, eid, revision = callback.data.split(':')
        entry = queues._require_active_entry(conn, int(eid))
        owner = conn.execute('SELECT telegram_id FROM users WHERE id = ?', (entry['user_id'],)).fetchone()
        if not owner or owner['telegram_id'] != callback.from_user.id:
            raise BotError('Ця кнопка призначена іншому студенту.')
        queues._check_revision(entry, int(eid), int(revision))
        queues.finish_own_entry(conn, callback.from_user.id, entry['session_id'], passed=False)
        if state is not None:
            await select_queue(state, entry['session_id'])
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
