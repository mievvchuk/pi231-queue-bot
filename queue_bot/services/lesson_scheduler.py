"""Start attendance from the same effective timetable shown in the bot."""
import logging
import re
from datetime import datetime

from queue_bot import config
from queue_bot.services import attendance, queues, schedule, air_alerts
from queue_bot.services.subjects import find_subject, find_teacher
from queue_bot.storage.database import transaction

log = logging.getLogger(__name__)
START_DELAY_SECONDS = 5 * 60


def normalized(value):
    return ''.join(c for c in str(value or '').casefold() if c.isalnum())


def subject_name(value):
    def key_for_name(name):
        return normalized(name).replace('проект', 'проєкт')

    aliases = {
        'кпз': normalized('Конструювання програмного забезпечення'),
        'лмв': normalized('Людино-машинна взаємодія'),
        'мн': normalized('Машинне навчання'),
        'пі': normalized('Програмування в Інтернет'),
        'атпз': normalized('Архітектура та проєкт ПЗ'),
        'пікпс': normalized('Проєкт інтерфейсів користувача ПС'),
        normalized('Проєкт інтерфейсів'): normalized('Проєкт інтерфейсів користувача ПС'),
        normalized('Проєкт інтерфейсів користувача'): normalized('Проєкт інтерфейсів користувача ПС'),
    }
    key = key_for_name(value)
    # Recognition displays both the expanded name and its acronym, e.g. «… (ЛМВ)».
    # Ignore the suffix only when both parts identify the same known subject.
    decorated = re.fullmatch(r'(.*?)\s*\(([^()]*)\)\s*', str(value or ''))
    if decorated:
        base, suffix = (key_for_name(part) for part in decorated.groups())
        if aliases.get(base, base) == aliases.get(suffix, suffix):
            return aliases.get(base, base)
    return aliases.get(key, key)


def _teacher_names(lesson):
    if lesson.get('teachers_raw'):
        return [t.get('name', '') for t in lesson['teachers_raw']]
    text = re.sub(r'\([^)]*\)', '', str(lesson.get('teacher') or ''))
    return [name.strip() for name in re.split(r'[,;/\n]+', text)]


def _matches_teacher(subject, teacher, names, *, allow_surname=False):
    keys = {normalized(name) for name in names}
    if normalized(teacher['name']) in keys:
        return True
    if not allow_surname:
        return False
    surname = normalized(teacher['name'].split()[0])
    return surname in keys and sum(
        normalized(person['name'].split()[0]) == surname for person in subject['teachers']
    ) == 1


def _queues_for_lesson(conn, catalog, sessions, lesson, replacement, planned_lessons):
    lesson_subject = subject_name(lesson.get('subject'))
    candidates = []
    for session in sessions:
        if session['queue_type'] != 'lesson':
            continue
        subject = find_subject(catalog, session['subject_id'])
        if not subject or subject_name(subject['name']) != lesson_subject:
            continue
        teacher = find_teacher(subject, session['teacher_id'])
        if teacher:
            candidates.append((session['id'], subject, teacher))

    if not replacement:
        names = _teacher_names(lesson)
        return [sid for sid, subject, teacher in candidates if _matches_teacher(subject, teacher, names)]

    names = _teacher_names(replacement)
    if any(normalized(name) not in {'', 'невідомо', 'невказано', 'немає'} for name in names):
        return [sid for sid, subject, teacher in candidates
                if _matches_teacher(subject, teacher, names, allow_surname=True)]

    # When only the room changes, keep the matching teacher from the original pair.
    planned = [item for item in planned_lessons if item['number'] == lesson['number']
               and subject_name(item['subject']) == lesson_subject]
    names = [name for item in planned for name in _teacher_names(item)]
    if any(normalized(name) for name in names):
        return [sid for sid, subject, teacher in candidates
                if _matches_teacher(subject, teacher, names, allow_surname=True)]

    # A missing teacher must not block a replacement with one identifiable queue.
    if len(candidates) == 1:
        return [candidates[0][0]]
    occupied = [sid for sid, _, _ in candidates if any(entry['user_id'] for entry in queues.get_entries(conn, sid))]
    return occupied if len(occupied) == 1 else []


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
    if air_alerts.blocked(conn, now):
        return
    local = datetime.fromtimestamp(now, schedule.UK_TZ)
    data = schedule.read_schedule(conn) if schedule_data is None else schedule_data
    lessons, replacements = schedule.get_actual_schedule(conn, data, local.date(), config.STUDENT_GROUP)
    replacements_by_pair = {int(item['lesson_number']): item for item in replacements}
    planned_lessons = schedule.get_planned_schedule(conn, data, local.date()) if replacements else []
    sessions = queues.list_sessions(conn, ('open', 'closed'))
    matching_by_queue = {}
    for lesson in lessons:
        times = slot_times(local.date(), lesson)
        if not times or not times[0] <= now < times[1]:
            continue
        replacement = replacements_by_pair.get(lesson['number'])
        for sid in _queues_for_lesson(conn, subjects, sessions, lesson, replacement, planned_lessons):
            matching_by_queue.setdefault(sid, (lesson, *times))

    # The key persists even after stop/restart, independently of any in-memory timer.
    for session in sessions:
        sid = session['id']
        matching = matching_by_queue.get(sid)
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
            lesson, begin, end = matching
            if now < begin + START_DELAY_SECONDS:
                continue
            key = key_for(sid, local.date(), lesson['number'])
            saved = conn.execute('SELECT value FROM bot_state WHERE key = ?', (key,)).fetchone()
            if saved and saved['value'] == 'stopped':
                continue
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
