"""Owner-only exception reports shared by Telegram, HTTP and background jobs."""
import hashlib
import html
import json
import logging
import os
import re
import time
import traceback
import uuid
from collections import OrderedDict
from datetime import datetime, timezone

from aiogram.types import BufferedInputFile

from queue_bot import config
from queue_bot.services import admins

log = logging.getLogger(__name__)
_recent = OrderedDict()
_sent = OrderedDict()
MAX_RECENT = 200
REPORT_TTL = 24 * 60 * 60


def redact(value):
    text = str(value)
    # Never include configured credentials, even if a library prints a DSN.
    names = set(os.environ) | set(vars(config))
    for name in names:
        if any(part in name.upper() for part in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY', 'DATABASE_URL')):
            secret = os.environ.get(name) or getattr(config, name, None)
            if isinstance(secret, str) and len(secret) >= 6:
                text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'\b(?:postgres(?:ql)?://)[^\s\'"<>]+', '[DATABASE_URL REDACTED]', text)
    text = re.sub(r'\b\d{6,}:[A-Za-z0-9_-]{20,}', '[BOT_TOKEN REDACTED]', text)
    text = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~-]+', 'Bearer [REDACTED]', text)
    text = re.sub(r'''(?i)\b(password|api[_-]?key|secret)\s*[:=]\s*('[^']*'|"[^"]*"|[^\s,;]+)''', r'\1=[REDACTED]', text)
    return text


def explanation(error):
    name = type(error).__name__
    if name in ('ProgrammingError', 'IntegrityError', 'OperationalError', 'DatabaseError'):
        return 'Бот не зміг виконати дію через помилку запиту або з’єднання з базою даних. Оновіть чергу перед повторною спробою.'
    if name == 'TelegramRetryAfter':
        return 'Telegram тимчасово обмежив частоту запитів. Зачекайте трохи й повторіть дію.'
    if name.startswith('Telegram'):
        return 'Telegram не прийняв запит бота. Відкрийте актуальний екран і повторіть дію.'
    if isinstance(error, (TimeoutError, ConnectionError)):
        return 'Бот не отримав відповіді від зовнішнього сервісу. Спробуйте ще раз трохи пізніше.'
    return 'Бот не зміг обробити цю дію через внутрішню помилку. Оновіть екран перед повторною спробою.'


def latest(telegram_id):
    record = _recent.get(telegram_id)
    return record if record and time.time() - record['timestamp'] <= REPORT_TTL else None


def document(record):
    return json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8')


def owner_caption(record):
    ctx = record['context']
    action = next((ctx[k] for k in ('button', 'handler', 'operation', 'route') if ctx.get(k)), 'невідомо')
    return html.escape(f'{record["code"]}\n{record["exception"]}: {record["message"][:240]}\n'
        f'Дія: {action[:80]}\nКористувач: {record["telegram_id"]}\nПовний traceback і контекст — у файлі.')


async def report(error, *, bot, conn, telegram_id=None, context=None):
    record = dict(code='ERR-' + uuid.uuid4().hex[:12], timestamp=time.time(),
        time_utc=datetime.now(timezone.utc).isoformat(), telegram_id=telegram_id,
        exception=type(error).__name__, message=redact(error),
        context={str(k): redact(v) for k, v in (context or {}).items()},
        traceback=redact(''.join(traceback.format_exception(type(error), error, error.__traceback__))))
    log.error('Incident %s: %s', record['code'], json.dumps(record, ensure_ascii=False))
    if telegram_id is not None:
        _recent[telegram_id] = record
        _recent.move_to_end(telegram_id)
        while len(_recent) > MAX_RECENT:
            _recent.popitem(last=False)
    fingerprint = hashlib.sha256(document({k: record[k] for k in ('exception', 'message', 'context', 'telegram_id')})).hexdigest()
    # A broken background service must not send a document every five seconds.
    if time.time() - _sent.get(fingerprint, 0) < 300:
        return record
    if bot is not None:
        try:
            delivered = False
            for owner in admins.get_owner_ids(conn):
                if owner and owner > 0:
                    await bot.send_document(owner, BufferedInputFile(document(record), filename=record['code']+'.json'),
                        caption=owner_caption(record))
                    delivered = True
            if delivered:
                _sent[fingerprint] = time.time()
            while len(_sent) > MAX_RECENT:
                _sent.popitem(last=False)
        except Exception as delivery_error:
            # The original exception stays in logs if DB/Telegram itself is down.
            log.error('Cannot deliver owner diagnostic %s: %s', record['code'], redact(delivery_error))
    return record
