from aiogram.fsm.storage.base import StorageKey
import pytest

from queue_bot.services import admins, attendance, queues
from tests.test_mini_app import client, headers
from tests.test_navigation_bans import app, send


@pytest.mark.asyncio
async def test_signup_is_permanent_and_legacy_close_finish_requests_do_not_change_entries(client, conn, monkeypatch):
    clock = [10000]
    monkeypatch.setattr(attendance, 'now_seconds', lambda: clock[0])
    sid = queues.create_session(conn, 100, 1, 1)
    # Existing closed flags are compatibility data, never an access restriction.
    conn.execute("UPDATE queue_sessions SET status = 'closed' WHERE id = ?", (sid,))
    url = f'/api/miniapp/queues/{sid}'
    assert (await client.post(url+'/join', headers=headers(2), json={'labs':[1],'minutes':10})).status == 200
    entry = dict(queues._own_active_entry(conn, 2, sid))
    attendance.start(conn, 100, sid, minutes=10)
    clock[0] += 601
    attendance.tick(conn)
    assert attendance.active_run(conn, sid) is None
    assert dict(queues.get_entry(conn, entry['id'])) == entry
    assert (await client.post(url+'/join', headers=headers(1), json={'labs':[1],'minutes':10})).status == 200
    assert (await (await client.get(url, headers=headers(1))).json())['status'] == 'open'
    before = [dict(row) for row in queues.get_entries(conn, sid)]
    for action in ('open', 'close'):
        assert (await client.post(f'/api/miniapp/admin/queues/{sid}?mode=admin', headers=headers(100), json={'action':action})).status == 400
    assert (await client.post('/api/miniapp/admin/manage/history?mode=admin', headers=headers(100),
        json={'action':'finish','session_id':sid,'create_new':True})).status == 400
    assert before == [dict(row) for row in queues.get_entries(conn, sid)]
    assert conn.execute('SELECT COUNT(*) FROM queue_sessions').fetchone()[0] == 1


@pytest.mark.asyncio
async def test_mode_buttons_are_on_main_screen_for_every_admin_and_create_is_inside_queues(conn, app, make_user):
    make_user(2, 'Адмін Другий')
    admins.add_admin(conn, 100, 2)
    for uid in (100, 2):
        await send(app, '⚡ Режим адміна', uid=uid)
        buttons = [b.text for r in app[1].call_args[0][0].reply_markup.keyboard for b in r]
        assert buttons.index('👤 Режим студента') < buttons.index('Ще для адміна')
        await send(app, 'Ще для адміна', uid=uid)
        assert '👤 Режим студента' not in [b.text for r in app[1].call_args[0][0].reply_markup.keyboard for b in r]
        await send(app, '👤 Режим студента', uid=uid)
        buttons = [b.text for r in app[1].call_args[0][0].reply_markup.keyboard for b in r]
        assert {'⚡ Режим адміна','❓ Допомога','🐞 Повідомити про баг'} <= set(buttons)
        assert '➕ Створити чергу' not in buttons
        await send(app, 'Черги', uid=uid)
        assert '➕ Створити чергу' in [b.text for r in app[1].call_args[0][0].reply_markup.keyboard for b in r]
    await send(app, '⚡ Режим адміна', uid=1)
    assert not str(await app[0].storage.get_state(StorageKey(bot_id=5,chat_id=1,user_id=1))).startswith('Admin')


@pytest.mark.asyncio
async def test_help_and_cancelled_bug_report_return_to_admin_main(conn, app):
    await send(app, '⚡ Адмін-панель')
    for feature, back in (('❓ Допомога','🔙 Назад'), ('🐞 Повідомити про баг','🔙 Скасувати')):
        await send(app, feature)
        await send(app, back)
        buttons = [b.text for r in app[1].call_args[0][0].reply_markup.keyboard for b in r]
        assert '👤 Режим студента' in buttons and 'Ще для адміна' in buttons


@pytest.mark.asyncio
async def test_old_telegram_finish_and_close_buttons_cannot_end_permanent_queue(conn, app):
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid)
    before = dict(queues.get_entries(conn, sid)[0])
    for text in ('🔒 Закрити запис','📦 Завершити чергу','📦 Завершити й архівувати','📦 Нова черга (порожня)'):
        await send(app, text)
        assert queues.get_session(conn, sid)['status'] == 'open'
        assert dict(queues.get_entries(conn, sid)[0]) == before
    assert conn.execute('SELECT COUNT(*) FROM queue_sessions').fetchone()[0] == 1
