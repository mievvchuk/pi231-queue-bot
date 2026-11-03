"""Per-lesson availability, independent of the persistent queue priority."""
import secrets
import time

from queue_bot.services import queues
from queue_bot.services.admins import require_admin, log_action
from queue_bot.services.errors import BotError
from queue_bot.services.restrictions import require_available
from queue_bot.storage.database import transaction, PGConnectionWrapper

RESPONSE_SECONDS = 10 * 60
LABELS = {'pending': 'Присутність не підтверджена', 'present': 'На місці',
          'late': 'Запізнюється', 'absent': 'На цій парі не буде',
          'skipped': 'Тимчасово пропущено', 'called': 'Викликано: очікуємо відповідь',
          'serving': 'Зараз здає'}




def now_seconds():
    return int(time.time())


def _lock(conn, sid):
    suffix = ' FOR UPDATE' if isinstance(conn, PGConnectionWrapper) else ''
    conn.execute('SELECT id FROM queue_sessions WHERE id = ?' + suffix, (sid,)).fetchone()


def active_run(conn, sid, *, now=None):
    return conn.execute('SELECT * FROM lesson_runs WHERE session_id = ? AND active = 1 AND ends_at > ?',
                        (sid, now_seconds() if now is None else now)).fetchone()


def members(conn, run_id):
    return conn.execute('''SELECT a.*, e.position, u.telegram_id, u.full_name FROM lesson_attendance a
        JOIN queue_entries e ON e.id = a.entry_id AND e.user_id = a.user_id AND e.status = 'active'
        JOIN users u ON u.id = a.user_id
        WHERE a.run_id = ? ORDER BY e.position''', (run_id,)).fetchall()


def _event(conn, row, kind):
    conn.execute('''INSERT INTO lesson_messages(attendance_id, revision, kind) VALUES (?, ?, ?)
        ON CONFLICT(attendance_id, revision, kind) DO NOTHING''', (row['id'], row['revision'], kind))


def _change(conn, row, state, deadline=None):
    conn.execute('''UPDATE lesson_attendance SET state = ?, deadline = ?, revision = revision + 1,
        token = ? WHERE id = ?''', (state, deadline, secrets.token_hex(10), row['id']))
    return conn.execute('SELECT * FROM lesson_attendance WHERE id = ?', (row['id'],)).fetchone()


def _sync(conn, run):
    # A vacant slot may be reused by a different student; never inherit their attendance.
    conn.execute('''DELETE FROM lesson_attendance WHERE run_id = ? AND NOT EXISTS
        (SELECT 1 FROM queue_entries e WHERE e.id = lesson_attendance.entry_id
         AND e.user_id = lesson_attendance.user_id AND e.status = 'active')''', (run['id'],))
    for entry in queues.get_entries(conn, run['session_id']):
        if entry['user_id'] is None:
            continue
        existing = conn.execute('SELECT id FROM lesson_attendance WHERE run_id = ? AND entry_id = ?',
                                (run['id'], entry['id'])).fetchone()
        if not existing:
            aid = conn.execute('INSERT INTO lesson_attendance(run_id, entry_id, user_id, token) VALUES (?, ?, ?, ?)',
                               (run['id'], entry['id'], entry['user_id'], secrets.token_hex(10))).lastrowid
            _event(conn, conn.execute('SELECT * FROM lesson_attendance WHERE id = ?', (aid,)).fetchone(), 'presence')


