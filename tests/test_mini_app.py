import hashlib
import asyncio
import hmac
import json
import time
from urllib.parse import urlencode
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from queue_bot import config
from queue_bot.mini_app.auth import authenticate, AuthenticationError
from queue_bot.services import queues, restrictions, users
from queue_bot.web import create_web_app


TOKEN = '123456:offline-test-token'


def signed(uid=1, *, when=None, **extra):
    data = {'auth_date': str(int(time.time()) if when is None else when),
            'user': json.dumps({'id': uid, 'first_name': 'Тест'}, separators=(',', ':')), **extra}
    secret = hmac.new(b'WebAppData', TOKEN.encode(), hashlib.sha256).digest()
    data['hash'] = hmac.new(secret, '\n'.join(f'{k}={v}' for k, v in sorted(data.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def headers(uid=1):
    return {'Authorization': 'tma ' + signed(uid)}


def test_authentication_proves_identity_not_unsafe_user_field():
    assert authenticate(signed(123), TOKEN) == 123
    with pytest.raises(AuthenticationError):
        authenticate(signed().replace('auth_date=', 'auth_date=1'), TOKEN)


@pytest.mark.parametrize('value', ['', 'user=1', signed(when=1), signed(when=int(time.time())+120), signed()+'&user=forged'])
def test_authentication_rejects_missing_expired_future_and_duplicate_fields(value):
    with pytest.raises(AuthenticationError):
        authenticate(value, TOKEN)


@pytest_asyncio.fixture
async def client(conn, make_user):
    make_user(1, 'Петренко Іван')
    make_user(2, 'Коваль Андрій')
    make_user(100, 'Адмін Власник')
    subjects = [{'id': 1, 'name': 'Програмування', 'teachers': [{'id': 1, 'name': 'Викладач'}]},
                {'id': 7, 'name': 'Курсова', 'teachers': [{'id': 1, 'name': 'Викладач'}]}]
    app = create_web_app(conn=conn, subjects=subjects, bot=AsyncMock(), bot_token=TOKEN)
    async with TestClient(TestServer(app)) as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_health_and_shell_public_but_private_api_requires_signed_identity(client):
    assert (await client.get('/')).status == 200
    assert (await client.get('/app/')).status == 200
    assert (await client.get('/app/app.js')).status == 200
    assert (await client.get('/api/miniapp/bootstrap')).status == 401
    assert (await client.get('/api/miniapp/bootstrap?telegram_id=100')).status == 401
    response = await client.get('/api/miniapp/bootstrap', headers=headers(999))
    assert response.status == 200 and (await response.json())['me']['is_registered'] is False
    assert (await client.get('/api/miniapp/not-a-route', headers=headers())).status == 404


@pytest.mark.asyncio
async def test_student_privacy_and_current_admin_permissions(client, conn):
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 2, sid, lab_numbers=[1], planned_minutes=10)
    response = await client.get(f'/api/miniapp/queues/{sid}', headers=headers(100))
    payload = await response.json()
    assert response.status == 200
    assert 'telegram_id' not in payload['entries'][0] and 'user_id' not in payload['entries'][0]
    response = await client.get(f'/api/miniapp/queues/{sid}?mode=admin', headers=headers(100))
    assert (await response.json())['entries'][0]['telegram_id'] == 2
    assert (await client.get(f'/api/miniapp/queues/{sid}?mode=admin', headers=headers(1))).status == 403
    conn.execute('DELETE FROM admins WHERE telegram_id = ?', (100,))
    assert (await client.get(f'/api/miniapp/queues/{sid}?mode=admin', headers=headers(100))).status == 403


@pytest.mark.asyncio
async def test_signup_edit_partial_and_final_completion_share_bot_data(client, conn):
    sid = queues.create_session(conn, 100, 1, 1)
    path = f'/api/miniapp/queues/{sid}'
    response = await client.post(path+'/join', headers=headers(), json={'labs': [1, 2], 'minutes': 20})
    assert response.status == 200
    row = queues._own_active_entry(conn, 1, sid)
    assert row['planned_minutes'] == 20
    ref = {'entry_id': row['id'], 'revision': row['entry_revision']}
    assert (await client.post(path+'/complete', headers=headers(), json={**ref, 'labs': [1]})).status == 200
    row = queues._own_active_entry(conn, 1, sid)
    assert json.loads(row['selected_labs']) == [2] and row['position'] == 1
    assert (await client.post(path+'/complete', headers=headers(), json=ref)).status == 400
    ref = {'entry_id': row['id'], 'revision': row['entry_revision']}
    assert (await client.post(path+'/complete', headers=headers(), json=ref)).status == 200
    assert queues.get_user_position(conn, 1, sid) is None
    assert queues.get_history(conn, sid)[0]['labs_passed'] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{}, {'labs': [1], 'minutes': 4}, {'labs': [1], 'minutes': 41}, {'labs': [True], 'minutes': 10}])
