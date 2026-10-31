import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from queue_bot import config
from queue_bot.mini_app import browser_auth as auth
from queue_bot.services import restrictions
from tests.test_mini_app import client, headers


@pytest.fixture
def login_config(monkeypatch):
    monkeypatch.setattr(config, 'MINI_APP_URL', 'https://queue.example/app/')
    monkeypatch.setattr(config, 'TELEGRAM_LOGIN_CLIENT_ID', '123456')
    monkeypatch.setattr(config, 'TELEGRAM_LOGIN_CLIENT_SECRET', 'offline-test-secret')


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def signed_id(key, expected_nonce, **overrides):
    claims = dict(iss=auth.ISSUER, aud='123456', sub='oidc-sub-not-telegram-id', id=1,
                  iat=int(time.time()), exp=int(time.time()) + 300, nonce=expected_nonce)
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'test-key'})


async def begin(client):
    response = await client.get('/auth/telegram/start', allow_redirects=False)
    params = parse_qs(urlsplit(response.headers['Location']).query)
    binding = response.cookies[auth.FLOW_COOKIE].value
    return params, binding, response


async def login(client, monkeypatch, key):
    params, binding, _ = await begin(client)
    exchange = AsyncMock(return_value=signed_id(key, params['nonce'][0]))
    monkeypatch.setattr(auth, 'exchange', exchange)
    monkeypatch.setattr(auth, 'public_keys', AsyncMock(return_value=[
        {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())), 'kid': 'test-key'}]))
    result = await client.get('/auth/telegram/callback', params={'state': params['state'][0], 'code': 'one-use'},
                              headers={'Cookie': f'{auth.FLOW_COOKIE}={binding}'}, allow_redirects=False)
    return result, exchange


@pytest.mark.asyncio
async def test_oidc_start_binds_secure_cookie_state_nonce_and_pkce(client, login_config):
    params, binding, response = await begin(client)
    assert response.status == 302
    assert params['scope'] == ['openid profile']
    assert params['redirect_uri'] == ['https://queue.example/auth/telegram/callback']
    flow = client.app[auth.STORE]['flows'][auth.digest(params['state'][0])]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(flow['verifier'].encode()).digest()).rstrip(b'=').decode()
    assert challenge == params['code_challenge'][0]
    assert params['code_challenge_method'] == ['S256']
    assert flow['binding'] == auth.digest(binding)
    cookie = response.cookies[auth.FLOW_COOKIE]
    assert cookie['secure'] and cookie['httponly'] and cookie['samesite'] == 'Lax'
    assert response.headers['Cache-Control'] == 'no-store'


@pytest.mark.asyncio
async def test_login_creates_same_user_session_csrf_and_revocable_logout(client, login_config, monkeypatch, signing_key):
    response, exchange = await login(client, monkeypatch, signing_key)
    assert response.headers['Location'] == '/app/'
    sid = response.cookies[auth.SESSION_COOKIE].value
    cookie = {'Cookie': f'{auth.SESSION_COOKIE}={sid}'}
    session = await (await client.get('/auth/session', headers=cookie)).json()
    assert session['authenticated'] is True and session['csrf']
    payload = await (await client.get('/api/miniapp/bootstrap', headers=cookie)).json()
    assert payload['me']['name'] == 'Петренко Іван'
    assert not payload['me']['is_admin']
    assert (await client.get('/api/miniapp/bootstrap?mode=admin', headers=cookie)).status == 403
    assert (await client.post('/auth/logout', headers=cookie)).status == 401
    protected = {**cookie, 'Origin': 'https://queue.example', 'X-CSRF-Token': session['csrf']}
    assert (await client.post('/auth/logout', headers=protected)).status == 200
    assert (await client.get('/api/miniapp/bootstrap', headers=cookie)).status == 401
    assert exchange.await_count == 1


@pytest.mark.asyncio
async def test_browser_mutations_reject_missing_csrf_and_foreign_origin(client, login_config, monkeypatch, signing_key):
    response, _ = await login(client, monkeypatch, signing_key)
    cookie = {'Cookie': f'{auth.SESSION_COOKIE}={response.cookies[auth.SESSION_COOKIE].value}'}
    session = await (await client.get('/auth/session', headers=cookie)).json()
    for extra in ({}, {'Origin': 'https://evil.example', 'X-CSRF-Token': session['csrf']},
                  {'Origin': 'https://queue.example', 'X-CSRF-Token': 'forged'}):
        assert (await client.post('/api/miniapp/register', json={'full_name': 'Змінене Ім’я'}, headers={**cookie, **extra})).status == 401
    valid = {**cookie, 'Origin': 'https://queue.example', 'X-CSRF-Token': session['csrf']}
    # A valid request reaches normal domain validation (already registered).
    assert (await client.post('/api/miniapp/register', json={'full_name': 'Петренко Іван'}, headers=valid)).status != 401


