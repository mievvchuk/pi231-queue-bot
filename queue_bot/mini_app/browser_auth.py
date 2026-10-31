"""Telegram OIDC authorization code + PKCE, and bounded server-side sessions."""
import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode, urlsplit

import aiohttp
import jwt
from aiohttp import web

from queue_bot import config
from queue_bot.mini_app.auth import AuthenticationError

STORE = web.AppKey('browser_auth_store', dict)
SESSION_COOKIE = '__Host-pi231-session'
FLOW_COOKIE = '__Host-pi231-login'
SESSION_TTL = 7 * 86400
FLOW_TTL = 600
ISSUER = 'https://oauth.telegram.org'


def settings():
    url = urlsplit(config.MINI_APP_URL)
    origin = f'{url.scheme}://{url.netloc}'
    enabled = bool(config.TELEGRAM_LOGIN_CLIENT_ID and config.TELEGRAM_LOGIN_CLIENT_SECRET
                   and url.scheme == 'https' and url.netloc)
    return enabled, origin, origin + '/auth/telegram/callback'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def redirect_to(location):
    return web.Response(status=302, headers={'Location': location})


def prune(entries):
    now = time.time()
    for key in list(entries):
        if entries[key]['expires'] <= now:
            del entries[key]


def cookie(response, name, value, age):
    response.set_cookie(name, value, max_age=age, httponly=True, secure=True,
                        samesite='Lax', path='/')


def current_session(request):
    store = request.app[STORE]['sessions']
    value = request.cookies.get(SESSION_COOKIE, '')
    if not value or len(value) > 128:
        raise AuthenticationError('Увійдіть через Telegram.')
    session = store.get(digest(value))
    if not session or session['expires'] <= time.time():
        store.pop(digest(value), None)
        raise AuthenticationError('Сесія завершилася. Увійдіть через Telegram ще раз.')
    return session


def browser_identity(request):
    session = current_session(request)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        _, origin, _ = settings()
        csrf = request.headers.get('X-CSRF-Token', '')
        if (request.headers.get('Origin') != origin or
                not hmac.compare_digest(csrf, session['csrf'])):
            raise AuthenticationError('Оновіть сторінку й повторіть дію.')
    return session['uid']


async def start(request):
    enabled, _, redirect = settings()
    if not enabled:
        return redirect_to('/app/?login=unavailable')
    store = request.app[STORE]['flows']
    prune(store)
    if len(store) >= 1024:
        raise web.HTTPServiceUnavailable(text='Забагато спроб входу. Спробуйте пізніше.')
    state, binding, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(4))
    store[digest(state)] = dict(binding=digest(binding), verifier=verifier, nonce=nonce,
                                expires=time.time() + FLOW_TTL)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    response = redirect_to(ISSUER + '/auth?' + urlencode(dict(
        client_id=config.TELEGRAM_LOGIN_CLIENT_ID, redirect_uri=redirect,
        response_type='code', scope='openid profile', state=state, nonce=nonce,
        code_challenge=challenge, code_challenge_method='S256')))
    cookie(response, FLOW_COOKIE, binding, FLOW_TTL)
    return response


async def exchange(code, verifier, redirect):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as client:
        async with client.post(ISSUER + '/token', auth=aiohttp.BasicAuth(
                config.TELEGRAM_LOGIN_CLIENT_ID, config.TELEGRAM_LOGIN_CLIENT_SECRET),
                data=dict(grant_type='authorization_code', code=code, redirect_uri=redirect,
                          client_id=config.TELEGRAM_LOGIN_CLIENT_ID, code_verifier=verifier),
                allow_redirects=False) as response:
            response.raise_for_status()
            return (await response.json())['id_token']


async def public_keys(request):
    store = request.app[STORE]
    if store.get('keys_until', 0) > time.time():
        return store['keys']
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as client:
        async with client.get(ISSUER + '/.well-known/jwks.json', allow_redirects=False) as response:
            response.raise_for_status()
            keys = (await response.json())['keys']
    store.update(keys=keys, keys_until=time.time() + 3600)
    return keys


