from datetime import datetime

from queue_bot.services import schedule, replacements, working_days
import pytest
from tests.test_mini_app import client, headers


def now(day, time):
    return datetime.fromisoformat(f'{day}T{time}:00').replace(tzinfo=schedule.UK_TZ)


def test_upcoming_includes_current_pair_but_not_finished_or_empty_slots(conn):
    data = {'monday': [{'pair': 1, 'subject': 'КПЗ'}, {'pair': 3, 'subject': 'МН'}]}
    first = schedule.get_upcoming_lesson(conn, data, now=now('2026-11-02', '09:49'))
    assert first['number'] == 1 and first['ongoing']
    next_pair = schedule.get_upcoming_lesson(conn, data, now=now('2026-11-02', '09:50'))
    assert next_pair['number'] == 3 and not next_pair['ongoing']
    assert next_pair['startTime'] == '11:50' and next_pair['endTime'] == '13:10'
    assert schedule.get_upcoming_lesson(conn, data, now=now('2026-11-02', '16:30')) is None


def test_upcoming_uses_replacements_working_saturday_and_date_not_repeating_rule(conn):
    data = {'monday': [{'pair': 1, 'subject': 'Базова пара'}, {'pair': 5, 'subject': 'Пізня пара'}]}
    working_days.set_working_saturday(conn, '2026-10-31', 'monday')
    replacements.save_replacement(conn, dict(date='2026-10-31', lesson_number=1, new_subject='Заміна', room='235'))
    item = schedule.get_upcoming_lesson(conn, data, now=now('2026-10-31', '08:00'))
    assert item['date'] == '2026-10-31'
    assert item['lessons'][0]['subject'] == 'Заміна'
    assert item['lessons'][0]['room'] == '235'
    item = schedule.get_upcoming_lesson(conn, data, now=now('2026-10-31', '13:30'))
    assert item['date'] == '2026-11-02'
    assert item['lessons'][0]['subject'] == 'Базова пара'


def test_upcoming_preserves_parallel_subgroups_and_skips_cancelled_pairs(conn):
    data = {'monday': [{'pair': 1, 'subject': 'Немає пари'},
                       {'pair': 2, 'subject': 'Підгрупа 1', 'room': '231'},
                       {'pair': 2, 'subject': 'Підгрупа 2', 'room': '232'}]}
    item = schedule.get_upcoming_lesson(conn, data, now=now('2026-11-02', '08:00'))
    assert item['number'] == 2
    assert [lesson['room'] for lesson in item['lessons']] == ['231', '232']


def test_empty_timetable_does_not_invent_a_class(conn):
    assert schedule.get_upcoming_lesson(conn, {}, now=now('2026-11-02', '08:00')) is None


@pytest.mark.asyncio
async def test_broken_optional_timetable_does_not_break_queue_bootstrap(client, monkeypatch):
    def invalid_timetable(*args, **kwargs):
        raise ValueError('bad pair')
    monkeypatch.setattr(schedule, 'get_upcoming_lesson', invalid_timetable)
    response = await client.get('/api/miniapp/bootstrap', headers=headers())
    assert response.status == 200
    data = await response.json()
    assert data['next_lesson'] is None
    assert data['me']['name'] == 'Петренко Іван'
