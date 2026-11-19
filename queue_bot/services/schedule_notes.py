"""Resolve extracted header references using the saved timetable."""
import re
from datetime import date

from queue_bot.services import schedule
from queue_bot.services.errors import BotError


def normalize_group(value):
    return ''.join(str(value).lower().split()).replace('-', '').translate(str.maketrans({'п': 'p', 'і': 'i'}))


def as_scheduled(subject):
    # The photo often cuts the word: «По розклад», «за розкл.».
    return bool(re.match(r'(по|за|згідно(\s+з)?)\s+розкл', str(subject).strip().lower()))


def with_old_subjects(rows, original):
    for row in rows:
        base = original.get(row['lesson_number'], {}).get('subject')
        if not row.get('old_subject'):
            row['old_subject'] = base
        if base and as_scheduled(row['new_subject']):
            row['new_subject'] = base
    return rows


def teacher_and_room(lesson):
    people = lesson.get('teachers_raw') or []
    if lesson.get('room') or not people:
        return lesson.get('teacher') or '—', lesson.get('room') or '—'
    return (', '.join(p['name'] for p in people if p.get('name')) or '—',
            ', '.join(p['room'] for p in people if p.get('room')) or '—')


def resolve_header_notes(result, date_str, group_name, rows, schedule_data=None):
    if result.get('unparsedNotes'):
        raise BotError('Не вдалося однозначно прочитати примітку над таблицею. Надішліть чіткіше фото або внесіть заміну вручну.')
    overrides = result.get('scheduleOverrides', [])
    if not isinstance(overrides, list) or len(overrides) > 10:
        raise BotError('Некоректні примітки над таблицею. Перевірте фото.')
    data = schedule.load_schedule_data() if schedule_data is None else schedule_data
    try:
        target_date = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        raise BotError('Не вдалося визначити дату примітки. Перевірте дату на фото.')
    original = {x['number']: x for x in schedule.get_base_schedule(data, target_date)}
    if not overrides:
        return with_old_subjects(rows, original)
    mapped = {}
    for note in overrides:
        if not isinstance(note, dict):
            raise BotError('Не вдалося прочитати примітку над таблицею.')
        if note.get('group') and normalize_group(note['group']) != normalize_group(group_name):
            continue
        target, source = note.get('targetPair'), note.get('sourcePair')
        day, week = note.get('sourceDay'), note.get('weekType')
        if (type(target) is not int or type(source) is not int or not 1 <= target <= 5 or not 1 <= source <= 5
                or day not in schedule.DAY_KEYS or week not in ('numerator', 'denominator')):
            raise BotError('Не вдалося визначити пару, день або чисельник/знаменник у примітці. Уточніть заміну вручну.')
        lessons = [x for x in schedule.get_base_schedule_for_day(data, day, week) if x['number'] == source]
        if len(lessons) != 1 or not lessons[0]['subject']:
            raise BotError(f'Примітка посилається на пару №{source}, якої немає або яку неоднозначно задано в збереженому розкладі. Перевірте базовий розклад.')
        if target in mapped:
            raise BotError('Кілька приміток змінюють одну пару. Уточніть заміну вручну.')
        lesson = lessons[0]
        teacher, room = teacher_and_room(lesson)
        description = f"{target} пара ← {schedule.DAY_NAMES_UK[day]}, {source} пара, {'знаменник' if week == 'denominator' else 'чисельник'}"
        mapped[target] = {
            'date': date_str, 'group_name': group_name, 'lesson_number': target,
            'new_subject': lesson['subject'], 'teacher': teacher,
            'room': room, 'old_subject': original.get(target, {}).get('subject'),
            'source_note': description + (f". {str(note['text'])[:500]}" if note.get('text') else ''),
        }
    for row in rows:
        number = row['lesson_number']
        if number not in mapped or not as_scheduled(row['new_subject']):
            mapped[number] = row
        else:
            for field in ('teacher', 'room'):
                if row.get(field):
                    mapped[number][field] = row[field]
    return with_old_subjects([mapped[number] for number in sorted(mapped)], original)
