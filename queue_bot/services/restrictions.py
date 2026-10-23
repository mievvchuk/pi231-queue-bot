"""Persistent access restrictions, independent of Telegram and FSM storage."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from queue_bot import config
from queue_bot.storage.database import transaction
from queue_bot.services import admins
from queue_bot.services.errors import BotError


def utc_now():
    return datetime.now(timezone.utc)


def restriction_message(conn, telegram_id: int, now=None) -> str | None:
    user = conn.execute('SELECT * FROM users WHERE telegram_id = ?', (telegram_id,)).fetchone()
    if not user or not user['is_blocked']:
        return None
    until = user['banned_until']
    if until:
        try:
            expires = datetime.fromisoformat(until)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return 'Вас заблоковано адміністратором. Зверніться до старости.'
        if expires <= (now or utc_now()):
            # Conditional update cannot accidentally lift a newer restriction.
            conn.execute(
                'UPDATE users SET is_blocked = FALSE, banned_until = NULL '
                'WHERE id = ? AND banned_until = ? AND is_blocked = TRUE',
                (user['id'], until),
            )
            return None
        local = expires.astimezone(ZoneInfo(config.TIMEZONE)).strftime('%d.%m.%Y %H:%M')
        return f'Вас забанено до {local} (за Києвом). Після цього доступ відновиться автоматично.'
    return 'Вас заблоковано безстроково. Зверніться до старости.'


def require_available(conn, telegram_id: int) -> None:
    reason = restriction_message(conn, telegram_id)
    if reason:
        raise BotError(reason)


def expire_due_restrictions(conn) -> None:
    """Refresh timed bans even when the student has not sent a new message."""
    rows = conn.execute(
        'SELECT telegram_id FROM users WHERE is_blocked = TRUE AND banned_until IS NOT NULL'
    ).fetchall()
    for row in rows:
        restriction_message(conn, row['telegram_id'])


def set_restriction(conn, actor_id: int, user_id: int, days=None, now=None) -> str | None:
    admins.require_admin(conn, actor_id)
    if days is not None and (type(days) is not int or not 1 <= days <= 36500):
        raise BotError('Введіть кількість днів від 1 до 36500.')
    expires = ((now or utc_now()) + timedelta(days=days)).isoformat() if days is not None else None
    with transaction(conn):
        user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        if not user:
            raise BotError('Користувача не знайдено.')
        if admins.is_admin(conn, user['telegram_id']) or admins.is_owner(conn, user['telegram_id']):
            raise BotError('Не можна заблокувати адміністратора. Спочатку змініть його права.')
        conn.execute('UPDATE users SET is_blocked = TRUE, banned_until = ? WHERE id = ?', (expires, user_id))
        # One transaction owns both restriction and removals; no nested transaction.
        from queue_bot.services import queues
        entries = conn.execute(
            "SELECT e.* FROM queue_entries e JOIN queue_sessions s ON s.id = e.session_id "
            "WHERE e.user_id = ? AND e.status = 'active' AND s.status IN ('open', 'closed')",
            (user_id,),
        ).fetchall()
        for entry in entries:
            queues._close_entry(conn, entry, 'removed')
        admins.log_action(conn, actor_id, 'ban_user' if expires else 'block_user',
                          f'user={user_id}, until={expires or "indefinite"}, removed={len(entries)}')
    return expires


def clear_restriction(conn, actor_id: int, user_id: int) -> None:
    admins.require_admin(conn, actor_id)
    with transaction(conn):
        if not conn.execute('SELECT 1 FROM users WHERE id = ?', (user_id,)).fetchone():
            raise BotError('Користувача не знайдено.')
        conn.execute('UPDATE users SET is_blocked = FALSE, banned_until = NULL WHERE id = ?', (user_id,))
        admins.log_action(conn, actor_id, 'unblock_user', str(user_id))
