from datetime import datetime
import json
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from queue_bot.routing import create_dispatcher, ordered_routers
from queue_bot.services import queues, restrictions, users


@pytest.fixture
def app(conn, make_user):
    make_user(1, 'Петренко Іван')
    make_user(2, 'Коваль Андрій')
    sid = queues.create_session(conn, 100, 1, 1)
    for router in ordered_routers():
        router._parent_router = None
    subjects = [{'id': i, 'name': f'Предмет {i}', 'teachers': [{'id': 1, 'name': 'Викладач'}]} for i in range(1, 10)]
    dp = create_dispatcher(conn, subjects)
    bot = AsyncMock()
    bot.id = 5
    return dp, bot, sid


def key():
    return StorageKey(bot_id=5, chat_id=1, user_id=1)


async def context(app):
    return await app[0].storage.get_data(key())


async def send(app, *texts):
    if not await context(app):
        await app[0].storage.set_data(key(), {'session_id': app[2]})
    for text in texts:
        message = Message(message_id=1, date=datetime.now(), chat=Chat(id=1, type='private'),
                          from_user=User(id=1, is_bot=False, first_name='Іван'), text=text)
        await app[0].feed_update(app[1], Update(update_id=1, message=message))


async def click(app, prefix, action, *, nonce=None):
    nonce = nonce or (await context(app)).get('picker_nonce', 'missing')
    message = Message(message_id=10, date=datetime.now(), chat=Chat(id=1, type='private'),
                      from_user=User(id=5, is_bot=True, first_name='Бот'), text='Перелік')
    callback = CallbackQuery(id='pick', from_user=User(id=1, is_bot=False, first_name='Іван'),
                             chat_instance='private', message=message, data=f'{prefix}:{nonce}:{action}')
    await app[0].feed_update(app[1], Update(update_id=2, callback_query=callback))


@pytest.mark.asyncio
@pytest.mark.parametrize('initial,position', [(['📝 Стати в кінець'], 1), (['🔢 Обрати місце', '3'], 3)])
async def test_numbered_signup_asks_time_bounded_by_selected_labs(conn, app, initial, position):
    await send(app, *initial, '1, 2')
    assert queues.get_user_position(conn, 1, app[2]) is None
    await send(app, '81', '4')
    assert queues.get_user_position(conn, 1, app[2]) is None
    await send(app, '17')
    row = next(r for r in queues.get_entries(conn, app[2]) if r['telegram_id'] == 1)
    assert json.loads(row['selected_labs']) == [1, 2] and row['position'] == position
    assert row['planned_minutes'] == 17
    text = app[1].call_args[0][0].text
    assert '№1, №2' in text and '80 хв' in text and '17 хв' in text


@pytest.mark.asyncio
async def test_inline_multiselect_and_stale_buttons_do_not_mutate_new_draft(conn, app):
    await send(app, '📝 Стати в кінець')
    old_nonce = (await context(app))['picker_nonce']
    await click(app, 'labpick', 't:1')
    await click(app, 'labpick', 't:2')
    assert (await context(app))['picker_selected'] == [1, 2]
    await send(app, '🔙 Назад до черги', '📝 Стати в кінець')
    new_nonce = (await context(app))['picker_nonce']
    await click(app, 'labpick', 'ok', nonce=old_nonce)
    assert (await context(app))['picker_nonce'] == new_nonce
    assert queues.get_entries(conn, app[2]) == []
    await click(app, 'labpick', 't:2')
    await click(app, 'labpick', 'ok')
    await send(app, '40')
    assert json.loads(queues.get_entries(conn, app[2])[0]['selected_labs']) == [2]


