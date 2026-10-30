"""Ten-minute unanswered invitations; accepted exchanges remain repeatable."""
from datetime import datetime, timedelta, timezone
from secrets import token_hex

from queue_bot.services import queues
from queue_bot.services.errors import BotError
from queue_bot.services.restrictions import require_available
from queue_bot.storage.database import PGConnectionWrapper, transaction


def utc_now():
    return datetime.now(timezone.utc)


def _lock_session(conn, sid):
    # Serialize invitations and answers across bot processes as well as tasks.
    suffix = ' FOR UPDATE' if isinstance(conn, PGConnectionWrapper) else ''
    conn.execute('SELECT id FROM queue_sessions WHERE id = ?' + suffix, (sid,)).fetchone()


def _expire(conn, now):
    conn.execute("UPDATE swap_requests SET status = 'expired' WHERE status = 'pending' AND expires_at <= ?", (now.isoformat(),))


def create(conn, actor_id, sender_id, target_id, *, now=None):
    now = now or utc_now()
    require_available(conn, actor_id)
    with transaction(conn):
        sender = queues.get_entry(conn, sender_id)
        if not sender:
            raise BotError('Цей запис уже неактивний.')
        _lock_session(conn, sender['session_id'])
        sender = queues._require_active_entry(conn, sender_id)
        target = queues._require_active_entry(conn, target_id)
        if sender['user_id'] is None or target['user_id'] is None:
            raise BotError('Це вільне місце, а не студент.')
        owner = conn.execute('SELECT telegram_id FROM users WHERE id = ?', (sender['user_id'],)).fetchone()
        if not owner or owner['telegram_id'] != actor_id:
            raise BotError('Ця кнопка призначена для іншого студента.')
        if sender['session_id'] != target['session_id'] or sender_id == target_id:
            raise BotError('Оберіть іншого студента в цій самій черзі.')
        _expire(conn, now)
        left, right = sorted((sender['user_id'], target['user_id']))
        existing = conn.execute("SELECT id FROM swap_requests WHERE session_id = ? AND pair_left = ? AND pair_right = ? AND status = 'pending'", (sender['session_id'], left, right)).fetchone()
        if existing:
            raise BotError('Запит між вами вже активний. На відповідь є 10 хв від його надсилання; після цього можна надіслати новий.')
        token = token_hex(12)
        request_id = conn.execute(
            '''INSERT INTO swap_requests (token, session_id, sender_entry_id, target_entry_id,
               sender_user_id, target_user_id, sender_revision, target_revision,
               sender_position, target_position, pair_left, pair_right, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (token, sender['session_id'], sender_id, target_id, sender['user_id'], target['user_id'],
             sender['entry_revision'], target['entry_revision'], sender['position'], target['position'],
             left, right, (now + timedelta(minutes=10)).isoformat()),
        ).lastrowid
        return conn.execute('SELECT * FROM swap_requests WHERE id = ?', (request_id,)).fetchone()


def cancel_delivery(conn, token):
    conn.execute("UPDATE swap_requests SET status = 'cancelled' WHERE token = ? AND status = 'pending'", (token,))


def respond(conn, actor_id, token, *, accept, now=None):
    now = now or utc_now()
    require_available(conn, actor_id)
    error = None
    result = None
    with transaction(conn):
        request = conn.execute('SELECT * FROM swap_requests WHERE token = ?', (token,)).fetchone()
        if not request:
            raise BotError('Цей запит застарів. Попросіть надіслати новий.')
        _lock_session(conn, request['session_id'])
        request = conn.execute('SELECT * FROM swap_requests WHERE token = ?', (token,)).fetchone()
        if not request:
            raise BotError('Цей запит застарів. Попросіть надіслати новий.')
        recipient = conn.execute('SELECT telegram_id FROM users WHERE id = ?', (request['target_user_id'],)).fetchone()
        if not recipient or recipient['telegram_id'] != actor_id:
            raise BotError('Ця пропозиція призначена для іншого студента.')
        _expire(conn, now)
        if request['status'] == 'expired' or (request['status'] == 'pending' and request['expires_at'] <= now.isoformat()):
            error = 'Час на відповідь (10 хв) минув. Запит закрито; можна надіслати новий.'
        elif request['status'] != 'pending' and not (accept and request['status'] == 'accepted'):
            error = 'Цей запит уже закрито. Для нового обміну надішліть новий запит.'
        else:
            try:
                if isinstance(conn, PGConnectionWrapper):
                    conn.execute('SELECT id FROM queue_entries WHERE id IN (?, ?) ORDER BY id FOR UPDATE',
                                 (request['sender_entry_id'], request['target_entry_id'])).fetchall()
                for role in ('sender', 'target'):
                    entry = queues._require_active_entry(conn, request[f'{role}_entry_id'])
                    fields = [('user_id', 'user_id'), ('entry_revision', 'revision')]
                    if request['status'] == 'pending':
                        fields.append(('position', 'position'))
                    if any(entry[key] != request[f'{role}_{field}'] for key, field in fields):
                        raise BotError('Чергу змінено. Надішліть новий запит на обмін.')
                result = queues._swap_entries_in_transaction(conn, request['sender_entry_id'], request['target_entry_id']) if accept else None
            except BotError as exc:
                error = str(exc)
            conn.execute('UPDATE swap_requests SET status = ? WHERE id = ?',
                         ('cancelled' if error else ('accepted' if accept else 'declined'), request['id']))
    # Commit expiration/cancellation even when the caller receives a BotError.
    if error:
        raise BotError(error)
    return request, result
