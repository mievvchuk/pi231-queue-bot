"""Signed-user/bot avatar proxy. Telegram file URLs and tokens never leave the server."""
import asyncio
import re
from collections import OrderedDict
from io import BytesIO
from time import monotonic

from aiohttp import web
from PIL import Image

from queue_bot.services.errors import BotError

MAX_BYTES = 2 * 1024 * 1024


class LimitedBuffer(BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_BYTES:
            raise ValueError('Avatar too large')
        return super().write(data)


def thumbnail(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('Avatar too large')
    with Image.open(BytesIO(raw)) as photo:
        if photo.format not in ('JPEG', 'PNG', 'WEBP') or photo.width * photo.height > 4_000_000:
            raise ValueError('Unsupported avatar')
        photo.thumbnail((256, 256))
        output = BytesIO()
        photo.convert('RGB').save(output, format='JPEG', quality=85)
        return output.getvalue()


async def _load(bot, kind, uid):
    if kind == 'bot':
        uid = (await bot.get_me()).id
        if type(uid) is not int:
            return None
    try:
        result = await bot.get_user_profile_photos(user_id=uid, limit=1)
        photos = result.photos
    except Exception:
        if kind != 'bot':
            raise
        photos = []
    if isinstance(photos, list) and photos and photos[0]:
        photo = photos[0][0]
        if getattr(photo, 'file_size', None) is not None and photo.file_size > MAX_BYTES:
            return None
        file_id = photo.file_id
    elif kind == 'bot':
        # Some bot profiles expose a chat photo rather than a profile-photo album.
        chat = await bot.get_chat(uid)
        file_id = getattr(getattr(chat, 'photo', None), 'small_file_id', None)
    else:
        return None
    if not isinstance(file_id, str) or not file_id:
        return None
    file = await bot.get_file(file_id)
    stream = await bot.download_file(file.file_path, destination=LimitedBuffer())
    return await asyncio.to_thread(thumbnail, stream.getvalue())


async def avatar(request):
    from . import api
    kind = request.match_info['kind']
    if kind not in ('bot', 'me'):
        raise BotError('Невідомий аватар.')
    ctx, uid = request[api.REQ_CONTEXT], request[api.REQ_UID]
    key = (kind, uid if kind == 'me' else 0)
    cache = ctx.setdefault('avatar_cache', OrderedDict())
    cached = cache.get(key)
    if cached and cached[0] > monotonic():
        raw = cached[1]
        cache.move_to_end(key)
    else:
        try:
            async with asyncio.timeout(6):
                raw = await _load(ctx['bot'], kind, uid)
        except Exception:
            # Optional photo failure must not block the app or log token-bearing URLs.
            raw = None
        api.revalidate(request)
        cache[key] = (monotonic() + (300 if raw else 60), raw)
        cache.move_to_end(key)
        while len(cache) > 64:
            cache.popitem(last=False)
    return web.Response(body=raw, status=200 if raw else 204,
                        content_type='image/jpeg' if raw else None,
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


def _student_target(request):
    from . import api
    from queue_bot.services import users
    api.require_admin(request)
    ctx = request[api.REQ_CONTEXT]
    student = users.get_user_by_id(ctx['conn'], api.path_id(request, 'user_id'))
    if not student:
        raise BotError('Студента не знайдено.')
    return ctx, student


async def _student_profile(ctx, uid):
    cache = ctx.setdefault('student_profile_cache', OrderedDict())
    cached = cache.get(uid)
    if cached and cached[0] > monotonic():
        cache.move_to_end(uid)
        return cached[1]
    pending = ctx.setdefault('student_profile_pending', {})
    async def load():
        semaphore = ctx.setdefault('student_profile_slots', asyncio.Semaphore(4))
        value = dict(username=None, username_available=False, photo=None)
        async with semaphore:
            try:
                async with asyncio.timeout(8):
                    try:
                        chat = await ctx['bot'].get_chat(uid)
                        username = getattr(chat, 'username', None)
                        if isinstance(username, str) and re.fullmatch(r'[A-Za-z0-9_]{1,32}', username):
                            value['username'] = username
                        value['username_available'] = True
                    except Exception:
                        pass
                    try:
                        value['photo'] = await _load(ctx['bot'], 'student', uid)
                    except Exception:
                        pass
            except TimeoutError:
                pass
        cache[uid] = (monotonic() + (300 if value['username_available'] or value['photo'] else 60), value)
        cache.move_to_end(uid)
        while len(cache) > 64:
            cache.popitem(last=False)
        return value
    if uid not in pending:
        if len(pending) >= 64:
            return dict(username=None, username_available=False, photo=None)
        pending[uid] = asyncio.create_task(load())
        pending[uid].add_done_callback(lambda finished: pending.pop(uid, None) if pending.get(uid) is finished else None)
    task = pending[uid]
    try:
        return await asyncio.shield(task)
    finally:
        if task.done() and pending.get(uid) is task:
            pending.pop(uid, None)


async def student_profile(request):
    """Only admins in admin mode can resolve a student stored in this database."""
    from . import api
    ctx, student = _student_target(request)
    target = student['telegram_id']
    profile = await _student_profile(ctx, target) if target > 0 else dict(username=None, username_available=False, photo=None)
    # Telegram I/O yields: recheck actor permissions and the target's current binding.
    api.revalidate(request)
    _, current = _student_target(request)
    if current['telegram_id'] != target:
        raise BotError('Профіль студента змінився. Оновіть список.')
    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
    if request.match_info['resource'] == 'avatar':
        raw = profile['photo']
        return web.Response(body=raw, status=200 if raw else 204,
                            content_type='image/jpeg' if raw else None, headers=headers)
    return web.json_response(dict(username=profile['username'], has_photo=bool(profile['photo']),
                                  username_available=profile['username_available']), headers=headers)


def setup(app):
    app.router.add_get('/api/miniapp/avatar/{kind}', avatar)
    app.router.add_get('/api/miniapp/admin/users/{user_id}/{resource:telegram|avatar}', student_profile)