@pytest.mark.asyncio
async def test_partial_numbered_completion_keeps_place_and_updates_limit(conn, app):
    conn.execute('UPDATE queue_sessions SET subject_id = 5 WHERE id = ?', (app[2],))
    await send(app, '📝 Стати в кінець', '1,2', '35', '✅ Здав')
    assert queues.get_user_position(conn, 1, app[2]) == 1
    await click(app, 'labdone', 't:1')
    await click(app, 'labdone', 'ok')
    row = queues.get_entries(conn, app[2])[0]
    assert row['position'] == 1 and json.loads(row['selected_labs']) == [2]
    assert row['planned_minutes'] == 10 and json.loads(row['passed_labs']) == [1]
    await send(app, '✅ Здав')
    assert queues.get_user_position(conn, 1, app[2]) is None
    assert queues.get_history(conn, app[2])[0]['labs_passed'] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('subject,qtype', [(7, 'lesson'), (7, 'consultation'), (6, 'lesson'), (1, 'consultation'), (9, 'lesson')])
async def test_consultation_and_excluded_subjects_skip_labs_and_time(conn, app, subject, qtype):
    conn.execute('UPDATE queue_sessions SET subject_id = ?, queue_type = ? WHERE id = ?', (subject, qtype, app[2]))
    await send(app, '📝 Стати в кінець')
    row = queues.get_entries(conn, app[2])[0]
    assert row['selected_labs'] is None and row['planned_minutes'] is None
    await send(app, '📍 Моє місце')
    method = app[1].call_args[0][0]
    assert 'ліміт' not in method.text and 'лаб.' not in method.text
    assert all('лаборатор' not in b.text and 'Час здачі' not in b.text for r in method.reply_markup.keyboard for b in r)
    await send(app, '✅ Здав')
    assert queues.get_user_position(conn, 1, app[2]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['selection', 'time', 'completion'])
@pytest.mark.parametrize('button', ['🔙 Назад до черги', '📍 Моє місце', '⚙️ Дії', '📅 Розклад', '🗳 Опитування'])
async def test_navigation_cancels_numbered_drafts(conn, app, stage, button):
    if stage == 'completion':
        queues.join_queue(conn, 1, app[2], lab_numbers=[1, 2], planned_minutes=20)
        await send(app, '✅ Здав')
    else:
        await send(app, '📝 Стати в кінець')
        if stage == 'time':
            await send(app, '1,2')
    await send(app, button, '1')
    rows = queues.get_entries(conn, app[2])
    if stage == 'completion':
        assert json.loads(rows[0]['selected_labs']) == [1, 2] and rows[0]['labs_passed'] == 0
    else:
        assert rows == []
    assert 'lab_action' not in await context(app) and 'picker_nonce' not in await context(app)


@pytest.mark.asyncio
async def test_invalid_numbers_edit_and_time_only_edit_keep_position(conn, app):
    await send(app, '📝 Стати в кінець', '8', '1,1', '0')
    assert queues.get_entries(conn, app[2]) == []
    await send(app, '1,2', '20', '🧪 Обрати лабораторні', '3,4', '15', '⏱ Час здачі', '5')
    row = queues.get_entries(conn, app[2])[0]
    assert json.loads(row['selected_labs']) == [3, 4] and row['planned_minutes'] == 5 and row['position'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['close', 'archive', 'ban'])
async def test_late_restriction_prevents_numbered_signup(conn, app, change):
    await send(app, '📝 Стати в кінець', '1,2')
    if change == 'close':
        queues.set_signup_open(conn, 100, app[2], False)
    elif change == 'archive':
        queues.finish_session(conn, 100, app[2])
    else:
        restrictions.set_restriction(conn, 100, users.get_user(conn, 1)['id'], days=1)
    await send(app, '10')
    assert queues.get_user_position(conn, 1, app[2]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [None, 1, 3])
async def test_unknown_labs_finish_without_questions_and_next_signup_asks(conn, app, count):
    queues.join_queue(conn, 1, app[2], lab_count=count, planned_minutes=20)
    await send(app, '✅ Здав')
    assert queues.get_user_position(conn, 1, app[2]) is None
    row = queues.get_history(conn, app[2])[0]
    assert row['selected_labs'] is None and row['labs_passed'] == 0
    assert row['status'] == 'completed'
    assert await context(app) == {'session_id': app[2]}
    assert await app[0].storage.get_state(key()) is None
    await send(app, '📝 Стати в кінець')
    assert queues.get_user_position(conn, 1, app[2]) is None
    assert (await context(app))['picker_allowed']
    await send(app, '2', '10')
    assert json.loads(queues.get_entries(conn, app[2])[0]['selected_labs']) == [2]