async def test_api_cannot_bypass_lab_requirements(client, conn, body):
    sid = queues.create_session(conn, 100, 1, 1)
    assert (await client.post(f'/api/miniapp/queues/{sid}/join', headers=headers(), json=body)).status == 400
    assert queues.get_entries(conn, sid) == []


@pytest.mark.asyncio
async def test_consultation_and_legacy_completion_do_not_ask_labs(client, conn):
    sid = queues.create_session(conn, 100, 7, 1)
    assert (await client.post(f'/api/miniapp/queues/{sid}/join', headers=headers(), json={})).status == 200
    row = queues._own_active_entry(conn, 1, sid)
    assert row['selected_labs'] is None
    assert (await client.post(f'/api/miniapp/queues/{sid}/complete', headers=headers(),
                             json={'entry_id': row['id'], 'revision': row['entry_revision']})).status == 200
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid)
    row = queues._own_active_entry(conn, 1, sid)
    assert (await client.post(f'/api/miniapp/queues/{sid}/complete', headers=headers(),
                             json={'entry_id': row['id'], 'revision': row['entry_revision']})).status == 200


@pytest.mark.asyncio
async def test_bans_and_ownership_enforced_for_every_request(client, conn):
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 2, sid)
    other = queues._own_active_entry(conn, 2, sid)
    body = {'entry_id': other['id'], 'revision': other['entry_revision']}
    assert (await client.post(f'/api/miniapp/queues/{sid}/complete', headers=headers(), json=body)).status == 400
    assert queues.get_user_position(conn, 2, sid) == 1
    restrictions.set_restriction(conn, 100, users.get_user(conn, 1)['id'], days=1)
    assert (await client.get('/api/miniapp/bootstrap', headers=headers())).status == 403
    assert (await client.post(f'/api/miniapp/queues/{sid}/join', headers=headers(), json={})).status == 403


@pytest.mark.asyncio
async def test_admin_operations_and_ban_then_unban(client, conn):
    sid = queues.create_session(conn, 100, 1, 1)
    path = f'/api/miniapp/admin/queues/{sid}?mode=admin'
    assert (await client.post(path, headers=headers(), json={'action': 'close'})).status == 403
    assert (await client.post(path, headers=headers(100), json={'action': 'close'})).status == 200
    assert queues.get_session(conn, sid)['status'] == 'closed'
    assert (await client.post(path, headers=headers(100), json={'action': 'open'})).status == 200
    uid = users.get_user(conn, 2)['id']
    queues.join_queue(conn, 2, sid)
    assert (await client.post(f'/api/miniapp/admin/users/{uid}?mode=admin', headers=headers(100), json={'action': 'ban', 'days': 2})).status == 200
    assert users.get_user(conn, 2)['banned_until'] and queues.get_user_position(conn, 2, sid) is None
    assert (await client.post(f'/api/miniapp/admin/users/{uid}?mode=admin', headers=headers(100), json={'action': 'unban'})).status == 200
    assert not users.get_user(conn, 2)['is_blocked']


