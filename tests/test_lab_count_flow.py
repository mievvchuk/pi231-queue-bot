from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from queue_bot.routing import create_dispatcher, ordered_routers
from queue_bot.services import queues, restrictions, users


@pytest.fixture
def lab_app(conn, make_user):
    make_user(1, 'Петренко Іван')
    make_user(2, 'Коваль Андрій')
    sid = queues.create_session(conn, 100, 1, 1)
    subjects = [{'id': 1, 'name': 'Предмет', 'teachers': [{'id': 1, 'name': 'Викладач'}]}]
    for router in ordered_routers():
        router._parent_router = None
    dp = create_dispatcher(conn, subjects)
    bot = AsyncMock()
    bot.id = 5
    return dp, bot, sid


async def choose(lab_app, *texts):
    dp, bot, sid = lab_app
    key = StorageKey(bot_id=5, chat_id=1, user_id=1)
    if not await dp.storage.get_data(key):
        await dp.storage.set_data(key, {'session_id': sid})
    for text in texts:
        msg = Message(message_id=1, date=datetime.now(), chat=Chat(id=1, type='private'),
                      from_user=User(id=1, is_bot=False, first_name='Іван'), text=text)
        await dp.feed_update(bot, Update(update_id=1, message=msg))


@pytest.mark.asyncio
@pytest.mark.parametrize('start_texts,position', [(['📝 Стати в кінець'], 1), (['🔢 Обрати місце', '3'], 3)])
async def test_join_waits_for_count_and_duration_then_renders_student_estimate(conn, lab_app, start_texts, position):
    await choose(lab_app, *start_texts)
    assert queues.get_user_position(conn, 1, lab_app[2]) is None
    await choose(lab_app, '2')
    assert queues.get_user_position(conn, 1, lab_app[2]) is None
    await choose(lab_app, '17')
    rows = queues.get_entries(conn, lab_app[2])
    own = next(r for r in rows if r['telegram_id'] == 1)
    assert own['position'] == position and own['lab_count'] == 2 and own['planned_minutes'] == 17
    assert '2 лаб. · ≈17 хв' in lab_app[1].call_args[0][0].text


@pytest.mark.asyncio
async def test_cancel_join_does_not_reserve_place(conn, lab_app):
    await choose(lab_app, '📝 Стати в кінець', '🔙 Назад до черги')
    assert queues.get_entries(conn, lab_app[2]) == []
    key = StorageKey(bot_id=5, chat_id=1, user_id=1)
    assert 'lab_action' not in await lab_app[0].storage.get_data(key)


@pytest.mark.asyncio
async def test_edit_invalid_count_does_not_change_position(conn, lab_app):
    sid = lab_app[2]
    queues.join_queue(conn, 1, sid)
    await choose(lab_app, '📍 Моє місце')
    markup = lab_app[1].call_args[0][0].reply_markup
    assert any(b.text == '🧪 Кількість лабораторних' for row in markup.keyboard for b in row)
    await choose(lab_app, '🧪 Кількість лабораторних', '0', '21', 'abc')
    assert queues.get_entries(conn, sid)[0]['lab_count'] is None
    await choose(lab_app, '3')
    assert queues.get_entries(conn, sid)[0]['lab_count'] is None
    await choose(lab_app, '15')
    row = queues.get_entries(conn, sid)[0]
    assert row['position'] == 1 and row['lab_count'] == 3
    await choose(lab_app, '🧪 Кількість лабораторних', '➖ Не вказувати', '5')
    assert queues.get_entries(conn, sid)[0]['lab_count'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['close', 'archive', 'ban'])
async def test_late_restriction_during_count_prompt_prevents_join(conn, lab_app, change):
    sid = lab_app[2]
    await choose(lab_app, '📝 Стати в кінець', '2')
    if change == 'close':
        queues.set_signup_open(conn, 100, sid, False)
    elif change == 'archive':
        queues.finish_session(conn, 100, sid)
    else:
        restrictions.set_restriction(conn, 100, users.get_user(conn, 1)['id'], days=1)
    await choose(lab_app, '10')
    assert queues.get_user_position(conn, 1, sid) is None


@pytest.mark.asyncio
async def test_taken_specific_position_during_prompt_never_shifts_another_student(conn, lab_app):
    sid = lab_app[2]
    await choose(lab_app, '🔢 Обрати місце', '1')
    queues.join_queue(conn, 2, sid, 1)
    await choose(lab_app, '2', '10')
    assert queues.get_user_position(conn, 1, sid) is None
    assert queues.get_user_position(conn, 2, sid) == 1


@pytest.mark.asyncio
async def test_full_first_ten_still_prompts_before_appending(conn, lab_app, make_user):
    for uid, surname in enumerate(['Мельник', 'Романюк', 'Кравчук', 'Савчук', 'Шевчук',
                                   'Бойко', 'Ткачук', 'Поліщук', 'Ковальчук'], start=3):
        make_user(uid, surname + ' Іван')
    for uid in range(2, 12):
        queues.join_queue(conn, uid, lab_app[2])
    await choose(lab_app, '🔢 Обрати місце')
    assert queues.get_user_position(conn, 1, lab_app[2]) is None
    await choose(lab_app, '20', '30')
    assert queues.get_user_position(conn, 1, lab_app[2]) == 11
    assert queues.get_entries(conn, lab_app[2])[-1]['lab_count'] == 20


