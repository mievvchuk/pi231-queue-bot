"""Durable manual and timed consultation starts using the attendance workflow."""
import json

from queue_bot.services import attendance, queues
from queue_bot.services.admins import require_admin, is_admin, log_action
from queue_bot.services.errors import BotError
from queue_bot.storage.database import transaction


def key(sid):
    return f'consultation_start:{sid}'


def get_plan(conn, sid):
    row = conn.execute('SELECT value FROM bot_state WHERE key = ?', (key(sid),)).fetchone()
    return json.loads(row['value']) if row else None


def _save(conn, sid, plan):
    conn.execute('''INSERT INTO bot_state(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value''', (key(sid), json.dumps(plan)))


def _consultation(conn, sid):
    session = queues._require_session(conn, sid)
    if session['queue_type'] != 'consultation':
        raise BotError('Ці кнопки доступні лише для консультацій. Пари запускаються за розкладом.')
    return session


def configure(conn, actor, sid, *, minutes, starts_at=None, now=None):
    now = attendance.now_seconds() if now is None else now
    if type(minutes) is not int or not 10 <= minutes <= 240:
        raise BotError('Тривалість консультації — від 10 до 240 хвилин.')
    if starts_at is not None and (type(starts_at) is not int or starts_at <= now):
        raise BotError('Оберіть час початку в майбутньому.')
    with transaction(conn):
        require_admin(conn, actor)
        attendance._lock(conn, sid)
        _consultation(conn, sid)
        if attendance.active_run(conn, sid, now=now):
            raise BotError('Консультація вже триває. Спочатку зупиніть її.')
        if starts_at is None:
            attendance._start_locked(conn, actor, sid, minutes=minutes, now=now)
            conn.execute('DELETE FROM bot_state WHERE key = ?', (key(sid),))
        else:
            _save(conn, sid, dict(starts_at=starts_at, minutes=minutes, actor=actor, status='pending'))
            log_action(conn, actor, 'schedule_consultation', f'{sid}: {starts_at}; {minutes} хв')


def cancel(conn, actor, sid, *, stop=False):
    with transaction(conn):
        require_admin(conn, actor)
        attendance._lock(conn, sid)
        _consultation(conn, sid)
        conn.execute('DELETE FROM bot_state WHERE key = ?', (key(sid),))
        if stop:
            conn.execute('UPDATE lesson_runs SET active = 0 WHERE session_id = ? AND active = 1', (sid,))
        log_action(conn, actor, 'stop_consultation' if stop else 'cancel_consultation_start', str(sid))


def tick(conn, *, now=None):
    now = attendance.now_seconds() if now is None else now
    rows = conn.execute('SELECT key FROM bot_state WHERE key LIKE ?', ('consultation_start:%',)).fetchall()
    for row in rows:
        sid = int(row['key'].split(':')[1])
        with transaction(conn):
            attendance._lock(conn, sid)
            plan = get_plan(conn, sid)
            if not plan or plan['status'] != 'pending' or plan['starts_at'] > now:
                continue
            session = queues.get_session(conn, sid)
            end = plan['starts_at'] + plan['minutes'] * 60
            if not session or session['status'] == 'archived' or session['queue_type'] != 'consultation':
                conn.execute('DELETE FROM bot_state WHERE key = ?', (key(sid),))
                continue
            if not is_admin(conn, plan['actor']):
                plan['status'] = 'cancelled'
            elif end <= now:
                plan['status'] = 'expired'
            elif attendance.active_run(conn, sid, now=now):
                plan['status'] = 'cancelled'
            else:
                attendance._start_locked(conn, plan['actor'], sid, minutes=plan['minutes'], now=now, ends_at=end)
                plan['status'] = 'started'
            _save(conn, sid, plan)
            log_action(conn, plan['actor'], 'consultation_timer', f'{sid}: {plan["status"]}')
