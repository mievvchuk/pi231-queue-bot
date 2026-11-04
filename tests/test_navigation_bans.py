from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Dispatcher
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Chat, Message, Update, User

from queue_bot.handlers import admin, bans, navigation, start, student
from queue_bot.handlers.common import Guard
from queue_bot.services import queues, users


@pytest.fixture
def app(conn, make_user):
    make_user(100, 'Адмін Власник')
    make_user(1, 'Петренко Іван')
    subjects = [{'id': 1, 'name': 'Предмет', 'teachers': [{'id': 1, 'name': 'Викладач'}]}]
    for r in (navigation.router, bans.router, start.router, admin.router, student.router, start.fallback_router):
        r._parent_router = None
    dp = Dispatcher(conn=conn, subjects=subjects)
    dp.message.outer_middleware(Guard())
    dp.callback_query.outer_middleware(Guard())
    dp.include_routers(navigation.router, bans.router, start.router, admin.router, student.router, start.fallback_router)
    bot = AsyncMock()
    bot.id = 5
    return dp, bot


async def send(app, text, uid=100):
    dp, bot = app
    msg = Message(message_id=1, date=datetime.now(), chat=Chat(id=uid, type='private'),
                  from_user=User(id=uid, is_bot=False, first_name='Тест'), text=text)
    await dp.feed_update(bot, Update(update_id=1, message=msg))


async def data(app, uid=100):
    return await app[0].storage.get_data(StorageKey(bot_id=5, chat_id=uid, user_id=uid))


@pytest.mark.asyncio
@pytest.mark.parametrize('uid,owner', [(100, True), (2, False)])
async def test_compact_admin_menu_keeps_secondary_actions_and_owner_access(conn, app, make_user, uid, owner):
    from queue_bot.services import admins
    if not owner:
        make_user(uid, 'Адмін Другий')
        admins.add_admin(conn, 100, uid)
    await send(app, '⚡ Адмін-панель', uid=uid)
    buttons = lambda: [b.text for row in app[1].call_args[0][0].reply_markup.keyboard for b in row]
    assert buttons() == ['📋 Черги та студенти', '📅 Розклад і заміни', 'Ще для адміна']
    await send(app, 'Ще для адміна', uid=uid)
    assert ('⚙️ Налаштування' in buttons()) is owner
    assert '🗳 Опитування' in buttons() and '👥 Список групи' in buttons()
    await send(app, '🔙 В адмін-панель', uid=uid)
    assert len(buttons()) == 3
    await send(app, 'Ще для адміна', uid=uid)
    await send(app, '👤 Режим студента', uid=uid)
    assert buttons() == ['Черги', '📅 Розклад', 'Ще']


@pytest.mark.asyncio
async def test_student_cannot_open_admin_more_by_typing_button_text(conn, app):
    before = len(app[1].call_args_list)
    await send(app, 'Ще для адміна', uid=1)
    assert (await data(app, uid=1)).get('ui_mode') != 'admin'
    assert not any('🗳 Опитування' in str(call) or '⚙️ Налаштування' in str(call)
                   for call in app[1].call_args_list[before:])


@pytest.mark.asyncio
async def test_queue_mode_roundtrip_discards_draft_preserves_queue(conn, app):
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid)
    key = StorageKey(bot_id=5, chat_id=100, user_id=100)
    await app[0].storage.set_state(key, admin.AdminOrderState.entering_name)
    await app[0].storage.set_data(key, {'session_id': sid, 'pending_name': 'Чернетка'})
    await send(app, '👤 Режим студента')
    assert (await data(app)) == {'session_id': sid, 'ui_mode': 'student'}
    assert '[ID:' not in app[1].call_args[0][0].text
    await send(app, '⚡ Режим адміна')
    assert (await data(app)) == {'session_id': sid, 'ui_mode': 'admin'}
    assert '[ID: 1]' in app[1].call_args[0][0].text
    assert (await app[0].storage.get_state(key)).startswith('Admin')
    await send(app, '👤 Режим студента')
    assert '[ID:' not in app[1].call_args[0][0].text
    await send(app, '⚡ Режим адміна')
    assert '[ID: 1]' in app[1].call_args[0][0].text
    await send(app, '🔙 Назад')
    assert (await app[0].storage.get_state(key)).startswith('Admin')


@pytest.mark.asyncio
async def test_non_admin_cannot_enter_admin_mode(app):
    await send(app, '⚡ Режим адміна', uid=1)
    assert (await data(app, 1)).get('ui_mode') != 'admin'