async def verify_token(request, token, nonce):
    if not isinstance(token, str) or len(token) > 16384:
        raise ValueError('Invalid ID token')
    header = jwt.get_unverified_header(token)
    if header.get('alg') != 'RS256' or not header.get('kid'):
        raise ValueError('Unsupported signing algorithm')
    keys = await public_keys(request)
    key = next((row for row in keys if row.get('kid') == header['kid']), None)
    if key is None:
        # Rotate cached keys only once per minute, not for every forged kid.
        store = request.app[STORE]
        if store.get('refresh_after', 0) < time.time():
            store.update(keys_until=0, refresh_after=time.time() + 60)
            keys = await public_keys(request)
            key = next((row for row in keys if row.get('kid') == header['kid']), None)
    if key is None:
        raise ValueError('Unknown signing key')
    claims = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=['RS256'],
                        audience=config.TELEGRAM_LOGIN_CLIENT_ID, issuer=ISSUER,
                        options={'require': ['exp', 'iat', 'iss', 'aud', 'sub', 'nonce', 'id']}, leeway=30)
    if not isinstance(claims['nonce'], str) or not hmac.compare_digest(claims['nonce'], nonce):
        raise ValueError('Invalid nonce')
    uid = claims['id']  # OIDC sub is NOT the Telegram Bot API user ID.
    if type(uid) is not int or not 0 < uid < 2**52:
        raise ValueError('Invalid Telegram ID')
    return uid


async def callback(request):
    response = redirect_to('/app/?login=failed')
    enabled, _, redirect = settings()
    state, code = request.query.get('state', ''), request.query.get('code', '')
    binding = request.cookies.get(FLOW_COOKIE, '')
    flows = request.app[STORE]['flows']
    prune(flows)
    flow = flows.get(digest(state)) if len(state) <= 128 else None
    if not enabled or not flow or not binding or not hmac.compare_digest(flow['binding'], digest(binding)):
        return response
    del flows[digest(state)]
    cookie(response, FLOW_COOKIE, '', 0)
    if not code or len(code) > 4096 or request.query.get('error'):
        return response
    try:
        token = await exchange(code, flow['verifier'], redirect)
        uid = await verify_token(request, token, flow['nonce'])
        # Restrictions and current role are checked again by every API call.
        from queue_bot.mini_app.api import CONTEXT
        from queue_bot.services import restrictions
        context = request.app[CONTEXT]
        if not context.get('ready'):
            return response
        restrictions.require_available(context['conn'], uid)
        sessions = request.app[STORE]['sessions']
        prune(sessions)
        if len(sessions) >= 4096:
            return response
        old = request.cookies.get(SESSION_COOKIE, '')
        sessions.pop(digest(old), None)
        session_id = secrets.token_urlsafe(32)
        sessions[digest(session_id)] = dict(uid=uid, csrf=secrets.token_urlsafe(32),
                                            expires=time.time() + SESSION_TTL)
        response = redirect_to('/app/')
        cookie(response, SESSION_COOKIE, session_id, SESSION_TTL)
        cookie(response, FLOW_COOKIE, '', 0)
    except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, jwt.PyJWTError):
        pass
    except Exception:
        # Neither authorization codes, tokens nor secrets belong in logs.
        pass
    return response


async def session_info(request):
    enabled, _, _ = settings()
    try:
        session = current_session(request)
        data = dict(authenticated=True, csrf=session['csrf'], login_enabled=enabled)
    except AuthenticationError:
        data = dict(authenticated=False, login_enabled=enabled)
    return web.json_response(data)


async def logout(request):
    try:
        browser_identity(request)
    except AuthenticationError:
        return web.json_response({'error': 'Оновіть сторінку.'}, status=401)
    request.app[STORE]['sessions'].pop(digest(request.cookies.get(SESSION_COOKIE, '')), None)
    response = web.json_response({'ok': True})
    cookie(response, SESSION_COOKIE, '', 0)
    return response


@web.middleware
async def no_auth_cache(request, handler):
    response = await handler(request)
    if request.path.startswith('/auth/'):
        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                 'X-Content-Type-Options': 'nosniff'})
    return response


def setup(app):
    app[STORE] = {'flows': {}, 'sessions': {}}
    app.router.add_get('/auth/session', session_info)
    app.router.add_get('/auth/telegram/start', start)
    app.router.add_get('/auth/telegram/callback', callback)
    app.router.add_post('/auth/logout', logout)
