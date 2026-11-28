"""Subject lab identities and summed limits. Loading never creates subjects or queues."""
from functools import lru_cache
import json
from pathlib import Path
import re

from queue_bot import config
from queue_bot.services.errors import BotError
from queue_bot.services.lab_estimates import validate_planned_minutes


@lru_cache(maxsize=8)
def load_catalog(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    validate_catalog(data)
    return data


def validate_catalog(data):
    if not isinstance(data, dict):
        raise RuntimeError('LAB_CATALOG_PATH must contain a subject map')
    for sid, subject in data.items():
        if not sid.isdigit() or int(sid) <= 0 or not isinstance(subject, dict):
            raise RuntimeError('Invalid laboratory subject ID')
        labs = subject.get('labs')
        if not isinstance(labs, list) or not labs or len(labs) > 20:
            raise RuntimeError('A subject must contain 1–20 laboratory definitions')
        numbers = []
        for lab in labs:
            if not isinstance(lab, dict):
                raise RuntimeError('Invalid laboratory definition')
            number, cap = lab.get('number'), lab.get('max_minutes')
            low = lab.get('min_minutes', cap)
            if (type(number) is not int or not 1 <= number <= 100 or
                    type(cap) is not int or not 5 <= cap <= 2**31 - 1 or
                    type(low) is not int or not 5 <= low <= cap or
                    not isinstance(lab.get('title', ''), str) or len(lab.get('title', '')) > 100):
                raise RuntimeError('Invalid laboratory number, title or time limit')
            numbers.append(number)
        if len(numbers) != len(set(numbers)) or sum(lab['max_minutes'] for lab in labs) > 2**31 - 1:
            raise RuntimeError('Duplicate laboratory number or excessive total time limit')


def labs_for_session(session, *, conn=None):
    if not session:
        return ()
    subject = load_catalog(config.LAB_CATALOG_PATH).get(str(session['subject_id']))
    labs = {lab['number']: dict(lab) for lab in subject['labs']} if subject else {}
    if conn is not None:
        rows = conn.execute('SELECT value FROM bot_state WHERE key LIKE ?',
                            (f'lab_definition:{session["subject_id"]}:%',)).fetchall()
        for row in rows:
            lab = json.loads(row['value'])
            labs[lab['number']] = lab
    result = sorted(labs.values(), key=lambda lab: lab['number'])
    if result:
        validate_catalog({str(session['subject_id']): {'labs': result}})
    return tuple(result)


def validate_numbers(session, numbers, *, allow_empty=False, conn=None):
    labs = labs_for_session(session, conn=conn)
    if not labs:
        raise BotError('У цій черзі немає переліку лабораторних або лімітів.')
    if (not isinstance(numbers, (list, tuple)) or (not numbers and not allow_empty) or
            any(type(n) is not int for n in numbers) or len(numbers) != len(set(numbers))):
        raise BotError('Оберіть конкретні лабораторні без повторів.')
    allowed = {lab['number'] for lab in labs}
    if not set(numbers).issubset(allowed):
        raise BotError('Такої лабораторної немає в переліку цього предмета.')
    return sorted(numbers)


def selection_limit(session, numbers, *, conn=None):
    numbers = validate_numbers(session, numbers, conn=conn)
    return sum(lab['max_minutes'] for lab in labs_for_session(session, conn=conn) if lab['number'] in numbers)


def validate_selection_time(session, numbers, minutes, *, conn=None):
    validate_planned_minutes(minutes)
    if minutes is not None and minutes > selection_limit(session, numbers, conn=conn):
        raise BotError(f'Максимальний час для обраних лабораторних — {selection_limit(session, numbers, conn=conn)} хв.')


def read_numbers(raw):
    if raw is None:
        return None
    try:
        result = json.loads(raw)
        if (not isinstance(result, list) or any(type(n) is not int for n in result) or
                len(result) != len(set(result))):
            raise ValueError
        return sorted(result)
    except (ValueError, TypeError):
        raise BotError('Не вдалося прочитати перелік лабораторних. Оберіть його ще раз.') from None


def parse_numbers(text, session, *, allowed=None, conn=None):
    parts = re.split(r'[,;\s]+', text.strip().replace('№', ''))
    if not parts or any(not part.isascii() or not part.isdigit() or len(part) > 3 for part in parts):
        raise BotError('Введіть номери через кому, наприклад: 1, 2.')
    numbers = validate_numbers(session, [int(part) for part in parts], conn=conn)
    if allowed is not None and not set(numbers).issubset(allowed):
        raise BotError('Оберіть лише лабораторні з вашого поточного запису.')
    return numbers


def format_numbers(numbers):
    return ', '.join(f'№{number}' for number in numbers)
