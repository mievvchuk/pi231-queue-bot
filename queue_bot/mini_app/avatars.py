"""Signed-user/bot avatar proxy. Telegram file URLs and tokens never leave the server."""
import asyncio
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


def setup(app):
    app.router.add_get('/api/miniapp/avatar/{kind}', avatar)