@pytest.mark.asyncio
@pytest.mark.parametrize('button', ['🔄 Оновити', '📍 Моє місце', '⚙️ Дії', '📅 Розклад', '🗳 Опитування'])
@pytest.mark.parametrize('editing', [False, True])
@pytest.mark.parametrize('duration_stage', [False, True])
async def test_queue_navigation_discards_pending_count(conn, lab_app, button, editing, duration_stage):
    sid = lab_app[2]
    if editing:
        queues.join_queue(conn, 1, sid, lab_count=2)
    await choose(lab_app, '🧪 Кількість лабораторних' if editing else '📝 Стати в кінець')
    if duration_stage:
        await choose(lab_app, '3')
    await choose(lab_app, button, '10')
    rows = queues.get_entries(conn, sid)
    if editing:
        assert rows[0]['lab_count'] == 2
    else:
        assert rows == []
    key = StorageKey(bot_id=5, chat_id=1, user_id=1)
    assert 'lab_action' not in await lab_app[0].storage.get_data(key)
    assert 'lab_count' not in await lab_app[0].storage.get_data(key)


@pytest.mark.asyncio
async def test_open_queue_command_is_not_swallowed_by_count_prompt(conn, lab_app):
    await choose(lab_app, '📝 Стати в кінець', '➕ Відкрити чергу')
    key = StorageKey(bot_id=5, chat_id=1, user_id=1)
    assert await lab_app[0].storage.get_state(key) == 'StudentFlow:creating_queue_type'
    assert 'lab_action' not in await lab_app[0].storage.get_data(key)
    assert queues.get_user_position(conn, 1, lab_app[2]) is None


@pytest.mark.asyncio
async def test_minimum_five_minutes_required_even_with_unknown_quantity(conn, lab_app):
    await choose(lab_app, '📝 Стати в кінець', '➖ Не вказувати', '4', '0', '-5', '5.5', 'abc')
    assert queues.get_user_position(conn, 1, lab_app[2]) is None
    await choose(lab_app, '5')
    row = queues.get_entries(conn, lab_app[2])[0]
    assert row['lab_count'] is None and row['planned_minutes'] == 5
    assert '≈5 хв' in lab_app[1].call_args[0][0].text


@pytest.mark.asyncio
async def test_time_only_edit_preserves_quantity_and_position(conn, lab_app):
    sid = lab_app[2]
    queues.join_queue(conn, 1, sid, lab_count=2, planned_minutes=20)
    await choose(lab_app, '⏱ Час здачі', '4')
    assert queues.get_entries(conn, sid)[0]['planned_minutes'] == 20
    await choose(lab_app, '5')
    row = queues.get_entries(conn, sid)[0]
    assert row['lab_count'] == 2 and row['position'] == 1 and row['planned_minutes'] == 5


@pytest.mark.asyncio
async def test_passed_button_asks_then_partial_stays_and_all_removes(conn, lab_app):
    sid = lab_app[2]
    queues.join_queue(conn, 1, sid, lab_count=3, planned_minutes=20)
    await choose(lab_app, '✅ Здав')
    assert queues.get_user_position(conn, 1, sid) == 1
    buttons = [b.text for row in lab_app[1].call_args[0][0].reply_markup.keyboard for b in row]
    assert '1' in buttons and '✅ Усі (3)' in buttons
    await choose(lab_app, '4', '0')
    assert queues.get_entries(conn, sid)[0]['lab_count'] == 3
    await choose(lab_app, '1')
    current = queues.get_entries(conn, sid)[0]
    assert current['lab_count'] == 2 and current['position'] == 1 and current['labs_passed'] == 1
    await choose(lab_app, '✅ Здав', '✅ Усі (2)')
    assert queues.get_user_position(conn, 1, sid) is None
    assert queues.get_history(conn, sid)[0]['labs_passed'] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('button', ['🔙 Назад до черги', '📍 Моє місце', '⚙️ Дії', '📅 Розклад'])
async def test_navigating_away_cancels_completion(conn, lab_app, button):
    queues.join_queue(conn, 1, lab_app[2], lab_count=3)
    await choose(lab_app, '✅ Здав', button, '1')
    row = queues.get_entries(conn, lab_app[2])[0]
    assert row['lab_count'] == 3 and row['labs_passed'] == 0 and row['position'] == 1


@pytest.mark.asyncio
async def test_legacy_passed_callback_asks_for_actual_quantity(conn, lab_app):
    sid = lab_app[2]
    queues.join_queue(conn, 1, sid, lab_count=3)
    msg = Message(message_id=1, date=datetime.now(), chat=Chat(id=1, type='private'),
                  from_user=User(id=5, is_bot=True, first_name='Бот'), text='Стара черга')
    callback = CallbackQuery(id='old-pass', from_user=User(id=1, is_bot=False, first_name='Іван'),
                             chat_instance='private', message=msg, data=f'sd:{sid}')
    await lab_app[0].feed_update(lab_app[1], Update(update_id=2, callback_query=callback))
    assert queues.get_user_position(conn, 1, sid) == 1
    await choose(lab_app, '1')
    row = queues.get_entries(conn, sid)[0]
    assert row['lab_count'] == 2 and row['labs_passed'] == 1