@pytest.mark.asyncio
async def test_browser_session_still_enforces_late_ban_and_expiry(client, conn, login_config, monkeypatch, signing_key):
    response, _ = await login(client, monkeypatch, signing_key)
    sid = response.cookies[auth.SESSION_COOKIE].value
    cookie = {'Cookie': f'{auth.SESSION_COOKIE}={sid}'}
    conn.execute('UPDATE users SET is_blocked = TRUE WHERE telegram_id = ?', (1,))
    assert (await client.get('/api/miniapp/bootstrap', headers=cookie)).status in (400, 403)
    client.app[auth.STORE]['sessions'][auth.digest(sid)]['expires'] = 1
    assert (await client.get('/api/miniapp/bootstrap', headers=cookie)).status == 401


@pytest.mark.asyncio
async def test_callback_requires_matching_browser_cookie_and_cannot_replay(client, login_config, monkeypatch, signing_key):
    params, binding, _ = await begin(client)
    exchange = AsyncMock(return_value=signed_id(signing_key, params['nonce'][0]))
    monkeypatch.setattr(auth, 'exchange', exchange)
    monkeypatch.setattr(auth, 'public_keys', AsyncMock(return_value=[
        {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key())), 'kid': 'test-key'}]))
    query = {'state': params['state'][0], 'code': 'one-use'}
    bad = await client.get('/auth/telegram/callback', params=query, headers={'Cookie': f'{auth.FLOW_COOKIE}=wrong'}, allow_redirects=False)
    assert auth.SESSION_COOKIE not in bad.cookies
    exchange.assert_not_awaited()
    for index in range(2):
        result = await client.get('/auth/telegram/callback', params=query, headers={'Cookie': f'{auth.FLOW_COOKIE}={binding}'}, allow_redirects=False)
        assert (auth.SESSION_COOKIE in result.cookies) == (index == 0)
    assert exchange.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('overrides', [{'aud': 'other'}, {'iss': 'https://evil.example'}, {'exp': 1},
                                     {'nonce': 'wrong'}, {'id': True}, {'id': 'not-a-number'}, {'iat': int(time.time())+1000}])
async def test_verified_id_rejects_bad_claims(client, login_config, monkeypatch, signing_key, overrides):
    monkeypatch.setattr(auth, 'public_keys', AsyncMock(return_value=[
        {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key())), 'kid': 'test-key'}]))
    request = type('Request', (), {'app': client.app})()
    with pytest.raises((ValueError, jwt.PyJWTError)):
        await auth.verify_token(request, signed_id(signing_key, 'nonce', **overrides), 'nonce')


@pytest.mark.asyncio
async def test_forged_signatures_never_become_browser_sessions(client, login_config, monkeypatch, signing_key):
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(auth, 'public_keys', AsyncMock(return_value=[
        {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key())), 'kid': 'test-key'}]))
    request = type('Request', (), {'app': client.app})()
    with pytest.raises(jwt.InvalidSignatureError):
        await auth.verify_token(request, signed_id(wrong_key, 'nonce'), 'nonce')


@pytest.mark.asyncio
async def test_pwa_assets_are_public_and_real_api_stays_private(client):
    for name in ('manifest.webmanifest', 'sw.js', 'pwa.js', 'icon-192.png', 'icon-512.png'):
        assert (await client.get('/app/' + name)).status == 200
    manifest = await (await client.get('/app/manifest.webmanifest')).json(content_type=None)
    assert manifest['display'] == 'standalone'
    assert {row['sizes'] for row in manifest['icons']} == {'192x192', '512x512'}
    assert (await client.get('/auth/session')).headers['Cache-Control'] == 'no-store'
    assert (await client.get('/api/miniapp/bootstrap')).status == 401
    assert (await client.get('/api/miniapp/bootstrap', headers=headers())).status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,reason', [
    (auth.LoginFailure('client_config'), 'client_config'),
    (auth.LoginFailure('code_expired'), 'code_expired'),
    (TimeoutError('private-code-and-secret'), 'telegram_unavailable'),
    (jwt.InvalidAudienceError('private-token'), 'client_mismatch'),
    (jwt.MissingRequiredClaimError('nonce'), 'nonce_missing'),
    (jwt.MissingRequiredClaimError('id'), 'profile_missing'),
    (RuntimeError('private-code-and-secret'), 'server_error'),
])
async def test_callback_categorizes_failure_without_logging_secrets(client, login_config, monkeypatch, caplog, failure, reason):
    params, binding, _ = await begin(client)
    monkeypatch.setattr(auth, 'exchange', AsyncMock(side_effect=failure))
    result = await client.get('/auth/telegram/callback', params={'state': params['state'][0], 'code': 'private-authorization-code'},
                              headers={'Cookie': f'{auth.FLOW_COOKIE}={binding}'}, allow_redirects=False)
    location = parse_qs(urlsplit(result.headers['Location']).query)
    assert location == {'login': ['failed'], 'reason': [reason]}
    assert auth.SESSION_COOKIE not in result.cookies
    assert result.cookies[auth.FLOW_COOKIE]['max-age'] == '0'
    assert result.headers['Cache-Control'] == 'no-store'
    for secret in ('private-code-and-secret', 'private-token', 'private-authorization-code', params['state'][0], binding):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_missing_cookie_and_expired_flow_have_different_recovery_messages(client, login_config):
    params, binding, _ = await begin(client)
    query = {'state': params['state'][0], 'code': 'test-code'}
    response = await client.get('/auth/telegram/callback', params=query, allow_redirects=False)
    assert parse_qs(urlsplit(response.headers['Location']).query)['reason'] == ['browser_changed']
    client.app[auth.STORE]['flows'][auth.digest(params['state'][0])]['expires'] = 1
    response = await client.get('/auth/telegram/callback', params=query,
                                headers={'Cookie': f'{auth.FLOW_COOKIE}={binding}'}, allow_redirects=False)
    assert parse_qs(urlsplit(response.headers['Location']).query)['reason'] == ['login_expired']