@pytest.mark.asyncio
async def test_swap_created_and_answered_through_shared_service(client, conn):
    sid = queues.create_session(conn, 100, 1, 1)
    for uid in (1, 2):
        queues.join_queue(conn, uid, sid)
    a, b = queues.get_entries(conn, sid)
    body = {'entry_id': a['id'], 'revision': a['entry_revision'], 'target_entry_id': b['id'], 'target_revision': b['entry_revision']}
    path = f'/api/miniapp/queues/{sid}/swap'
    assert (await client.post(path, headers=headers(), json=body)).status == 200
    assert (await client.post(path, headers=headers(), json=body)).status == 400
    inbox = await (await client.get('/api/miniapp/bootstrap', headers=headers(2))).json()
    token = inbox['swaps'][0]['token']
    assert (await client.post(f'/api/miniapp/swaps/{token}', headers=headers(), json={'accept': True})).status == 400
    assert (await client.post(f'/api/miniapp/swaps/{token}', headers=headers(2), json={'accept': True})).status == 200
    assert queues.get_user_position(conn, 1, sid) == 2


@pytest.mark.asyncio
async def test_pre_ready_api_cannot_access_database():
    async with TestClient(TestServer(create_web_app())) as client:
        assert (await client.get('/')).status == 200
        assert (await client.get('/api/miniapp/bootstrap', headers=headers())).status == 503


@pytest.mark.asyncio
async def test_huge_position_is_rejected_without_creating_slots(client, conn):
    sid = queues.create_session(conn, 100, 1, 1)
    response = await client.post(f'/api/miniapp/queues/{sid}/join', headers=headers(), json={'labs': [1], 'minutes': 10, 'position': 1001})
    assert response.status == 400
    assert queues.get_entries(conn, sid) == []


@pytest.mark.asyncio
async def test_swap_rejects_target_changed_since_screen_loaded(client, conn):
    sid = queues.create_session(conn, 100, 1, 1)
    for uid in (1, 2):
        queues.join_queue(conn, uid, sid, lab_numbers=[1], planned_minutes=10)
    a, b = queues.get_entries(conn, sid)
    queues.set_own_planned_minutes(conn, 2, sid, 15)
    response = await client.post(f'/api/miniapp/queues/{sid}/swap', headers=headers(), json={
        'entry_id': a['id'], 'revision': a['entry_revision'], 'target_entry_id': b['id'], 'target_revision': b['entry_revision']})
    assert response.status == 400
    assert conn.execute('SELECT count(*) FROM swap_requests').fetchone()[0] == 0


@pytest.mark.asyncio
async def test_admin_identifiers_require_explicit_admin_mode(client):
    assert (await client.get('/api/miniapp/admin/users', headers=headers(100))).status == 403
    assert (await client.get('/api/miniapp/admin/users?mode=admin', headers=headers(100))).status == 200


@pytest.mark.asyncio
async def test_malformed_json_and_stale_leave_do_not_mutate_other_entries(client, conn):
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_numbers=[1], planned_minutes=10)
    row = queues._own_active_entry(conn, 1, sid)
    path = f'/api/miniapp/queues/{sid}'
    assert (await client.post(path+'/edit', headers={**headers(), 'Content-Type':'application/json'}, data='not json')).status == 400
    queues.set_own_planned_minutes(conn, 1, sid, 15)
    assert (await client.post(path+'/leave', headers=headers(), json={'entry_id':row['id'],'revision':row['entry_revision']})).status == 400
    assert queues.get_user_position(conn, 1, sid) == 1


@pytest.mark.asyncio
async def test_authentication_is_rechecked_when_body_wait_crosses_session_expiry(client, conn, monkeypatch):
    from types import SimpleNamespace
    from queue_bot.mini_app import api, auth
    clock = [int(time.time())]
    monkeypatch.setattr(auth, 'time', SimpleNamespace(time=lambda: clock[0]))
    entered, release = asyncio.Event(), asyncio.Event()
    original = api.body
    async def delayed_body(request):
        entered.set()
        await release.wait()
        return await original(request)
    monkeypatch.setattr(api, 'body', delayed_body)
    sid = queues.create_session(conn, 100, 1, 1)
    request = asyncio.create_task(client.post(f'/api/miniapp/queues/{sid}/join',
        headers={'Authorization': 'tma ' + signed(when=clock[0])}, json={'labs': [1], 'minutes': 10}))
    await asyncio.wait_for(entered.wait(), 3)
    clock[0] += 3601
    release.set()
    assert (await request).status == 401
    assert queues.get_user_position(conn, 1, sid) is None


