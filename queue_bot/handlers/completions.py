"""Confirm the actual number of labs passed, retaining partially completed entries."""
from aiogram.fsm.state import State, StatesGroup

from queue_bot.handlers.common import make_reply_keyboard, notify_next_students
from queue_bot.services import queues
from queue_bot.services.errors import BotError
from queue_bot.services.lab_estimates import MAX_LAB_COUNT
from queue_bot.services.restrictions import require_available

FINISH_UNKNOWN = '✅ Завершити без кількості'


class CompletionFlow(StatesGroup):
    count = State()


async def begin_completion(message, state, conn, session_id, *, telegram_id=None):
    uid = telegram_id if telegram_id is not None else message.from_user.id
    require_available(conn, uid)
    session = queues.get_session(conn, session_id)
    if not session or session['status'] == 'archived':
        raise BotError('Ця черга вже недоступна.')
    entry = next((row for row in queues.get_entries(conn, session_id) if row['telegram_id'] == uid), None)
    if entry is None:
        raise BotError('Ви не записані в цю чергу.')
    count = entry['lab_count']
    await state.set_state(CompletionFlow.count)
    await state.update_data(session_id=session_id, passed_entry_id=entry['id'], passed_expected_count=count)
    rows = []
    if count is not None:
        rows.append([f'✅ Усі ({count})'])
    numbers = list(range(1, min(count - 1 if count else MAX_LAB_COUNT, 4) + 1))
    if numbers:
        rows.append([str(number) for number in numbers])
    if count is None:
        rows.append([FINISH_UNKNOWN])
    rows.append(['🔙 Назад до черги'])
    explanation = (f'У вашому записі залишилось <b>{count}</b> лабораторних. '
                   'Оберіть «Усі», число або введіть кількість.\n\n'
                   'Якщо здали частину, решта залишиться на тому самому місці.'
                   if count is not None else
                   'Заплановану кількість не вказано. Введіть, скільки здали (1–20), '
                   'або завершіть без кількості. Після підтвердження вас буде знято з черги.')
    await message.answer('✅ <b>Скільки лабораторних здали?</b>\n\n' + explanation,
                         reply_markup=make_reply_keyboard(rows))


async def receive_passed_labs(message, state, conn, subjects):
    from queue_bot.handlers.student import open_queue_screen
    data = await state.get_data()
    sid = data.get('session_id')
    if not sid or not data.get('passed_entry_id'):
        await state.clear()
        raise BotError('Потрібно знову обрати чергу.')
    planned = data.get('passed_expected_count')
    text = message.text.strip()
    if planned is not None and text == f'✅ Усі ({planned})':
        count = planned
    elif planned is None and text == FINISH_UNKNOWN:
        count = None
    elif text.isascii() and text.isdigit() and len(text) <= 2 and 1 <= int(text) <= (planned or MAX_LAB_COUNT):
        count = int(text)
    else:
        await message.answer(f'Вкажіть ціле число від 1 до {planned or MAX_LAB_COUNT} або оберіть кнопку.')
        return
    old_position = queues.get_user_position(conn, message.from_user.id, sid)
    try:
        remaining = queues.record_own_passed_labs(conn, message.from_user.id, sid, count,
                                                  entry_id=data['passed_entry_id'], expected_count=planned)
    finally:
        await state.clear()
        await state.update_data(session_id=sid)
    if remaining:
        note = (f'✅ Зараховано: <b>{count}</b> лаб. Залишилось: <b>{remaining}</b>.\n'
                'Ваше місце збережено. За потреби оновіть «⏱ Час здачі».')
    else:
        note = '✅ <b>Здачу підтверджено. Вас знято з черги.</b>'
        await notify_next_students(message.bot, conn, subjects, sid, removed_position=old_position)
    await open_queue_screen(message, conn, subjects, sid, note=note)
