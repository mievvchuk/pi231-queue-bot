from unittest.mock import AsyncMock

import pytest

from tests.test_mini_app import client, headers
from queue_bot.services import attendance as a, queues
from queue_bot.services.lesson_notifier import deliver
from queue_bot.handlers import attendance as handlers


@pytest.mark.asyncio
async def test_mini_app_and_bot_share_attendance_turn_and_partial_completion(client, conn):
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_numbers=[1, 2], planned_minutes=15)
    queues.join_queue(conn, 2, sid, lab_numbers=[1], planned_minutes=10)
    url = f'/api/miniapp/queues/{sid}'
    admin_url = f'/api/miniapp/admin/queues/{sid}?mode=admin'
    assert (await client.post(admin_url, headers=headers(1), json={'action':'start-lesson'})).status == 403
    result = await client.post(admin_url, headers=headers(100), json={'action':'start-lesson','minutes':80})
    assert result.status == 200
    detail = await (await client.get(url, headers=headers(1))).json()
    assert detail['lesson']['response_minutes'] == 10
    assert 'telegram_id' not in str(detail['lesson'])
    token = detail['lesson']['own']['token']
    assert (await client.post(url+'/attendance', headers=headers(2), json={'token':token,'response':'present'})).status == 400
    assert (await client.post(url+'/attendance', headers=headers(1), json={'token':token,'response':'late','minutes':5})).status == 200
    token = a.view(conn, sid, 1)['own']['token']
    callback = AsyncMock()
    callback.from_user.id = 1
    callback.data = f'att:{token}:present'
    await handlers.attendance_answer(callback, AsyncMock(), conn, [])
    callback.answer.assert_any_await('Відповідь збережено')
    assert a.view(conn, sid, 1)['own']['state'] == 'called'
    token = a.view(conn, sid, 1)['own']['token']
    result = await client.post(url+'/attendance', headers=headers(1), json={'token':token,'response':'ready'})
    assert result.status == 200
    entry = queues.get_entries(conn, sid)[0]
    reference = {'entry_id':entry['id'], 'revision':entry['entry_revision']}
    result = await client.post(url+'/complete', headers=headers(1), json={**reference, 'labs':[1]})
    assert result.status == 200
    entry = queues.get_entry(conn, entry['id'])
    result = await client.post(url+'/finish-turn', headers=headers(1), json={'entry_id':entry['id'], 'revision':entry['entry_revision']})
    assert result.status == 200
    assert queues.get_user_position(conn, 1, sid) == 2
    assert queues.get_entry(conn, entry['id'])['selected_labs'] == '[2]'
    assert (await client.post(admin_url, headers=headers(100), json={'action':'stop-lesson'})).status == 200
    assert (await (await client.get(url, headers=headers(1))).json())['lesson'] is None


@pytest.mark.asyncio
async def test_legacy_finish_token_cannot_finish_someone_elses_approach(conn, make_user):
    make_user(1, 'Петренко Іван')
    make_user(2, 'Коваль Андрій')
    sid = queues.create_session(conn, 100, 1, 1)
    for uid in (1, 2):
        queues.join_queue(conn, uid, sid)
    entry = queues.get_entries(conn, sid)[0]
    callback = AsyncMock()
    callback.from_user.id = 2
    callback.data = f'turn:{entry["id"]}:{entry["entry_revision"]}'
    await handlers.finish_turn(callback, conn, [])
    assert callback.answer.call_args.kwargs.get('show_alert') is True
    assert queues.get_user_position(conn, 2, sid) == 2


@pytest.mark.asyncio
async def test_not_ready_from_bot_is_reordered_and_shown_in_mini_app(client, conn, make_user):
    make_user(3, 'Шевченко Олег')
    sid = queues.create_session(conn, 100, 1, 1)
    for uid in (1, 2, 3):
        queues.join_queue(conn, uid, sid, lab_numbers=[1], planned_minutes=10)
    original = [(r['id'], r['position'], r['passed_labs']) for r in queues.get_entries(conn, sid)]
    a.start(conn, 100, sid)
    for uid, response, extra in ((1, 'present', {}), (2, 'present', {}), (3, 'late', {'minutes':25})):
        a.respond(conn, uid, a.view(conn, sid, uid)['own']['token'], response, **extra)
    callback = AsyncMock()
    callback.from_user.id = 1
    callback.data = 'att:' + a.view(conn, sid, 1)['own']['token'] + ':skip'
    await handlers.attendance_answer(callback, AsyncMock(), conn, [])
    callback.answer.assert_any_await('Відповідь збережено')
    screen = callback.message.answer.call_args.args[0]
    assert 'Не готовий · тимчасово пропускаємо' in screen
    assert screen.index('Коваль Андрій') < screen.index('Петренко Іван')
    url = f'/api/miniapp/queues/{sid}'
    for uid in (1, 2):
        data = await (await client.get(url, headers=headers(uid))).json()
        assert [e['id'] for e in data['entries']] == [original[i][0] for i in (1, 0, 2)]
        assert data['lesson']['ready_ids'] == [original[1][0]]
        assert 'Не готовий' in data['lesson']['states'][str(original[0][0])]
        assert 'Запізнюється' in data['lesson']['states'][str(original[2][0])]
        assert 'telegram_id' not in str(data['lesson'])
    token = a.view(conn, sid, 1)['own']['token']
    result = await client.post(url+'/attendance', headers=headers(1), json={'token':token,'response':'present'})
    assert result.status == 200
    assert a.view(conn, sid, 2)['own']['state'] == 'called'
    assert a.view(conn, sid, 1)['own']['state'] == 'present'
    assert original == [(r['id'], r['position'], r['passed_labs']) for r in queues.get_entries(conn, sid)]
