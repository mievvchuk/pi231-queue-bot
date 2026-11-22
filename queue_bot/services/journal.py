"""Students enter their own grades; administrators may edit anyone's. No approval step."""
import json
import math
import re
import uuid
from collections import defaultdict
from datetime import date

from queue_bot.services import admins, users, schedule
from queue_bot.services.errors import BotError, PermissionDenied
from queue_bot.services.lesson_scheduler import subject_name
from queue_bot.services.journal_layout import layout, summary
from queue_bot.storage.database import transaction, PGConnectionWrapper


_PHYSICAL_EDUCATION = "Фізичне виховання"
_SUBJECT_SHORTCUTS = {
    **dict.fromkeys(("фв", "фіз", "фізра", "фізкультура", "фізвиховання"), _PHYSICAL_EDUCATION),
    "пві": "Програмування в Інтернет",
}


def current_period():
    today = schedule.get_current_date()
    start = today.year if today.month >= 9 else today.year - 1
    return f'{start}-{start + 1}:{1 if today.month >= 9 or today.month == 1 else 2}'


def period_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'20\d{2}-20\d{2}:[12]', value):
        raise BotError('Оберіть навчальний рік і семестр.')
    if int(value[5:9]) != int(value[:4]) + 1:
        raise BotError('Некоректний навчальний рік.')
    return value


def catalog(subjects, schedule_data=None, *, conn=None):
    names = {subject_name(s['name']): s['name'] for s in subjects}
    names.setdefault(subject_name(_PHYSICAL_EDUCATION), _PHYSICAL_EDUCATION)
    data = schedule.read_schedule(conn) if schedule_data is None else schedule_data
    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get('subject'), str) and value['subject'].strip():
                label = value['subject'].strip()
                names.setdefault(subject_name(label), label)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(data)
    return [{'key': key, 'name': name, **layout(name)} for key, name in sorted(names.items(), key=lambda item: users.ukrainian_name_key(item[1]))]


def match_subjects(subjects, label):
    key = subject_name(label)
    if not key:
        return []
    key = subject_name(_SUBJECT_SHORTCUTS.get(key, key))
    exact = [subject for subject in subjects if subject["key"] == key]
    return exact or [subject for subject in subjects if subject["key"].startswith(key)]


def submit_grade_series(conn, uid, subject, digits, *, period=None):
    """Write one grade per configured journal cell in table order."""
    actor = users.get_user(conn, uid)
    if not actor:
        raise PermissionDenied("Спочатку зареєструйтесь у боті.")
    if not re.fullmatch(r"[0-5]+", digits):
        raise BotError("Запишіть оцінки одним рядком цифр від 0 до 5.")
    period = period_value(period or current_period())
    cells = [
        (work["number"], part)
        for work in sorted(subject["labs"], key=lambda row: (row["attestation"], row["number"]))
        for part in range(1, work["parts"] + 1)
    ]
    if len(digits) > len(cells):
        raise BotError(f"У цьому предметі {len(cells)} клітинок. Надішліть не більше {len(cells)} оцінок.")

    with transaction(conn):
        from queue_bot.services.queues import _require_roster_user
        _require_roster_user(conn, actor["id"])
        for (number, part), digit in zip(cells, digits):
            row = conn.execute(
                """INSERT INTO journal_cells
                   (id, user_id, period, subject_key, subject_name, kind, number, part, grade)
                   VALUES (?, ?, ?, ?, ?, 'lab', ?, ?, ?)
                   ON CONFLICT (user_id, period, subject_key, kind, number, part) DO UPDATE
                   SET grade = excluded.grade, subject_name = excluded.subject_name,
                       revision = journal_cells.revision + 1, updated_at = CURRENT_TIMESTAMP
                   WHERE journal_cells.grade <> excluded.grade
                   RETURNING id""",
                (uuid.uuid4().hex, actor["id"], period, subject["key"], subject["name"], number, part, int(digit)),
            ).fetchone()
            if row:
                _audit(conn, row["id"], uid, "submit")
    return period


def _number(value):
    if type(value) is not int or not 1 <= value <= 100:
        raise BotError('Номер роботи має бути від 1 до 100.')
    return value


def _text(value, maximum=500):
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise BotError(f'Текст має містити не більше {maximum} символів.')
    return value.strip()