def _advance(conn, run, now):
    _sync(conn, run)
    for row in members(conn, run['id']):
        if row['deadline'] is None or row['deadline'] > now:
            continue
        if row['state'] in ('pending', 'called'):
            _event(conn, _change(conn, row, 'skipped'), 'skipped')
        elif row['state'] == 'late':
            _event(conn, _change(conn, row, 'pending'), 'reminder')
    rows = members(conn, run['id'])
    busy = next((r for r in rows if r['state'] in ('called', 'serving')), None)
    if busy is None:
        first = next((r for r in rows if r['state'] == 'present'), None)
        if first:
            # Delivery arms the 10-minute deadline, rather than time waiting in the outbox.
            busy = _change(conn, first, 'called')
            _event(conn, busy, 'call')
    eligible = [r for r in members(conn, run['id']) if r['state'] in ('present', 'called', 'serving')]
    if busy:
        eligible = [r for r in eligible if r['id'] == busy['id']] + [r for r in eligible if r['id'] != busy['id']]
    if len(eligible) >= 3:
        third = eligible[2]
        if not third['warned']:
            _event(conn, third, 'warning')
            conn.execute('UPDATE lesson_attendance SET warned = 1 WHERE id = ?', (third['id'],))


def start(conn, actor, sid, *, minutes=90, now=None):
    if type(minutes) is not int or not 10 <= minutes <= 240:
        raise BotError('Тривалість пари має бути від 10 до 240 хвилин.')
    now = now_seconds() if now is None else now
    with transaction(conn):
        require_admin(conn, actor)
        _lock(conn, sid)
        queues._require_session(conn, sid)
        if active_run(conn, sid, now=now):
            raise BotError('Режим здачі вже запущено для цієї черги.')
        conn.execute('UPDATE lesson_runs SET active = 0 WHERE session_id = ? AND active = 1', (sid,))
        rid = conn.execute('INSERT INTO lesson_runs(session_id, started_at, ends_at) VALUES (?, ?, ?)',
                           (sid, now, now + minutes * 60)).lastrowid
        run = conn.execute('SELECT * FROM lesson_runs WHERE id = ?', (rid,)).fetchone()
        _sync(conn, run)
        log_action(conn, actor, 'start_lesson', f'{sid}: {minutes} хв; відповідь 10 хв')
    return rid


def stop(conn, actor, sid):
    with transaction(conn):
        require_admin(conn, actor)
        _lock(conn, sid)
        queues._require_session(conn, sid)
        from queue_bot.services.lesson_scheduler import suppress_current
        suppress_current(conn, sid, now_seconds())
        conn.execute('UPDATE lesson_runs SET active = 0 WHERE session_id = ? AND active = 1', (sid,))
        log_action(conn, actor, 'stop_lesson', str(sid))


def view(conn, sid, uid):
    run = active_run(conn, sid)
    if not run:
        return None
    rows = members(conn, run['id'])
    own = next((r for r in rows if r['telegram_id'] == uid), None)
    eligible = [r for r in rows if r['state'] in ('present', 'called', 'serving')]
    current = next((r for r in eligible if r['state'] in ('called', 'serving')), None)
    # A late arrival keeps priority but cannot preempt an existing call/turn.
    ordered = ([current] if current else []) + [r for r in eligible if not current or r['id'] != current['id']]
    rank = next((i for i, r in enumerate(ordered) if own and r['id'] == own['id']), None)
    return dict(id=run['id'], ends_at=run['ends_at'], response_minutes=RESPONSE_SECONDS // 60,
                current_name=current['full_name'] if current else None,
                people_ahead=rank, own=dict(own) if own else None,
                states={r['entry_id']: LABELS.get(r['state'], r['state']) for r in rows})