@pytest.mark.asyncio
@pytest.mark.parametrize('status,body,reason', [
    (401, {'error': 'invalid_client', 'error_description': 'PRIVATE_SECRET'}, 'client_config'),
    (400, {'error': 'invalid_grant', 'error_description': 'PRIVATE_CODE'}, 'code_expired'),
    (500, {'error': 'server_error'}, 'telegram_unavailable'),
    (200, {'access_token': 'PRIVATE_TOKEN'}, 'token_response'),
])
async def test_exchange_classifies_real_http_response(client, login_config, monkeypatch, status, body, reason):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    seen = []
    async def token(request):
        assert request.headers['Authorization'] == 'Basic ' + base64.b64encode(b'123456:offline-test-secret').decode()
        seen.append(await request.post())
        return web.json_response(body, status=status)
    app = web.Application()
    app.router.add_post('/token', token)
    async with TestServer(app) as server:
        monkeypatch.setattr(auth, 'ISSUER', str(server.make_url('')).rstrip('/'))
        with pytest.raises(auth.LoginFailure) as error:
            await auth.exchange('offline-code', 'offline-verifier', 'https://queue.example/auth/telegram/callback')
    assert error.value.reason == reason
    assert seen[0]['code'] == 'offline-code'
    assert seen[0]['code_verifier'] == 'offline-verifier'


@pytest.mark.asyncio
async def test_exchange_accepts_successful_chunked_response(client, login_config, monkeypatch):
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    async def token(request):
        response = web.StreamResponse(headers={'Content-Type': 'application/json'})
        await response.prepare(request)
        await response.write(b'{"id_')
        await asyncio.sleep(0.02)
        await response.write(b'token":"offline.jwt.token"}')
        await response.write_eof()
        return response
    app = web.Application()
    app.router.add_post('/token', token)
    async with TestServer(app) as server:
        monkeypatch.setattr(auth, 'ISSUER', str(server.make_url('')).rstrip('/'))
        assert await auth.exchange('code', 'verifier', 'https://queue.example/auth/telegram/callback') == 'offline.jwt.token'


@pytest.mark.asyncio
@pytest.mark.parametrize('use_id', [False, True])
async def test_numeric_subject_only_and_string_id_finish_real_callback(client, login_config, monkeypatch, signing_key, use_id):
    params,binding,_ = await begin(client)
    claims = dict(iss=auth.ISSUER,aud='123456',sub='1',iat=int(time.time()),exp=int(time.time())+300,nonce=params['nonce'][0])
    if use_id:
        claims['id']='1'
    token=jwt.encode(claims,signing_key,algorithm='RS256',headers={'kid':'test-key'})
    monkeypatch.setattr(auth,'exchange',AsyncMock(return_value=token))
    monkeypatch.setattr(auth,'public_keys',AsyncMock(return_value=[{**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key())),'kid':'test-key'}]))
    result=await client.get('/auth/telegram/callback',params={'state':params['state'][0],'code':'test-code'},headers={'Cookie':f'{auth.FLOW_COOKIE}={binding}'},allow_redirects=False)
    assert result.headers['Location']=='/app/'
    cookie={'Cookie':f'{auth.SESSION_COOKIE}={result.cookies[auth.SESSION_COOKIE].value}'}
    data=await (await client.get('/api/miniapp/bootstrap',headers=cookie)).json()
    assert data['me']['name']=='Петренко Іван'
    assert not data['me']['is_admin']


@pytest.mark.asyncio
@pytest.mark.parametrize('subject', ['', 'abc', '1.0', '-1', '0001', '١', str(2**52)])
async def test_subject_fallback_rejects_noncanonical_or_out_of_range_identifiers(client, login_config, monkeypatch, signing_key, subject):
    claims=dict(iss=auth.ISSUER,aud='123456',sub=subject,iat=int(time.time()),exp=int(time.time())+300,nonce='nonce')
    token=jwt.encode(claims,signing_key,algorithm='RS256',headers={'kid':'test-key'})
    monkeypatch.setattr(auth,'public_keys',AsyncMock(return_value=[{**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key())),'kid':'test-key'}]))
    with pytest.raises(auth.LoginFailure):
        await auth.verify_token(type('Request',(),{'app':client.app})(),token,'nonce')
