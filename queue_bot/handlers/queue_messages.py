"""Keep refreshes on the queue message owned by this chat and role."""
import json

from aiogram.exceptions import TelegramBadRequest


def _key(message, uid, sid, view):
    return f'queue_message:{message.bot.id}:{message.chat.id}:{uid}:{sid}:{view}'


def remember(conn, message, uid, sid, view):
    if not isinstance(message.message_id, int):
        return
    conn.execute('''INSERT INTO bot_state(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value''',
        (_key(message, uid, sid, view), json.dumps({'message_id': message.message_id})))


async def refresh(message, conn, uid, sid, view, text, *, markup=None, target=None):
    """Return False only when a fresh queue message must be sent."""
    if target is not None:
        message_id = target.message_id
    else:
        row = conn.execute('SELECT value FROM bot_state WHERE key = ?',
                           (_key(message, uid, sid, view),)).fetchone()
        if not row:
            return False
        message_id = json.loads(row['value'])['message_id']
    try:
        await message.bot.edit_message_text(chat_id=message.chat.id, message_id=message_id,
                                           text=text, reply_markup=markup, parse_mode='HTML')
    except TelegramBadRequest as exc:
        reason = str(exc).lower()
        if 'message is not modified' not in reason:
            if any(value in reason for value in ('message to edit not found',
                                                "message can't be edited", 'message can not be edited')):
                return False
            raise
    if target is not None:
        remember(conn, target, uid, sid, view)
    return True