def _record(conn, record_id):
    if type(record_id) is int and record_id > 0:
        record_id = f'legacy:{record_id}'
    if not isinstance(record_id, str) or not re.fullmatch(r'(?:[a-f0-9]{32}|legacy:[1-9][0-9]*)', record_id):
        raise BotError('Оберіть запис журналу.')
    lock = ' FOR UPDATE' if isinstance(conn, PGConnectionWrapper) else ''
    row = conn.execute('SELECT * FROM journal_cells WHERE id = ?' + lock, (record_id,)).fetchone()
    if not row:
        raise BotError('Запис журналу не знайдено.')
    return row


def _revision(row, revision):
    if type(revision) is not int or revision != row['revision']:
        raise BotError('Запис уже змінився. Оновіть журнал перед збереженням.')


def _audit(conn, record_id, uid, action):
    row = conn.execute('SELECT * FROM journal_cells WHERE id = ?', (record_id,)).fetchone()
    conn.execute('INSERT INTO journal_cell_events(id, record_id, actor_id, action, snapshot) VALUES (?, ?, ?, ?, ?)',
                 (uuid.uuid4().hex, record_id, uid, action, json.dumps(dict(row), ensure_ascii=False, default=str)))


def submit(conn, uid, subjects, data, *, admin_mode=False):
    actor = users.get_user(conn, uid)
    if not actor:
        raise PermissionDenied('Спочатку зареєструйтесь у боті.')
    target_id = data.get('user_id', actor['id'])
    if type(target_id) is not int:
        raise BotError('Оберіть студента.')
    if target_id != actor['id']:
        if not admin_mode:
            raise PermissionDenied('Можна змінювати лише власні оцінки.')
        admins.require_admin(conn, uid)
    period = period_value(data.get('period', current_period()))
    subject = next((s for s in catalog(subjects, conn=conn) if s['key'] == data.get('subject_key')), None)
    if not subject:
        raise BotError('Оберіть предмет зі списку журналу.')
    kind = data.get('kind')
    if kind not in ('lab', 'attestation'):
        raise BotError('Оберіть лабораторну або атестацію.')
    number = _number(data.get('number'))
    part = data.get('part', 1)
    if type(part) is not int or not 1 <= part <= 5:
        raise BotError('Оберіть коректну клітинку оцінки.')
    if subject.get("coursework_for") and (kind, number, part) != ("lab", 1, 1):
        raise BotError("Для курсової роботи є одна оцінка.")
    grade = data.get('grade')
    if type(grade) not in (int, float) or not math.isfinite(grade) or not 0 <= grade <= 5:
        raise BotError('Оцінка має бути від 0 до 5.')
    note = _text(data.get('note', ''))
    with transaction(conn):
        from queue_bot.services.queues import _require_roster_user
        _require_roster_user(conn, target_id)
        record_id = data.get('record_id')
        previous = _record(conn, record_id) if record_id is not None else None
        allowed_parts = next((work['parts'] for work in subject['labs'] if work['number'] == number), 0) if kind == 'lab' else 1
        same_cell = previous is not None and (previous['period'], previous['subject_key'], previous['kind'], previous['number'], previous['part']) == (period, subject['key'], kind, number, part)
        if not same_cell and not 1 <= part <= allowed_parts:
            raise BotError('Цієї клітинки немає у структурі предмета. Оберіть лабораторну й номер оцінки з таблиці.')
        if record_id is not None:
            row = previous
            record_id = row['id']
            if row['user_id'] != target_id:
                raise PermissionDenied('Цей запис належить іншому студенту.')
            _revision(row, data.get('revision'))
            duplicate = conn.execute('''SELECT 1 FROM journal_cells WHERE user_id = ? AND period = ?
                AND subject_key = ? AND kind = ? AND number = ? AND part = ? AND id != ?''',
                (target_id, period, subject['key'], kind, number, part, record_id)).fetchone()
            if duplicate:
                raise BotError('Запис з таким предметом і номером уже є. Відредагуйте його.')
            conn.execute("""UPDATE journal_cells SET period = ?, subject_key = ?, subject_name = ?,
                kind = ?, number = ?, part = ?, grade = ?, note = ?, revision = revision + 1,
                updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (period, subject['key'], subject['name'], kind, number, part, grade, note, record_id))
        else:
            exists = conn.execute('''SELECT 1 FROM journal_cells WHERE user_id = ? AND period = ?
                AND subject_key = ? AND kind = ? AND number = ? AND part = ?''', (target_id, period, subject['key'], kind, number, part)).fetchone()
            if exists:
                raise BotError('Такий запис уже є. Відкрийте його й натисніть «Редагувати».')
            record_id = uuid.uuid4().hex
            conn.execute('''INSERT INTO journal_cells
                (id, user_id, period, subject_key, subject_name, kind, number, part, grade, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (record_id, target_id, period, subject['key'], subject['name'], kind, number, part, grade, note))
        _audit(conn, record_id, uid, 'submit')
    return record_id


def set_date(conn, uid, subjects, data):
    admins.require_owner(conn, uid)
    period = period_value(data.get('period', current_period()))
    key = data.get('subject_key')
    subject = next((s for s in catalog(subjects, conn=conn) if s['key'] == key), None)
    if not subject:
        raise BotError('Оберіть предмет зі списку журналу.')
    if subject.get("coursework_for"):
        raise BotError("Для курсової роботи немає атестацій.")
    number = _number(data.get('number'))
    raw = data.get('due_date')
    if raw is not None and not isinstance(raw, str):
        raise BotError('Вкажіть коректну дату атестації.')
    try:
        due = date.fromisoformat(raw).isoformat() if raw else None
    except (ValueError, TypeError):
        raise BotError('Вкажіть коректну дату атестації.')
    with transaction(conn):
        conn.execute('''INSERT INTO journal_dates(period, subject_key, number, due_date, updated_by)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(period, subject_key, number)
            DO UPDATE SET due_date = excluded.due_date, updated_by = excluded.updated_by''',
            (period, key, number, due, uid))
        admins.log_action(conn, uid, 'journal_date', f'{period} {key} {number} {due}')


def view(conn, uid, subjects, *, group=False, period=None):
    actor = users.get_user(conn, uid)
    if not actor:
        raise PermissionDenied('Спочатку зареєструйтесь у боті.')
    if group:
        admins.require_admin(conn, uid)
    period = period_value(period or current_period())
    records = [dict(row) for row in conn.execute('''SELECT r.*, u.full_name AS student_name
        FROM journal_cells r JOIN users u ON u.id = r.user_id
        WHERE r.period = ? AND u.deleted_at IS NULL
        ORDER BY r.subject_name, r.kind, r.number, r.part, r.id''', (period,)).fetchall()]
    records.sort(key=lambda r: (users.ukrainian_name_key(r['student_name']), r['subject_key'], r['kind'], r['number'], r['part']))
    for record in records:
        for field in ('reviewed_by', 'status', 'review_note'):
            record.pop(field, None)
        record['can_edit'] = group or record['user_id'] == actor['id']
        if not record['can_edit']:
            record.pop('note', None)
    periods = {current_period(), period}
    periods.update(row['period'] for row in conn.execute('SELECT DISTINCT period FROM journal_cells').fetchall())
    subject_rows = catalog(subjects, conn=conn)
    known = {row['key'] for row in subject_rows}
    for record in records:
        if record['subject_key'] not in known:
            subject_rows.append({'key': record['subject_key'], 'name': record['subject_name'], **layout(record['subject_name'])})
            known.add(record['subject_key'])
    students = users.list_users(conn)
    by_student_subject = defaultdict(list)
    for record in records:
        by_student_subject[record['user_id'], record['subject_key']].append(record)
    coursework_by_subject = {row["coursework_for"]: row["key"] for row in subject_rows if row.get("coursework_for")}
    averages = {}
    for student in students:
        student_id = student["id"]
        subject_averages = {}
        for subject in subject_rows:
            key = subject["key"]
            linked = coursework_by_subject.get(key)
            coursework = by_student_subject.get((student_id, linked), ()) if linked else None
            subject_averages[key] = summary(subject, by_student_subject.get((student_id, key), ()), coursework_cells=coursework)
        averages[str(student_id)] = subject_averages
    return {'period': period, 'periods': sorted(periods, reverse=True), 'subjects': subject_rows, 'averages': averages,
            'records': records, 'group': group, 'viewer_id': actor['id'],
            'can_set_dates': group and admins.is_owner(conn, uid),
            'students': [{'id': row['id'], 'name': row['full_name']} for row in students],
            'dates': [dict(row) for row in conn.execute('SELECT subject_key, number, due_date FROM journal_dates WHERE period = ?', (period,)).fetchall()]}
