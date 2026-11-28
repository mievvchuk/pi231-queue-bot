"""Administrators add or name works without changing their numeric identities."""
import json
import re

from queue_bot.services import admins, lab_catalog
from queue_bot.services.errors import BotError
from queue_bot.services.subjects import find_subject
from queue_bot.storage.database import transaction, PGConnectionWrapper


def parse_batch(text):
    result = []
    seen = set()
    for line_number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        parts = [part.strip() for part in raw.split('|')]
        if len(parts) in (2, 3):
            number, title = parts[:2]
            minutes = parts[2] if len(parts) == 3 else None
        elif len(parts) == 1:
            match = re.fullmatch(r'\s*(?:(?:ЛР|лаба|лабораторна(?: робота)?)\s*)?№?\s*(\d{1,3})[.)\s:–—-]+(.+)', raw, re.I)
            if not match:
                raise BotError(f'Рядок {line_number}: очікується «1. Назва» або «1 | Назва | 20».')
            number, title = match.groups()
            minutes = None
        else:
            raise BotError(f'Рядок {line_number}: використайте формат «1 | Назва | 20».')
        if not number.isascii() or not number.isdecimal() or not 1 <= int(number) <= 100:
            raise BotError(f'Рядок {line_number}: номер має бути від 1 до 100.')
        number = int(number)
        title = title.strip()
        if not title or len(title) > 100:
            raise BotError(f'Рядок {line_number}: назва має містити від 1 до 100 символів.')
        if number in seen:
            raise BotError(f'Лабораторна №{number} повторюється у списку.')
        if minutes is not None and (not minutes.isascii() or not minutes.isdecimal() or not 5 <= int(minutes) <= 240):
            raise BotError(f'Рядок {line_number}: час має бути числом від 5 до 240 хвилин.')
        result.append(dict(number=number, title=title, max_minutes=int(minutes) if minutes is not None else None))
        seen.add(number)
    if not result or len(result) > 20:
        raise BotError('Надішліть від 1 до 20 лабораторних, кожну з нового рядка.')
    return result


def prepare(conn, subject_id, batch):
    existing = {lab['number']: lab for lab in lab_catalog.labs_for_session({'subject_id':subject_id}, conn=conn)}
    prepared = []
    for item in batch:
        lab = dict(existing.get(item['number'], {'number':item['number'], 'max_minutes':20}))
        lab['title'] = item['title']
        if item['max_minutes'] is not None:
            lab['max_minutes'] = item['max_minutes']
            if 'min_minutes' in lab:
                lab['min_minutes'] = min(lab['min_minutes'], lab['max_minutes'])
        existing[lab['number']] = lab
        prepared.append(lab)
    try:
        lab_catalog.validate_catalog({str(subject_id): {'labs':list(existing.values())}})
    except RuntimeError:
        raise BotError('Для предмета можна зберегти до 20 лабораторних з унікальними номерами.') from None
    return prepared


def save_batch(conn, actor, subjects, subject_id, text):
    admins.require_admin(conn, actor)
    if not find_subject(subjects, subject_id):
        raise BotError('Предмет уже видалено або не знайдено.')
    batch = parse_batch(text)
    with transaction(conn):
        admins.require_admin(conn, actor)
        # Serialize edits to one subject so parallel batches cannot exceed its limit.
        lock_key = f'lab_catalog_lock:{subject_id}'
        conn.execute('INSERT INTO bot_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING', (lock_key, '1'))
        if isinstance(conn, PGConnectionWrapper):
            conn.execute('SELECT key FROM bot_state WHERE key = ? FOR UPDATE', (lock_key,)).fetchone()
        # Read again at confirmation, preserving unspecified limits and all other works.
        prepared = prepare(conn, subject_id, batch)
        for lab in prepared:
            conn.execute('''INSERT INTO bot_state(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value''',
                (f'lab_definition:{subject_id}:{lab["number"]}', json.dumps(lab, ensure_ascii=False)))
        admins.log_action(conn, actor, 'save_lab_definitions',
                          f'{subject_id}: '+', '.join(str(lab['number']) for lab in prepared))
    return prepared
