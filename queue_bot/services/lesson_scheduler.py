"""Start attendance from the same effective timetable shown in the bot."""
import logging
import re
from datetime import datetime

from queue_bot import config
from queue_bot.services import attendance, queues, schedule
from queue_bot.services.subjects import find_subject, find_teacher
from queue_bot.storage.database import transaction

log = logging.getLogger(__name__)
START_DELAY_SECONDS = 5 * 60


def normalized(value):
    return ''.join(c for c in str(value or '').casefold() if c.isalnum())


def subject_name(value):
    key = normalized(value)
    aliases = {
        'кпз': normalized('Конструювання програмного забезпечення'),
        'лмв': normalized('Людино-машинна взаємодія'),
        normalized('Проєкт інтерфейсів користувача'): normalized('Проєкт інтерфейсів користувача ПС'),
    }
    return aliases.get(key, key)


def matches(subjects, session, lesson):
    if session['queue_type'] != 'lesson':
        return False
    subject = find_subject(subjects, session['subject_id'])
    teacher = find_teacher(subject, session['teacher_id']) if subject else None
    if not teacher or subject_name(subject['name']) != subject_name(lesson.get('subject')):
        return False
    raw = lesson.get('teachers_raw')
    if raw:
        names = [t.get('name', '') for t in raw]
    else:
        text = re.sub(r'\([^)]*\)', '', str(lesson.get('teacher') or ''))
        names = re.split(r'[,;/\n]+', text)
    return normalized(teacher['name']) in {normalized(name) for name in names if normalized(name)}


def slot_times(day, lesson):
    start, end = schedule.PAIR_TIMES.get(lesson['number'], ('', ''))
    if not start or not end:
        return None
    begin = datetime.combine(day, datetime.strptime(start, '%H:%M').time(), schedule.UK_TZ)
    finish = datetime.combine(day, datetime.strptime(end, '%H:%M').time(), schedule.UK_TZ)
    return int(begin.timestamp()), int(finish.timestamp())


def key_for(sid, day, number):
    return f'lesson_schedule:{sid}:{day.isoformat()}:{number}'


def suppress_current(conn, sid, now):
    """An explicit stop must not be undone by the next automatic check."""
    local = datetime.fromtimestamp(now, schedule.UK_TZ)
    for number in schedule.PAIR_TIMES:
        begin, end = slot_times(local.date(), {'number': number})
        if begin <= now < end:
            conn.execute('''INSERT INTO bot_state(key, value) VALUES (?, 'stopped')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value''',
                (key_for(sid, local.date(), number),))


def sync_schedule(conn, subjects, *, now=None, schedule_data=None):
    now = attendance.now_seconds() if now is None else now
    local = datetime.fromtimestamp(now, schedule.UK_TZ)
    data = schedule.load_schedule_data() if schedule_data is None else schedule_data
    lessons, _ = schedule.get_actual_schedule(conn, data, local.date(), config.STUDENT_GROUP)
    current = []
    for lesson in lessons:
        times = slot_times(local.date(), lesson)
        if times and times[0] <= now < times[1]:
            current.append((lesson, *times))
    # The key persists even after stop/restart, independently of any in-memory timer.
    for session in queues.list_sessions(conn, ('open', 'closed')):
        sid = session['id']
        matching = [(lesson, start, end) for lesson, start, end in current if matches(subjects, session, lesson)]
        with transaction(conn):
            attendance._lock(conn, sid)
            session = queues.get_session(conn, sid)
            if not session or session['status'] not in ('open', 'closed'):
                continue
            run = attendance.active_run(conn, sid, now=now)
            # A replacement/cancellation must also stop an automatically started run.
            if run:
                scheduled = conn.execute("SELECT key FROM bot_state WHERE key LIKE ? AND value = ?",
                    (f'lesson_schedule:{sid}:%', str(run['id']))).fetchone()
                if scheduled and not matching:
                    conn.execute('UPDATE lesson_runs SET active = 0 WHERE id = ?', (run['id'],))
                    run = None
            if not matching:
                continue
            lesson, begin, end = matching[0]
            if now < begin + START_DELAY_SECONDS:
                continue
            key = key_for(sid, local.date(), lesson['number'])
            saved = conn.execute('SELECT value FROM bot_state WHERE key = ?', (key,)).fetchone()
            if saved and saved['value'] == 'stopped':
                continue
            # Open signup even for an empty queue so students can join. Keep a
            # separate marker for upgrades of already-running lessons and never
            # undo an administrator's subsequent manual closure on every tick.
            signup_key = key + ':signup'
            if not conn.execute('SELECT key FROM bot_state WHERE key = ?', (signup_key,)).fetchone():
                conn.execute("UPDATE queue_sessions SET status = 'open' WHERE id = ? AND status = 'closed'", (sid,))
                conn.execute("INSERT INTO bot_state(key, value) VALUES (?, 'opened')", (signup_key,))
                log.info('Scheduled signup opened: queue=%s pair=%s date=%s', sid, lesson['number'], local.date())
            if saved:
                continue
            if run is None:
                conn.execute('UPDATE lesson_runs SET active = 0 WHERE session_id = ? AND active = 1', (sid,))
                rid = conn.execute('INSERT INTO lesson_runs(session_id, started_at, ends_at) VALUES (?, ?, ?)',
                                   (sid, now, end)).lastrowid
                run = conn.execute('SELECT * FROM lesson_runs WHERE id = ?', (rid,)).fetchone()
                attendance._sync(conn, run)
                log.info('Scheduled attendance started: queue=%s pair=%s date=%s', sid, lesson['number'], local.date())
            else:
                # Adopt a manual start without asking everyone twice or extending the class.
                conn.execute('UPDATE lesson_runs SET ends_at = ? WHERE id = ?', (end, run['id']))
            conn.execute('INSERT INTO bot_state(key, value) VALUES (?, ?)', (key, str(run['id'])))
