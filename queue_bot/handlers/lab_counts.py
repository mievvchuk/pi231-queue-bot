"""Quantity prompt shared by signup and editing an existing queue entry."""
from aiogram.fsm.state import State, StatesGroup

from queue_bot.handlers.common import make_reply_keyboard
from queue_bot.services import queues
from queue_bot.services.errors import BotError
from queue_bot.services.lab_estimates import MAX_LAB_COUNT
from queue_bot.services.restrictions import require_available

LAB_COUNT_BUTTON = '🧪 Кількість лабораторних'
SKIP_COUNT = '➖ Не вказувати'


class LabCountFlow(StatesGroup):
    entering = State()


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
    await state.set_state(LabCountFlow.entering)
    await state.update_data(session_id=session_id, lab_action=action, lab_position=position)
    note = ('Місце буде зайнято після вибору кількості.' if action == 'join'
            else 'Ваше місце в черзі не зміниться.')
    await message.answer(
        f'🧪 <b>Скільки лабораторних плануєте здати?</b>\n\n'
        f'Оберіть кнопку або введіть число від 1 до {MAX_LAB_COUNT}. '
        'Бот покаже приблизну тривалість здачі, а не час очікування.\n\n'
        f'{note}\nДля консультації чи невідомої кількості натисніть «Не вказувати».',
        reply_markup=make_reply_keyboard([['1', '2', '3', '4'], [SKIP_COUNT], ['🔙 Назад до черги']]),
    )


async def ask_lab_count(message, state, conn):
    from queue_bot.handlers.student import ensure_session_id
    session_id = await ensure_session_id(message, state)
    if session_id:
        await begin_lab_count(message, state, conn, session_id, action='edit')


async def receive_lab_count(message, state, conn, subjects):
    from queue_bot.handlers.student import open_queue_screen
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
    try:
        if data['lab_action'] == 'join':
            position, total = queues.join_queue(conn, message.from_user.id, session_id,
                                                data.get('lab_position'), lab_count=count)
            note = f'✅ <b>Ви записалися</b>\n\nВаше місце: <b>{position}</b> із {total}.'
        else:
            queues.set_own_lab_count(conn, message.from_user.id, session_id, count)
            note = '✅ Кількість лабораторних оновлено. Ваше місце збережено.'
    except BotError:
        # A taken slot/closed queue needs a fresh choice, not an endless count prompt.
        await state.clear()
        await state.update_data(session_id=session_id)
        raise
    await state.clear()
    await state.update_data(session_id=session_id)
    await open_queue_screen(message, conn, subjects, session_id, note=note)
