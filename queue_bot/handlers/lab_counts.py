"""Numbered lab selection and a student estimate bounded by the chosen limits."""
from aiogram import BaseMiddleware
from aiogram.fsm.state import State, StatesGroup

from queue_bot.handlers.common import make_reply_keyboard, show
from queue_bot.handlers.lab_picker import start_picker, process_picker, picker_content, picker_back
from queue_bot.services import queues, lab_catalog
from queue_bot.services.errors import BotError
from queue_bot.services.lab_estimates import validate_planned_minutes
from queue_bot.services.restrictions import require_available

LAB_COUNT_BUTTON = '🧪 Обрати лабораторні'
TIME_BUTTON = '⏱ Час здачі'


class LabCountFlow(StatesGroup):
    entering = State()
    minutes = State()


class QueueDraftNavigation(BaseMiddleware):
    async def __call__(self, handler, event, data):
        from queue_bot.handlers.completions import CompletionFlow, receive_passed_labs, passed_selection_callback
        state = data.get('state')
        selected = getattr(data.get('handler'), 'callback', None)
        current = await state.get_state() if state else None
        # A stale inline picker must not cancel a newer operation.
        picker_callbacks = {lab_selection_callback, passed_selection_callback}
        receivers = {LabCountFlow.entering.state: receive_lab_count,
                     LabCountFlow.minutes.state: receive_planned_minutes,
                     CompletionFlow.count.state: receive_passed_labs}
        if state and current in receivers and selected not in picker_callbacks and selected is not receivers[current]:
            context = await state.get_data()
            await state.clear()
            await state.set_data({k: v for k, v in context.items() if not k.startswith(('lab_', 'picker_', 'passed_'))})
        return await handler(event, data)


async def begin_lab_count(message, state, conn, session_id, *, action='join', position=None, telegram_id=None, subjects=None):
    from queue_bot.handlers.student import open_queue_screen
    uid = telegram_id if telegram_id is not None else message.from_user.id
    require_available(conn, uid)
    session = queues.get_session(conn, session_id)
    if not session or session['status'] == 'archived':
        raise BotError('Ця черга вже недоступна.')
    entry = next((r for r in queues.get_entries(conn, session_id) if r['telegram_id'] == uid), None)
    if action == 'join':
        if session['status'] == 'archived':
            raise BotError('Запис у цю чергу закрито.')
        if entry:
            raise BotError('Ви вже записані в цю чергу.')
    elif entry is None:
        raise BotError('Ви не записані в цю чергу.')
    if not lab_catalog.labs_for_session(session):
        await state.clear()
        await state.update_data(session_id=session_id)
        if action == 'join':
            place, total = queues.join_queue(conn, uid, session_id, position)
            note = f'✅ Ви записалися. Ваше місце: <b>{place}</b> із {total}.'
        else:
            note = 'Ця черга без лабораторних і лімітів часу.'
        await open_queue_screen(message, conn, subjects, session_id=session_id, note=note, telegram_id=uid)
        return
    numbers = lab_catalog.read_numbers(entry['selected_labs']) if entry else None
    completed = set(lab_catalog.read_numbers(entry['passed_labs']) or []) if entry else set()
    await state.update_data(session_id=session_id, lab_action=action, lab_position=position,
                            lab_entry_id=entry['id'] if entry else None,
                            lab_revision=entry['entry_revision'] if entry else None)
    if action == 'time' and numbers:
        await state.update_data(lab_numbers=numbers)
        await state.set_state(LabCountFlow.minutes)
        await prompt_minutes(message, session, numbers)
        return
    if action == 'time':
        await state.update_data(lab_action='edit')
    await state.set_state(LabCountFlow.entering)
    await start_picker(message, state, session, prefix='labpick',
                       allowed=[lab['number'] for lab in lab_catalog.labs_for_session(session) if lab['number'] not in completed],
                       selected=numbers or [])


async def ask_lab_count(message, state, conn, subjects):
    from queue_bot.handlers.student import ensure_session_id
    sid = await ensure_session_id(message, state)
    if sid:
        await begin_lab_count(message, state, conn, sid, action='edit', subjects=subjects)