@pytest.mark.asyncio
@pytest.mark.parametrize('callback', [False, True])
async def test_single_known_lab_finishes_immediately_with_correct_history(conn, app, callback):
    queues.join_queue(conn, 1, app[2], lab_numbers=[2], planned_minutes=10)
    if callback:
        message = Message(message_id=1, date=datetime.now(), chat=Chat(id=1, type='private'),
                          from_user=User(id=5, is_bot=True, first_name='Бот'), text='Черга')
        event = CallbackQuery(id='single', from_user=User(id=1, is_bot=False, first_name='Іван'),
                              chat_instance='private', message=message, data=f'sd:{app[2]}')
        await app[0].feed_update(app[1], Update(update_id=2, callback_query=event))
    else:
        await send(app, '✅ Здав')
    assert queues.get_user_position(conn, 1, app[2]) is None
    history = queues.get_history(conn, app[2])[0]
    assert json.loads(history['passed_labs']) == [2] and history['labs_passed'] == 1
    assert json.loads(history['selected_labs']) == []
    assert await context(app) == {'session_id': app[2]}
    assert await app[0].storage.get_state(key()) is None


@pytest.mark.asyncio
async def test_full_first_ten_still_waits_for_numbered_selection(conn, app, make_user):
    for uid, surname in enumerate(['Мельник', 'Романюк', 'Кравчук', 'Савчук', 'Шевчук',
                                   'Бойко', 'Ткачук', 'Поліщук', 'Ковальчук'], start=3):
        make_user(uid, surname + ' Іван')
    for uid in range(2, 12):
        queues.join_queue(conn, uid, app[2])
    await send(app, '🔢 Обрати місце', '1,2')
    assert queues.get_user_position(conn, 1, app[2]) is None
    await send(app, '30')
    row = queues.get_entries(conn, app[2])[-1]
    assert row['position'] == 11 and json.loads(row['selected_labs']) == [1, 2]


@pytest.mark.asyncio
async def test_legacy_passed_callback_selects_exact_labs_and_preserves_actor(conn, app):
    queues.join_queue(conn, 1, app[2], lab_numbers=[1, 2], planned_minutes=20)
    message = Message(message_id=1, date=datetime.now(), chat=Chat(id=1, type='private'),
                      from_user=User(id=5, is_bot=True, first_name='Бот'), text='Стара черга')
    callback = CallbackQuery(id='old', from_user=User(id=1, is_bot=False, first_name='Іван'),
                             chat_instance='private', message=message, data=f'sd:{app[2]}')
    await app[0].feed_update(app[1], Update(update_id=2, callback_query=callback))
    assert queues.get_user_position(conn, 1, app[2]) == 1
    await send(app, '3')  # In the subject, but not in this student's entry.
    assert json.loads(queues.get_entries(conn, app[2])[0]['selected_labs']) == [1, 2]
    await send(app, '1')
    assert json.loads(queues.get_entries(conn, app[2])[0]['selected_labs']) == [2]


@pytest.mark.asyncio
async def test_time_only_edit_respects_remaining_limit(conn, app):
    queues.join_queue(conn, 1, app[2], lab_numbers=[2], planned_minutes=20)
    await send(app, '⏱ Час здачі', '41', '4', '5.5', 'abc')
    assert queues.get_entries(conn, app[2])[0]['planned_minutes'] == 20
    await send(app, '5')
    row = queues.get_entries(conn, app[2])[0]
    assert row['planned_minutes'] == 5 and json.loads(row['selected_labs']) == [2] and row['position'] == 1
