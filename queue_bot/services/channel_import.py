"""Poll the public Telegram preview; persist drafts before requesting approval."""
import asyncio
import datetime
import html
import json
import logging
import re
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from queue_bot import config
from queue_bot.services import recognition, diagnostics
from queue_bot.services.errors import BotError

log = logging.getLogger(__name__)


def format_no_replacements(date_str):
    day = datetime.date.fromisoformat(date_str).strftime("%d.%m.%Y")
    return f"На {day} замін немає для {html.escape(config.STUDENT_GROUP)}."


def get_state(conn, key):
    row = conn.execute('SELECT value FROM bot_state WHERE key = ?', (key,)).fetchone()
    return json.loads(row['value']) if row else None


def set_state(conn, key, value):
    conn.execute('INSERT INTO bot_state (key, value) VALUES (?, ?) '
                 'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                 (key, json.dumps(value, ensure_ascii=False)))


def parse_posts(page, channel):
    posts = []
    for node in BeautifulSoup(page, 'html.parser').select('.tgme_widget_message[data-post]'):
        post = node['data-post']
        if not re.fullmatch(re.escape(channel) + r'/\d+', post):
            continue
        photos = []
        for photo in node.select('.tgme_widget_message_photo_wrap'):
            match = re.search(r"background-image:\s*url\(['\"]?(https://[^'\")]+)", photo.get('style', ''))
            if match:
                url = html.unescape(match[1])
                host = urlparse(url).hostname or ''
                if host.endswith('.telesco.pe') or host.endswith('.telegram-cdn.org'):
                    photos.append(url)
        if photos:
            posts.append({'id': int(post.split('/')[1]), 'photos': list(dict.fromkeys(photos)),
                          'url': 'https://t.me/' + post})
    return sorted(posts, key=lambda p: p['id'])


async def read_bounded(session, url, limit):
    async with session.get(url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; PI231ScheduleBot/1.0)',
        'Referer': 'https://t.me/',
    }) as response:
        response.raise_for_status()
        data = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError('Вміст перевищує допустимий розмір')
        return bytes(data)


async def download_post_images(session, post, channel):
    """Retry transient CDN failures, refreshing signed URLs from the post embed."""
    current = post
    for attempt in range(3):
        try:
            images = [await read_bounded(session, url, 15 * 1024 * 1024) for url in current['photos']]
            # Do not send an HTML CDN error page to the vision model as a JPEG.
            if any(not (image.startswith(b'\xff\xd8\xff') or image.startswith(b'\x89PNG\r\n\x1a\n')
                        or (image.startswith(b'RIFF') and image[8:12] == b'WEBP')) for image in images):
                raise BotError('Telegram CDN повернув файл, який не є зображенням.')
            return images
        except (aiohttp.ClientError, asyncio.TimeoutError, BotError) as error:
            if isinstance(error, aiohttp.ClientResponseError) and error.status not in {403, 404, 408, 429, 500, 502, 503, 504}:
                raise
            if attempt == 2:
                raise BotError(f'Не вдалося завантажити фото допису №{post["id"]} з Telegram CDN '
                               f'після 3 спроб ({type(error).__name__}'
                               f'{" HTTP " + str(error.status) if isinstance(error, aiohttp.ClientResponseError) else ""}).') from error
            log.warning('Telegram photo download failed for post %s, attempt %s: %s',
                        post['id'], attempt + 1, type(error).__name__)
            await asyncio.sleep(2 * (attempt + 1))
            try:
                page = await read_bounded(session, post['url'] + '?embed=1&mode=tme', 4 * 1024 * 1024)
                refreshed = parse_posts(page.decode('utf-8'), channel)
                current = next((item for item in refreshed if item['id'] == post['id']), current)
            except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError):
                log.warning('Could not refresh Telegram photo URL for post %s', post['id'])


async def poll_once(bot, conn, session):
    channel = config.REPLACEMENTS_CHANNEL
    if not re.fullmatch(r'[A-Za-z0-9_]+', channel):
        raise ValueError('Invalid REPLACEMENTS_CHANNEL')
    page = await read_bounded(session, f'https://t.me/s/{channel}', 4 * 1024 * 1024)
    posts = parse_posts(page.decode('utf-8'), channel)
    if not posts:
        raise ValueError('На сторінці не знайдено доступних фото; це не означає відсутність замін')
    cursor_key = f'channel_cursor:{channel}'
    cursor = get_state(conn, cursor_key)
    # First launch imports only the latest album, never the entire visible history.
    candidates = [p for p in posts if p['id'] > cursor] if cursor is not None else posts[-1:]
    for post in candidates:
        key = f'channel_draft:{channel}:{post["id"]}'
        draft = get_state(conn, key)
        if draft is None:
            images = await download_post_images(session, post, channel)
            result = await recognition.recognize_replacements_from_images(
                images, config.STUDENT_GROUP, conn, allow_empty=True)
            datetime.date.fromisoformat(result['date'])
            draft = dict(result, source=post['url'], status='pending')
            set_state(conn, key, draft)
        if not draft.get('notified'):
            from queue_bot.services.replacements import format_replacement
            text = (f'📷 <a href="{post["url"]}">Новий допис із замінами</a>\n'
                    f'📅 {html.escape(draft["date"])}\n\n')
            text += '\n\n'.join(format_replacement(r) for r in draft['replacements']) if draft['replacements'] else format_no_replacements(draft['date'])
            text += '\n\nПідтвердити цей результат і сповістити студентів?'
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text='✅ Підтвердити', callback_data=f'ch_yes:{post["id"]}'),
                InlineKeyboardButton(text='❌ Відхилити', callback_data=f'ch_no:{post["id"]}'),
            ]])
            await bot.send_message(config.OWNER_ID, text, reply_markup=keyboard)
            draft['notified'] = True
            set_state(conn, key, draft)
        set_state(conn, cursor_key, post['id'])
        log.info('Channel post %s processed; approval draft delivered', post['id'])


async def watch_channel(bot, conn):
    if not config.REPLACEMENTS_CHANNEL:
        return
    async with aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=60)) as session:
        while True:
            try:
                if config.RECOGNITION_API_KEY and config.RECOGNITION_PROVIDER != 'disabled':
                    await poll_once(bot, conn, session)
                    set_state(conn, 'channel_last_error', None)
            except Exception as error:
                log.exception('Channel import failed; will retry without advancing cursor')
                if not get_state(conn, 'channel_last_error'):
                    try:
                        reason = diagnostics.redact(str(error)) if isinstance(error, BotError) else type(error).__name__
                        await bot.send_message(config.OWNER_ID, '⚠️ Не вдалося перевірити фото каналу.\n'
                                               f'Причина: {html.escape(reason[:700])}\n'
                                               'Повторю спробу автоматично. Це не означає, що замін немає.')
                        set_state(conn, 'channel_last_error', True)
                    except Exception:
                        log.exception('Could not notify owner')
            await asyncio.sleep(config.CHANNEL_POLL_SECONDS)