@pytest.mark.asyncio
async def test_archived_queue_switch_recovers_to_menu(conn, app):
    sid = queues.create_session(conn, 100, 1, 1)
    queues.finish_session(conn, 100, sid)
    key = StorageKey(bot_id=5, chat_id=100, user_id=100)
    await app[0].storage.set_data(key, {'session_id': sid})
    await send(app, '👤 Режим студента')
    assert 'session_id' not in await data(app)
    assert 'Головне меню' in app[1].call_args[0][0].text


@pytest.mark.asyncio
async def test_ban_wizard_validates_confirms_and_removes_student(conn, app):
    user = users.get_user(conn, 1)
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid)
    key = StorageKey(bot_id=5, chat_id=100, user_id=100)
    await app[0].storage.set_state(key, admin.AdminUserState.viewing_card)
    await app[0].storage.set_data(key, {'current_user_id': user['id']})
    await send(app, '⏳ Бан на N днів')
    await send(app, '0')
    assert not users.get_user(conn, 1)['is_blocked']
    await send(app, '3')
    assert not users.get_user(conn, 1)['is_blocked']
    await send(app, '✅ Підтвердити бан')
    assert users.get_user(conn, 1)['banned_until']
    assert queues.get_user_position(conn, 1, sid) is None
    assert (await app[0].storage.get_state(key)) == admin.AdminUserState.viewing_card.state


@pytest.mark.asyncio
async def test_cancel_ban_and_forbidden_ban(conn, app):
    key = StorageKey(bot_id=5, chat_id=100, user_id=100)
    await app[0].storage.set_data(key, {'current_user_id': users.get_user(conn, 1)['id']})
    await send(app, '⏳ Бан на N днів')
    await send(app, '🔙 Скасувати бан')
    assert not users.get_user(conn, 1)['is_blocked']
    await send(app, '⏳ Бан на N днів', uid=1)
    assert (await data(app, 1)).get('ban_user_id') is None


@pytest.mark.asyncio
async def test_home_keeps_current_mode(conn, app):
    await send(app, '⚡ Адмін-панель')
    await send(app, '🏠 Головне меню')
    assert (await data(app))['ui_mode'] == 'admin'
    await send(app, '👤 Режим студента')
    await send(app, '🏠 Головне меню')
    assert (await data(app))['ui_mode'] == 'student'


@pytest.mark.asyncio
async def test_user_card_opens_and_ban_button_is_visible(conn, app):
    user = users.get_user(conn, 1)
    await send(app, f'👤 #{user["id"]} Петренко Іван')
    method = app[1].call_args[0][0]
    assert '⏳ Бан на N днів' in [b.text for row in method.reply_markup.keyboard for b in row]


@pytest.mark.asyncio
async def test_admin_selected_queue_stays_admin_on_refresh(conn, app):
    queues.create_session(conn, 100, 1, 1)
    await send(app, '👀 Активні черги')
    keyboard = app[1].call_args[0][0].reply_markup.keyboard
    label = next(b.text for row in keyboard for b in row if b.text.startswith('📋 '))
    await send(app, label)
    key = StorageKey(bot_id=5, chat_id=100, user_id=100)
    assert (await app[0].storage.get_state(key) or '').startswith('Admin')
    await send(app, '🔄 Оновити')
    assert 'Режим адміна' in app[1].call_args[0][0].text


@pytest.mark.asyncio
async def test_cancel_switch_during_queue_ban_preserves_selected_queue(conn, app):
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid)
    entry = queues.get_entries(conn, sid)[0]
    key = StorageKey(bot_id=5, chat_id=100, user_id=100)
    await app[0].storage.set_state(key, admin.AdminOrderState.managing_entry)
    await app[0].storage.set_data(key, {'session_id': sid, 'entry_id': entry['id']})
    await send(app, '⏳ Бан на N днів')
    await send(app, '👤 Режим студента')
    assert (await data(app)) == {'session_id': sid, 'ui_mode': 'student'}
    assert not users.get_user(conn, 1)['is_blocked']


@pytest.mark.asyncio
async def test_stale_unblock_card_never_installs_permanent_ban(conn, app, monkeypatch):
    from datetime import timedelta, timezone
    from queue_bot.services import restrictions
    now = datetime.now(timezone.utc)
    user = users.get_user(conn, 1)
    restrictions.set_restriction(conn, 100, user['id'], days=1, now=now)
    key = StorageKey(bot_id=5, chat_id=100, user_id=100)
    await app[0].storage.set_state(key, admin.AdminUserState.viewing_card)
    await app[0].storage.set_data(key, {'current_user_id': user['id']})
    monkeypatch.setattr(restrictions, 'utc_now', lambda: now + timedelta(days=2))
    await send(app, '🟢 Розблокувати')
    assert not users.get_user(conn, 1)['is_blocked']