@pytest.mark.asyncio
async def test_maintenance_enabled_while_body_waits_blocks_completion(client, conn, monkeypatch):
    from queue_bot.mini_app import api
    from queue_bot.services import maintenance
    entered, release = asyncio.Event(), asyncio.Event()
    original = api.body
    async def delayed_body(request):
        entered.set()
        await release.wait()
        return await original(request)
    monkeypatch.setattr(api, 'body', delayed_body)
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_numbers=[1], planned_minutes=10)
    row = queues._own_active_entry(conn, 1, sid)
    request = asyncio.create_task(client.post(f'/api/miniapp/queues/{sid}/complete', headers=headers(),
        json={'entry_id':row['id'], 'revision':row['entry_revision']}))
    await asyncio.wait_for(entered.wait(), 3)
    monkeypatch.setattr(maintenance, 'is_maintenance_active', lambda conn: True)
    release.set()
    assert (await request).status == 503
    assert queues.get_user_position(conn, 1, sid) == 1


def test_mini_app_entry_keeps_all_existing_chat_buttons(conn, monkeypatch):
    from queue_bot.handlers.common import get_reply_menu
    monkeypatch.setattr(config, 'MINI_APP_URL', '', raising=False)
    before = [b.text for row in get_reply_menu(conn, 100).keyboard for b in row]
    monkeypatch.setattr(config, 'MINI_APP_URL', 'https://example.invalid/app/')
    after = [b.text for row in get_reply_menu(conn, 100).keyboard for b in row]
    assert after == before + ['📱 Мініапка']


@pytest.mark.parametrize('url', ['http://example.com/app/', 'https://user:password@example.com/app/', 'javascript:alert(1)', 'https://example.com/app/#bad'])
def test_unsafe_mini_app_urls_fail_runtime_validation(monkeypatch, url):
    monkeypatch.setenv('BOT_TOKEN', TOKEN)
    monkeypatch.setattr(config, 'DATABASE_URL', ':memory:')
    monkeypatch.setattr(config, 'OWNER_ID', 100)
    monkeypatch.setattr(config, 'MINI_APP_URL', url, raising=False)
    with pytest.raises(RuntimeError, match='MINI_APP_URL'):
        config.validate_runtime()


@pytest.mark.asyncio
async def test_home_cards_show_only_current_users_remaining_labs(client, conn):
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_numbers=[1, 2], planned_minutes=20)
    queues.join_queue(conn, 2, sid, lab_numbers=[3], planned_minutes=15)
    payload = await (await client.get('/api/miniapp/bootstrap', headers=headers())).json()
    card = next(q for q in payload['queues'] if q['id'] == sid)
    assert card['my_labs'] == [1, 2] and card['my_planned_minutes'] == 20
    row = queues._own_active_entry(conn, 1, sid)
    response = await client.post(f'/api/miniapp/queues/{sid}/complete', headers=headers(),
                                 json={'entry_id': row['id'], 'revision': row['entry_revision'], 'labs': [1]})
    assert response.status == 200
    payload = await (await client.get('/api/miniapp/bootstrap', headers=headers())).json()
    card = next(q for q in payload['queues'] if q['id'] == sid)
    assert card['my_labs'] == [2] and card['my_position'] == 1
    peer = await (await client.get('/api/miniapp/bootstrap', headers=headers(2))).json()
    card = next(q for q in peer['queues'] if q['id'] == sid)
    assert card['my_labs'] == [3] and card['my_planned_minutes'] == 15
    assert 'telegram_id' not in card and 'user_id' not in card
    outsider = await (await client.get('/api/miniapp/bootstrap', headers=headers(100))).json()
    card = next(q for q in outsider['queues'] if q['id'] == sid)
    assert card['my_labs'] is None and card['my_planned_minutes'] is None
