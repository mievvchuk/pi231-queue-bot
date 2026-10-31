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
                                     {'nonce': 'wrong'}, {'id': True}, {'id': '1'}, {'iat': int(time.time())+1000}])
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
