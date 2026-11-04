from unittest.mock import AsyncMock

import pytest

from tests.test_mini_app import client, headers
from queue_bot.handlers import attendance as handlers
from queue_bot.services import attendance as a, queues
from queue_bot.services.lesson_notifier import deliver


@pytest.mark.asyncio
async def test_mini_app_starts_duration_extends_and_rejects_expired_confirmation(client, conn, monkeypatch):
    clock = [10000]
    monkeypatch.setattr(a, 'now_seconds', lambda: clock[0])
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_numbers=[1, 2], planned_minutes=20)
    a.start(conn, 100, sid)
    url = f'/api/miniapp/queues/{sid}'
    async def detail():
        response = await client.get(url, headers=headers())
        return await response.json()
    async def respond(action, minutes=None, uid=1):
        token = (await detail())['lesson']['own']['token']
        return await client.post(url+'/attendance', headers=headers(uid), json={'token':token,'response':action,'minutes':minutes})
    assert (await respond('present')).status == 200
    assert (await respond('ready')).status == 200
    data = await detail()
    assert data['lesson']['own']['turn_due'] == 11200
    assert 'telegram_id' not in str(data['lesson'])
    clock[0] = 11200
    a.tick(conn)
    await deliver(AsyncMock(), conn, [])
    assert (await detail())['lesson']['own']['deadline'] == 11800
    assert (await respond('extend', 5, uid=2)).status == 400
    assert (await respond('extend', 5)).status == 200
    assert (await detail())['lesson']['own']['turn_due'] == 11500
    clock[0] = 11500
    a.tick(conn)
    await deliver(AsyncMock(), conn, [])
    clock[0] = 12100
    assert (await respond('extend', 5)).status == 400
    data = await detail()
    entry = data['entries'][0]
    result = await client.post(url+'/complete', headers=headers(), json={'entry_id':entry['id'],'revision':entry['entry_revision'],'labs':[1,2]})
    assert result.status == 400


@pytest.mark.asyncio
async def test_bot_completion_question_does_not_grade_and_extension_uses_student_input(conn, make_user, monkeypatch):
    monkeypatch.setattr(a, 'now_seconds', lambda: 10000)
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_numbers=[1, 2], planned_minutes=20)
    a.start(conn, 100, sid)
    def token():
        return a.view(conn, sid, 1)['own']['token']
    a.respond(conn, 1, token(), 'present')
    a.respond(conn, 1, token(), 'ready')
    callback = AsyncMock()
    callback.from_user.id = 1
    state = AsyncMock()
    callback.data = f'att:{token()}:review'
    await handlers.attendance_answer(callback, state, conn, [])
    assert not queues.get_history(conn, sid)
    assert queues.get_entries(conn, sid)[0]['passed_labs'] == '[]'
    callback.data = f'att:{token()}:extend'
    await handlers.attendance_answer(callback, state, conn, [])
    state.set_state.assert_awaited_with(handlers.TurnTime.minutes)
    state.get_data.return_value = {'turn_token':token(),'turn_action':'extend'}
    message = AsyncMock()
    message.from_user.id = 1
    message.text = '7'
    await handlers.turn_time(message, state, conn, [])
    assert a.view(conn, sid, 1)['own']['turn_due'] == 10420
