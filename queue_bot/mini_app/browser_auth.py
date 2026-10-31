"""Telegram OIDC authorization code + PKCE, and bounded server-side sessions."""
import base64
import hashlib
import hmac
import json
import logging
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
log = logging.getLogger(__name__)


class LoginFailure(ValueError):
    """Internal allowlisted failure category, never a provider response body."""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def failed(reason, *, report=False):
    if report:
        log.warning('Telegram Login failed: reason=%s', reason)
    return redirect_to('/app/?login=failed&reason=' + reason)


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
    credentials = base64.b64encode(
        (config.TELEGRAM_LOGIN_CLIENT_ID + ':' + config.TELEGRAM_LOGIN_CLIENT_SECRET).encode()
    ).decode('ascii')
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as client:
        async with client.post(ISSUER + '/token', headers={'Authorization': 'Basic ' + credentials},
                data=dict(grant_type='authorization_code', code=code, redirect_uri=redirect,
                          client_id=config.TELEGRAM_LOGIN_CLIENT_ID, code_verifier=verifier),
                allow_redirects=False) as response:
            chunks, size = [], 0
            async for chunk in response.content.iter_chunked(8192):
                size += len(chunk)
                if size > 65536:
                    raise LoginFailure('token_response')
                chunks.append(chunk)
            raw = b''.join(chunks)
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise LoginFailure('telegram_unavailable' if response.status >= 500 else 'token_response') from None
            if not isinstance(payload, dict):
                raise LoginFailure('token_response')
            if response.status != 200:
                error = payload.get('error')
                if error == 'invalid_client' or response.status in (401, 403):
                    raise LoginFailure('client_config')
                if error == 'invalid_grant':
                    raise LoginFailure('code_expired')
                if error in ('invalid_scope', 'unauthorized_client'):
                    raise LoginFailure('client_config')
                raise LoginFailure('telegram_unavailable' if response.status >= 500 else 'token_response')
            token = payload.get('id_token')
            if not isinstance(token, str) or not token:
                raise LoginFailure('token_response')
            return token


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
        raise LoginFailure('token_response')
    header = jwt.get_unverified_header(token)
    if header.get('alg') != 'RS256' or not header.get('kid'):
        raise LoginFailure('signing_algorithm' if header.get('alg') != 'RS256' else 'signing_key')
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
        raise LoginFailure('signing_key')
    claims = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=['RS256'],
                        audience=config.TELEGRAM_LOGIN_CLIENT_ID, issuer=ISSUER,
                        options={'require': ['exp', 'iat', 'iss', 'aud', 'sub', 'nonce', 'id']}, leeway=30)
    if not isinstance(claims['nonce'], str) or not hmac.compare_digest(claims['nonce'], nonce):
        raise LoginFailure('nonce_mismatch')
    uid = claims['id']  # OIDC sub is NOT the Telegram Bot API user ID.
    if type(uid) is not int or not 0 < uid < 2**52:
        raise LoginFailure('profile_missing')
    return uid


async def callback(request):
    enabled, _, redirect = settings()
    state, code = request.query.get('state', ''), request.query.get('code', '')
    binding = request.cookies.get(FLOW_COOKIE, '')
    flows = request.app[STORE]['flows']
    prune(flows)
    flow = flows.get(digest(state)) if len(state) <= 128 else None
    if not enabled:
        return failed('client_config')
    if not flow:
        return failed('login_expired')
    if not binding or not hmac.compare_digest(flow['binding'], digest(binding)):
        return failed('browser_changed')
    del flows[digest(state)]
    if not code or len(code) > 4096 or request.query.get('error'):
        response = failed('cancelled' if request.query.get('error') == 'access_denied' else 'provider_response')
        cookie(response, FLOW_COOKIE, '', 0)
        return response
    stage = 'exchange'
    try:
        token = await exchange(code, flow['verifier'], redirect)
        stage = 'verification'
        uid = await verify_token(request, token, flow['nonce'])
        stage = 'session'
        # Restrictions and current role are checked again by every API call.
        from queue_bot.mini_app.api import CONTEXT
        from queue_bot.services import restrictions
        context = request.app[CONTEXT]
        if not context.get('ready'):
            raise LoginFailure('server_restarting')
        restrictions.require_available(context['conn'], uid)
        sessions = request.app[STORE]['sessions']
        prune(sessions)
        if len(sessions) >= 4096:
            raise LoginFailure('server_busy')
        old = request.cookies.get(SESSION_COOKIE, '')
        sessions.pop(digest(old), None)
        session_id = secrets.token_urlsafe(32)
        sessions[digest(session_id)] = dict(uid=uid, csrf=secrets.token_urlsafe(32),
                                            expires=time.time() + SESSION_TTL)
        response = redirect_to('/app/')
        cookie(response, SESSION_COOKIE, session_id, SESSION_TTL)
        cookie(response, FLOW_COOKIE, '', 0)
    except LoginFailure as error:
        response = failed(error.reason, report=True)
    except jwt.MissingRequiredClaimError as error:
        reason = {'nonce': 'nonce_missing', 'id': 'profile_missing'}.get(error.claim, 'token_claims')
        response = failed(reason, report=True)
    except jwt.ExpiredSignatureError:
        response = failed('token_expired', report=True)
    except jwt.InvalidAudienceError:
        response = failed('client_mismatch', report=True)
    except jwt.InvalidSignatureError:
        response = failed('token_signature', report=True)
    except jwt.PyJWTError:
        response = failed('token_claims', report=True)
    except (aiohttp.ClientError, TimeoutError):
        response = failed('telegram_unavailable', report=True)
    except Exception as error:
        from queue_bot.services.errors import BotError
        reason = 'access_denied' if isinstance(error, BotError) else 'server_error'
        # Log the stage and category only: exception messages can contain URLs,
        # authorization codes, tokens, or upstream response bodies.
        log.warning('Telegram Login failed: stage=%s category=%s', stage, type(error).__name__)
        response = failed(reason)
    cookie(response, FLOW_COOKIE, '', 0)
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
