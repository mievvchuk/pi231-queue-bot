"""Confirm actual laboratory identities when a queue entry has selected works."""
from aiogram.fsm.state import State, StatesGroup

from queue_bot.handlers.common import notify_next_students, show
from queue_bot.handlers.lab_picker import start_picker, process_picker, picker_content, picker_back
from queue_bot.services import queues, lab_catalog
from queue_bot.services.errors import BotError
from queue_bot.services.restrictions import require_available


class CompletionFlow(StatesGroup):
    count = State()


async def begin_completion(message, state, conn, session_id, *, telegram_id=None, subjects=None, end_consultation=False):
    from queue_bot.handlers.student import open_queue_screen
    uid = telegram_id if telegram_id is not None else message.from_user.id
    require_available(conn, uid)
    session = queues.get_session(conn, session_id)
    if not session or session['status'] == 'archived':
        raise BotError('Ця черга вже недоступна.')
    entry = next((r for r in queues.get_entries(conn, session_id) if r['telegram_id'] == uid), None)
    if entry is None:
        raise BotError('Ви не записані в цю чергу.')
    labs_available = bool(lab_catalog.labs_for_session(session))
    pending = lab_catalog.read_numbers(entry['selected_labs']) if labs_available else None
    consultation = session['queue_type'] == 'consultation' or session['subject_id'] == 7
    if end_consultation and not consultation:
        raise BotError('Ця дія доступна лише для консультації.')
    if not pending or end_consultation:
        old_position = entry['position']
        queues.finish_own_entry(conn, uid, session_id, passed=True)
        await state.clear()
        await state.update_data(session_id=session_id)
        await notify_next_students(message.bot, conn, subjects, session_id, removed_position=old_position)
        note = '✅ Здачу позначено. Вас знято з черги.' if labs_available and not consultation else '✅ Консультацію / запис завершено.'
        await open_queue_screen(message, conn, subjects, session_id, note=note, telegram_id=uid)
        return
    await state.update_data(session_id=session_id, passed_entry_id=entry['id'], passed_revision=entry['entry_revision'])
    if len(pending) == 1:
        await finish_selection(message, state, conn, subjects, pending, telegram_id=uid)
        return
    await state.set_state(CompletionFlow.count)
    await start_picker(message, state, session, prefix='labdone', allowed=pending, selected=[])


async def finish_selection(message, state, conn, subjects, numbers, *, telegram_id=None, bot=None):
    from queue_bot.handlers.student import open_queue_screen
    uid = telegram_id if telegram_id is not None else message.from_user.id
    data = await state.get_data()
    sid = data.get('session_id')
    if not sid or not data.get('passed_entry_id') or 'passed_revision' not in data:
        await state.clear()
        raise BotError('Потрібно знову обрати чергу.')
    old_position = queues.get_user_position(conn, uid, sid)
    try:
        remaining = queues.record_own_passed_lab_numbers(conn, uid, sid, numbers,
                          entry_id=data['passed_entry_id'], expected_revision=data['passed_revision'])
    finally:
        await state.clear()
        await state.update_data(session_id=sid)
    if remaining:
        note = (f'✅ Здано: {lab_catalog.format_numbers(numbers)}. '
                f'Залишилось: <b>{lab_catalog.format_numbers(remaining)}</b>.\n'
                'Якщо продовжуєте здавати, місце збережено. Коли закінчите, натисніть «Завершити підхід». Решта робіт перейде в кінець черги.')
    else:
        note = '✅ Усі вибрані лабораторні здані. Вас знято з черги.'
        await notify_next_students(bot or message.bot, conn, subjects, sid, removed_position=old_position)
    await open_queue_screen(message, conn, subjects, sid, note=note, telegram_id=uid)


async def receive_passed_labs(message, state, conn, subjects):
    data = await state.get_data()
    session = queues.get_session(conn, data.get('session_id'))
    try:
        text = message.text.strip()
        numbers = (data.get('picker_allowed', []) if text.casefold() in {'усі', 'всі', '✅ усі'} else
                   lab_catalog.parse_numbers(text, session, allowed=data.get('picker_allowed')))
    except BotError as error:
        await message.answer(str(error))
        return
    await finish_selection(message, state, conn, subjects, numbers)


async def passed_selection_callback(callback, state, conn, subjects):
    result = await process_picker(callback, state, CompletionFlow.count.state, 'labdone')
    if not result:
        return
    action, data = result
    if action == 'back':
        await picker_back(callback, state, conn, subjects)
    elif action == 'ok':
        await finish_selection(callback.message, state, conn, subjects, data['picker_selected'],
                               telegram_id=callback.from_user.id, bot=callback.bot)
    else:
        text, markup = picker_content(queues.get_session(conn, data['session_id']), data, 'labdone')
        await show(callback, text, markup)