def respond(conn, uid, token, action, *, minutes=None, now=None):
    now = now_seconds() if now is None else now
    require_available(conn, uid)
    with transaction(conn):
        row = conn.execute('SELECT a.*, r.session_id FROM lesson_attendance a JOIN lesson_runs r ON r.id = a.run_id WHERE a.token = ?', (token,)).fetchone()
        if not row:
            raise BotError('Ця кнопка застаріла. Оновіть чергу.')
        _lock(conn, row['session_id'])
        run = active_run(conn, row['session_id'], now=now)
        row = next((r for r in members(conn, run['id']) if r['token'] == token and r['telegram_id'] == uid), None) if run else None
        if not row:
            raise BotError('Ця кнопка застаріла або призначена іншому студенту.')
        if row['state'] == 'serving':
            raise BotError('Спочатку завершіть поточний підхід.')
        if action == 'ready':
            if row['state'] != 'called' or (row['deadline'] is not None and row['deadline'] <= now):
                raise BotError('Виклик уже неактуальний. Підтвердьте присутність у черзі.')
            _change(conn, row, 'serving')
        elif action == 'present':
            # Presence does not replace the separate confirmation of a call.
            if row['state'] == 'called':
                raise BotError('Підтвердьте готовність кнопкою у виклику.')
            _event(conn, _change(conn, row, 'present'), 'confirmed')
        elif action == 'late':
            if type(minutes) is not int or not 1 <= minutes <= 240:
                raise BotError('Вкажіть час запізнення від 1 до 240 хвилин.')
            _event(conn, _change(conn, row, 'late', now + minutes * 60), 'confirmed')
        elif action in ('absent', 'skip'):
            _event(conn, _change(conn, row, 'absent' if action == 'absent' else 'skipped'), 'confirmed')
        else:
            raise BotError('Невідома дія.')
        _advance(conn, run, now)
    return run['session_id']


def require_turn(conn, entry):
    run = active_run(conn, entry['session_id'])
    if run:
        row = conn.execute("SELECT id FROM lesson_attendance WHERE run_id = ? AND entry_id = ? AND user_id = ? AND state = 'serving'",
                           (run['id'], entry['id'], entry['user_id'])).fetchone()
        if not row:
            raise BotError('Спочатку дочекайтеся виклику й підтвердьте готовність здавати.')


def end_turn(conn, entry):
    # Called inside the same transaction as moving/closing a queue entry.
    conn.execute("UPDATE lesson_attendance SET state = 'present', deadline = NULL, warned = 0, revision = revision + 1, token = ? WHERE entry_id = ? AND user_id = ? AND state IN ('called', 'serving') AND run_id IN (SELECT id FROM lesson_runs WHERE active = 1)",
                 (secrets.token_hex(10), entry['id'], entry['user_id']))


def correct(conn, actor, entry_id, action, *, run_id=None):
    """Explicit administrator overrides, with the same single-turn invariant."""
    with transaction(conn):
        require_admin(conn, actor)
        entry = queues._require_active_entry(conn, entry_id)
        _lock(conn, entry['session_id'])
        run = active_run(conn, entry['session_id'])
        row = next((r for r in members(conn, run['id']) if r['entry_id'] == entry_id), None) if run else None
        if not row or (run_id is not None and run['id'] != run_id):
            raise BotError('На цій парі запис неактивний. Оновіть чергу.')
        if action == 'finish':
            if row['state'] != 'serving':
                raise BotError('Студент зараз не здає.')
            end_turn(conn, entry)
            ids = queues._ordered_ids(conn, entry['session_id'])
            ids.remove(entry_id)
            ids.append(entry_id)
            queues._set_order(conn, entry['session_id'], ids)
            conn.execute('UPDATE queue_entries SET entry_revision = entry_revision + 1 WHERE id = ?', (entry_id,))
        elif action in ('present', 'skipped', 'absent'):
            if row['state'] == 'serving':
                raise BotError('Спочатку завершіть підхід студента.')
            _event(conn, _change(conn, row, action), 'confirmed')
        else:
            raise BotError('Невідома дія адміністратора.')
        log_action(conn, actor, 'correct_attendance', f'{entry_id}: {action}')
        _advance(conn, run, now_seconds())


def tick(conn, *, now=None):
    now = now_seconds() if now is None else now
    with transaction(conn):
        for row in conn.execute('SELECT * FROM lesson_runs WHERE active = 1').fetchall():
            _lock(conn, row['session_id'])
            run = conn.execute('SELECT * FROM lesson_runs WHERE id = ? AND active = 1', (row['id'],)).fetchone()
            if not run:
                continue
            session = queues.get_session(conn, run['session_id'])
            if run['ends_at'] <= now or not session or session['status'] == 'archived':
                conn.execute('UPDATE lesson_runs SET active = 0 WHERE id = ?', (run['id'],))
            else:
                _advance(conn, run, now)
