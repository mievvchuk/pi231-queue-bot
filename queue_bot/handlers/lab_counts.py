"""Quantity prompt shared by signup and editing an existing queue entry."""
from aiogram import BaseMiddleware
from aiogram.fsm.state import State, StatesGroup

from queue_bot.handlers.common import make_reply_keyboard
from queue_bot.handlers.completions import CompletionFlow, receive_passed_labs
from queue_bot.services import queues
from queue_bot.services.errors import BotError
from queue_bot.services.lab_estimates import MAX_LAB_COUNT, validate_planned_minutes
from queue_bot.services.restrictions import require_available

LAB_COUNT_BUTTON = '🧪 Кількість лабораторних'
TIME_BUTTON = '⏱ Час здачі'
SKIP_COUNT = '➖ Не вказувати'


class LabCountFlow(StatesGroup):
    entering = State()
    minutes = State()


class QueueDraftNavigation(BaseMiddleware):
    """Discard pending workload or completion when another handler takes over the UI.

    Runs as dispatcher inner middleware after handler selection, including
    handlers belonging to schedule, polls and other routers.
    """
    async def __call__(self, handler, event, data):
        state = data.get('state')
        selected = getattr(data.get('handler'), 'callback', None)
        current = await state.get_state() if state else None
        receivers = {LabCountFlow.entering.state: receive_lab_count,
                     LabCountFlow.minutes.state: receive_planned_minutes,
                     CompletionFlow.count.state: receive_passed_labs}
        if state and current in receivers and selected is not receivers[current]:
            context = await state.get_data()
            await state.clear()
            await state.set_data({key: value for key, value in context.items()
                                  if key not in {'lab_action', 'lab_position', 'lab_count',
                                                 'passed_entry_id', 'passed_expected_count'}})
        return await handler(event, data)


async def begin_lab_count(message, state, conn, session_id, *, action='join', position=None):
    require_available(conn, message.from_user.id)
    session = queues.get_session(conn, session_id)
    if not session or session['status'] == 'archived':
        raise BotError('Ця черга вже недоступна.')
    current = queues.get_user_position(conn, message.from_user.id, session_id)
    if action == 'join':
        if session['status'] != 'open':
            raise BotError('Запис у цю чергу закрито.')
        if current is not None:
            raise BotError('Ви вже записані в цю чергу.')
    elif current is None:
        raise BotError('Ви не записані в цю чергу.')
    await state.set_state(LabCountFlow.minutes if action == 'time' else LabCountFlow.entering)
    await state.update_data(session_id=session_id, lab_action=action, lab_position=position)
    if action == 'time':
        await prompt_minutes(message)
        return
    note = ('Місце буде зайнято після введення кількості та часу.' if action == 'join'
            else 'Ваше місце в черзі не зміниться.')
    await message.answer(
        f'🧪 <b>Скільки лабораторних плануєте здати?</b>\n\n'
        f'Оберіть кнопку або введіть число від 1 до {MAX_LAB_COUNT}. '
        'Після цього вкажіть свій приблизний час здачі.\n\n'
        f'{note}\nДля консультації чи невідомої кількості натисніть «Не вказувати».',
        reply_markup=make_reply_keyboard([['1', '2', '3', '4'], [SKIP_COUNT], ['🔙 Назад до черги']]),
    )


async def ask_lab_count(message, state, conn):
    from queue_bot.handlers.student import ensure_session_id
    session_id = await ensure_session_id(message, state)
    if session_id:
        await begin_lab_count(message, state, conn, session_id, action='edit')


async def receive_lab_count(message, state, conn, subjects):
    text = message.text.strip()
    if text == SKIP_COUNT:
        count = None
    elif text.isascii() and text.isdigit() and len(text) <= 2 and 1 <= int(text) <= MAX_LAB_COUNT:
        count = int(text)
    else:
        await message.answer(f'Введіть ціле число від 1 до {MAX_LAB_COUNT} або натисніть «Не вказувати».')
        return
    data = await state.get_data()
    session_id = data.get('session_id')
    if not session_id or data.get('lab_action') not in {'join', 'edit'}:
        await state.clear()
        raise BotError('Потрібно знову обрати чергу.')
    await state.update_data(lab_count=count)
    await state.set_state(LabCountFlow.minutes)
    await prompt_minutes(message)


async def prompt_minutes(message):
    await message.answer(
        '⏱ <b>Скільки хвилин приблизно займе ваша здача?</b>\n\n'
        'Введіть ціле число хвилин, мінімум <b>5</b>. Наприклад: 15. '
        'Це ваша оцінка тривалості здачі, а не час очікування.',
        reply_markup=make_reply_keyboard([['5', '10', '15', '20'], ['🔙 Назад до черги']]),
    )


async def ask_planned_minutes(message, state, conn):
    from queue_bot.handlers.student import ensure_session_id
    session_id = await ensure_session_id(message, state)
    if session_id:
        await begin_lab_count(message, state, conn, session_id, action='time')


async def receive_planned_minutes(message, state, conn, subjects):
    from queue_bot.handlers.student import open_queue_screen
    text = message.text.strip()
    try:
        minutes = int(text) if text.isascii() and text.isdigit() and len(text) <= 10 else None
        validate_planned_minutes(minutes, allow_unknown=False)
    except BotError as error:
        await message.answer(str(error))
        return
    data = await state.get_data()
    session_id = data.get('session_id')
    action = data.get('lab_action')
    if not session_id or action not in {'join', 'edit', 'time'}:
        await state.clear()
        raise BotError('Потрібно знову обрати чергу.')
    count = data.get('lab_count')
    try:
        if action == 'join':
            position, total = queues.join_queue(conn, message.from_user.id, session_id,
                                                data.get('lab_position'), lab_count=count, planned_minutes=minutes)
            note = f'✅ <b>Ви записалися</b>\n\nВаше місце: <b>{position}</b> із {total}.'
        elif action == 'time':
            queues.set_own_planned_minutes(conn, message.from_user.id, session_id, minutes)
            note = '✅ Час здачі оновлено. Ваше місце збережено.'
        else:
            queues.set_own_lab_count(conn, message.from_user.id, session_id, count, planned_minutes=minutes)
            note = '✅ Кількість лабораторних і час оновлено. Ваше місце збережено.'
    except BotError:
        # A taken slot/closed queue needs a fresh choice, not an endless count prompt.
        await state.clear()
        await state.update_data(session_id=session_id)
        raise
    await state.clear()
    await state.update_data(session_id=session_id)
    await open_queue_screen(message, conn, subjects, session_id, note=note)