async def ask_planned_minutes(message, state, conn, subjects):
    from queue_bot.handlers.student import ensure_session_id
    sid = await ensure_session_id(message, state)
    if sid:
        await begin_lab_count(message, state, conn, sid, action='time', subjects=subjects)


async def prepare_minutes(message, state, conn, numbers):
    data = await state.get_data()
    session = queues.get_session(conn, data['session_id'])
    numbers = lab_catalog.validate_numbers(session, numbers)
    if not set(numbers).issubset(data.get('picker_allowed', [])):
        raise BotError('Оберіть лише доступні лабораторні.')
    await state.update_data(lab_numbers=numbers)
    await state.set_state(LabCountFlow.minutes)
    await prompt_minutes(message, session, numbers)


async def receive_lab_count(message, state, conn, subjects):
    data = await state.get_data()
    session = queues.get_session(conn, data.get('session_id'))
    try:
        numbers = lab_catalog.parse_numbers(message.text, session, allowed=data.get('picker_allowed'))
        await prepare_minutes(message, state, conn, numbers)
    except BotError as error:
        await message.answer(str(error))


async def lab_selection_callback(callback, state, conn, subjects):
    result = await process_picker(callback, state, LabCountFlow.entering.state, 'labpick')
    if not result:
        return
    action, data = result
    if action == 'back':
        await picker_back(callback, state, conn, subjects)
    elif action == 'ok':
        await prepare_minutes(callback.message, state, conn, data['picker_selected'])
    else:
        text, markup = picker_content(queues.get_session(conn, data['session_id']), data, 'labpick')
        await show(callback, text, markup)


async def prompt_minutes(message, session, numbers):
    limit = lab_catalog.selection_limit(session, numbers)
    choices = sorted({n for n in (5, 10, 15, 20, limit) if n <= limit})
    await message.answer(f'⏱ <b>Скільки хвилин приблизно займе здача?</b>\n\n'
                         f'Обрано: {lab_catalog.format_numbers(numbers)}. Максимальний ліміт: <b>{limit} хв</b>.\n'
                         f'Введіть ціле число від <b>5 до {limit}</b>. Це тривалість вашої здачі, не очікування.',
                         reply_markup=make_reply_keyboard([[str(n) for n in choices], ['🔙 Назад до черги']]))


async def receive_planned_minutes(message, state, conn, subjects):
    from queue_bot.handlers.student import open_queue_screen
    data = await state.get_data()
    sid, action = data.get('session_id'), data.get('lab_action')
    if not sid or action not in {'join', 'edit', 'time'}:
        await state.clear()
        raise BotError('Потрібно знову обрати чергу.')
    numbers = data.get('lab_numbers')
    text = message.text.strip()
    try:
        minutes = int(text) if text.isascii() and text.isdigit() and len(text) <= 10 else None
        validate_planned_minutes(minutes, allow_unknown=False)
        lab_catalog.validate_selection_time(queues.get_session(conn, sid), numbers, minutes)
    except BotError as error:
        await message.answer(str(error))
        return
    try:
        if action == 'join':
            place, total = queues.join_queue(conn, message.from_user.id, sid, data.get('lab_position'),
                                             lab_numbers=numbers, planned_minutes=minutes)
            note = f'✅ Ви записалися. Ваше місце: <b>{place}</b> із {total}.'
        elif action == 'time':
            queues.set_own_planned_minutes(conn, message.from_user.id, sid, minutes,
                                           expected_entry_id=data['lab_entry_id'], expected_revision=data['lab_revision'])
            note = '✅ Час оновлено. Ваше місце збережено.'
        else:
            queues.set_own_lab_selection(conn, message.from_user.id, sid, numbers, planned_minutes=minutes,
                                         expected_entry_id=data['lab_entry_id'], expected_revision=data['lab_revision'])
            note = '✅ Лабораторні й час оновлено. Ваше місце збережено.'
    finally:
        await state.clear()
        await state.update_data(session_id=sid)
    await open_queue_screen(message, conn, subjects, sid, note=note)
