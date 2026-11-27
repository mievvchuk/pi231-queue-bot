from datetime import datetime, date, timedelta

import pytest

from queue_bot import config
from queue_bot.services import schedule, queues, attendance, lesson_scheduler, replacements, working_days
from queue_bot.storage.database import connect
from tests.test_mini_app import client, headers


@pytest.fixture
def finished_course(conn, monkeypatch):
    monkeypatch.setattr(config, 'SEMESTER_END_DATE', date(2026, 12, 31))
    schedule.replace_schedule_from_backup(conn, {'friday': [
        {'pair': 1, 'subject': 'МН', 'teacher': 'Барабаш Д.С.'},
        {'pair': 2, 'subject': 'МН', 'teacher': 'Барабаш Д.С.'},
        {'pair': 3, 'subject': 'ЛМВ'}]})
    start = datetime(2026, 11, 27, 8, 30, tzinfo=schedule.UK_TZ)
    schedule.save_subject_pair_plan(conn, 'Машинне навчання', 1, now=start-timedelta(hours=1))
    return start, schedule.read_schedule(conn)


def test_final_class_stays_until_end_and_does_not_reappear_at_next_slot(conn, finished_course):
    start, data = finished_course
    def subjects(target, now):
        return [lesson['subject'] for lesson in schedule.get_actual_schedule(conn, data, target, now=now)[0]]
    assert subjects(start.date(), start-timedelta(seconds=1)) == ['МН', 'МН', 'ЛМВ']
    assert subjects(start.date(), start) == ['МН', 'ЛМВ']
    assert subjects(start.date(), start.replace(hour=9, minute=49)) == ['МН', 'ЛМВ']
    assert subjects(start.date(), start.replace(hour=9, minute=50)) == ['ЛМВ']
    assert subjects(start.date(), start.replace(hour=10, minute=5)) == ['ЛМВ']
    later = start+timedelta(days=7)
    assert subjects(later.date(), later) == ['ЛМВ']
    # Historical dates and the editable timetable retain their source lessons.
    assert subjects((start-timedelta(days=7)).date(), later) == ['МН', 'МН', 'ЛМВ']
    assert schedule.read_schedule(conn) == data


def test_hidden_subject_count_stays_zero_after_restart_and_can_be_restored(conn, db_path, finished_course):
    start, data = finished_course
    now = start.replace(hour=10)
    next_week = (start+timedelta(days=7)).date()
    schedule.get_actual_schedule(conn, data, next_week, now=now)
    reopened = connect(db_path)
    try:
        plan, = schedule.get_remaining_pairs(reopened, now=now, project_finish=False).values()
        assert plan['remaining'] == 0
        assert [l['subject'] for l in schedule.get_actual_schedule(reopened, data, next_week, now=now)[0]] == ['ЛМВ']
        schedule.save_subject_pair_plan(reopened, 'МН', 2, now=now)
        assert [l['subject'] for l in schedule.get_actual_schedule(reopened, data, next_week, now=now)[0]] == ['МН', 'МН', 'ЛМВ']
    finally:
        reopened.close()


def test_manual_zero_replacements_and_working_saturday_respect_completion(conn, finished_course):
    start, data = finished_course
    schedule.save_subject_pair_plan(conn, 'МН', 0, now=start-timedelta(hours=1))
    replacements.save_replacement(conn, {'date':'2026-11-27', 'lesson_number':3, 'new_subject':'Машинне навчання'})
    assert schedule.get_actual_schedule(conn, data, start.date(), now=start)[0] == []
    working_days.set_working_saturday(conn, '2026-11-28', 'friday', 'denominator')
    lessons, _ = schedule.get_actual_schedule(conn, data, date(2026, 11, 28), now=start)
    assert [l['subject'] for l in lessons] == ['ЛМВ']
    # A replacement to a different, unfinished subject still takes place.
    replacements.save_replacement(conn, {'date':'2026-11-27', 'lesson_number':1, 'new_subject':'КПЗ'})
    lessons, _ = schedule.get_actual_schedule(conn, data, start.date(), now=start)
    assert any(lesson['subject'] == 'КПЗ' for lesson in lessons)


def test_final_pair_attendance_runs_normally_but_future_completed_pairs_do_not(conn, finished_course, make_user):
    start, data = finished_course
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 3, 4)
    queues.join_queue(conn, 1, sid)
    catalog = [{'id':3, 'name':'Машинне навчання', 'teachers':[{'id':4, 'name':'Барабаш Д.С.'}]}]
    for now in (start+timedelta(minutes=5), start+timedelta(minutes=30)):
        lesson_scheduler.sync_schedule(conn, catalog, now=int(now.timestamp()), schedule_data=data)
        assert attendance.active_run(conn, sid, now=int(now.timestamp()))
    for now in (start.replace(hour=9, minute=50), start.replace(hour=10, minute=5), start+timedelta(days=7, minutes=5)):
        lesson_scheduler.sync_schedule(conn, catalog, now=int(now.timestamp()), schedule_data=data)
        assert attendance.active_run(conn, sid, now=int(now.timestamp())) is None
    assert queues.get_user_position(conn, 1, sid) == 1
    assert conn.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 1


def test_upcoming_lesson_skips_completed_subject(conn, finished_course):
    start, data = finished_course
    upcoming = schedule.get_upcoming_lesson(conn, data, now=start.replace(hour=10, minute=5))
    assert upcoming['number'] == 3
    assert [l['subject'] for l in upcoming['lessons']] == ['ЛМВ']


@pytest.mark.asyncio
async def test_bot_and_miniapp_hide_zero_count_subjects(conn, client):
    schedule.replace_schedule_from_backup(conn, {'friday': [{'pair':1, 'subject':'МН'}, {'pair':2, 'subject':'ЛМВ'}]})
    now = datetime.now(schedule.UK_TZ)
    schedule.save_subject_pair_plan(conn, 'МН', 0, now=now)
    target = now.date()+timedelta(days=(4-now.weekday()) % 7 + 7)
    response = await client.get('/api/miniapp/schedule?date='+target.isoformat(), headers=headers(1))
    payload = await response.json()
    assert [lesson['subject'] for lesson in payload['lessons']] == ['ЛМВ']
    assert all(lesson['subject'] != 'МН' for slot in payload['slots'] for lesson in slot['lessons'])
    assert 'МН' not in schedule.format_schedule_for_date(conn, schedule.read_schedule(conn), target)
