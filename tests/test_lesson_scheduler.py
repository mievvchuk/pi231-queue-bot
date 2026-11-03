from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from queue_bot.services import attendance, queues, schedule, working_days
from queue_bot.services.lesson_scheduler import sync_schedule
from queue_bot.services.lesson_notifier import deliver
from queue_bot.storage.database import connect

SUBJECTS = [
    {'id': 1, 'name': 'Конструювання програмного забезпечення',
     'teachers': [{'id': 1, 'name': 'Щуцький С.В.'}, {'id': 2, 'name': 'Кондратюк Є.С.'}]},
    {'id': 2, 'name': 'Людино-машинна взаємодія', 'teachers': [{'id': 1, 'name': 'Щуцький С.В.'}]},
]
DATA = {'tuesday': [{'pair': 1, 'subject': 'КПЗ', 'teachers': [{'name': 'Щуцький С.В.', 'room': '243а'}]}]}


def at(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=schedule.UK_TZ).timestamp())


@pytest.fixture
def queue(conn, make_user):
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid)
    return sid


def check(conn, clock, data=DATA):
    sync_schedule(conn, SUBJECTS, now=at(clock), schedule_data=data)
    attendance.tick(conn, now=at(clock))


@pytest.mark.asyncio
async def test_starts_at_0830_kyiv_once_and_stops_at_end(conn, queue):
    check(conn, '2026-11-03T08:29:59')
    assert attendance.active_run(conn, queue, now=at('2026-11-03T08:29:59')) is None
    check(conn, '2026-11-03T08:30:00')
    run = attendance.active_run(conn, queue, now=at('2026-11-03T08:30:00'))
    assert run and run['ends_at'] == at('2026-11-03T09:50:00')
    bot = AsyncMock()
    await deliver(bot, conn, SUBJECTS, now=at('2026-11-03T08:30:01'))
    row = attendance.members(conn, run['id'])[0]
    assert row['deadline'] == at('2026-11-03T08:40:01')
    check(conn, '2026-11-03T08:30:05')
    await deliver(bot, conn, SUBJECTS, now=at('2026-11-03T08:30:05'))
    assert bot.send_message.await_count == 1
    check(conn, '2026-11-03T09:50:00')
    assert attendance.active_run(conn, queue, now=at('2026-11-03T09:50:00')) is None
    assert queues.get_user_position(conn, 1, queue) == 1


def test_restart_during_pair_does_not_reset_attendance(conn, queue, db_path):
    check(conn, '2026-11-03T08:30:00')
    run = attendance.active_run(conn, queue, now=at('2026-11-03T08:30:00'))
    row = attendance.members(conn, run['id'])[0]
    attendance.respond(conn, 1, row['token'], 'late', minutes=15, now=at('2026-11-03T08:31:00'))
    reopened = connect(db_path)
    try:
        check(reopened, '2026-11-03T08:35:00')
        current = attendance.active_run(reopened, queue, now=at('2026-11-03T08:35:00'))
        assert current['id'] == run['id']
        assert attendance.members(reopened, run['id'])[0]['state'] == 'late'
    finally:
        reopened.close()


def test_catches_up_after_downtime_but_never_starts_after_class(conn, queue):
    check(conn, '2026-11-03T08:55:00')
    run = attendance.active_run(conn, queue, now=at('2026-11-03T08:55:00'))
    assert run['ends_at'] == at('2026-11-03T09:50:00')
    check(conn, '2026-11-10T09:50:01')
    assert attendance.active_run(conn, queue, now=at('2026-11-10T09:50:01')) is None
    assert conn.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 1


def test_explicit_stop_is_respected_and_next_pair_starts_again(conn, queue, monkeypatch):
    check(conn, '2026-11-03T08:30:00')
    monkeypatch.setattr(attendance, 'now_seconds', lambda: at('2026-11-03T08:35:00'))
    attendance.stop(conn, 100, queue)
    check(conn, '2026-11-03T08:36:00')
    assert attendance.active_run(conn, queue, now=at('2026-11-03T08:36:00')) is None
    check(conn, '2026-11-10T08:30:00')
    assert attendance.active_run(conn, queue, now=at('2026-11-10T08:30:00'))


def test_only_matching_subject_teacher_and_lesson_queue_start(conn, queue):
    other_teacher = queues.create_session(conn, 100, 1, 2)
    other_subject = queues.create_session(conn, 100, 2, 1)
    consultation = queues.create_session(conn, 100, 1, 1, queue_type='consultation')
    for sid in (other_teacher, other_subject, consultation):
        queues.join_queue(conn, 1, sid)
    check(conn, '2026-11-03T08:30:00')
    assert conn.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 1
    assert attendance.active_run(conn, queue, now=at('2026-11-03T08:30:00'))


def test_replacement_moves_launch_to_actual_subject_and_cancels_old_run(conn, queue):
    other = queues.create_session(conn, 100, 2, 1)
    queues.join_queue(conn, 1, other)
    check(conn, '2026-11-03T08:30:00')
    # Use the same source of replacements as the bot's timetable.
    conn.execute('''INSERT INTO replacements(date, group_name, lesson_number, new_subject, teacher)
                    VALUES (?, ?, ?, ?, ?)''',
                 ('2026-11-03', 'ПІ-231', 1, 'ЛМВ', 'Щуцький С.В.'))
    check(conn, '2026-11-03T08:35:00')
    assert attendance.active_run(conn, queue, now=at('2026-11-03T08:35:00')) is None
    assert attendance.active_run(conn, other, now=at('2026-11-03T08:35:00'))


def test_week_parity_working_saturday_and_timezone(conn, queue):
    # 3 November is ISO week 45 (numerator). Denominator-only must not start.
    data = {'tuesday': [{**DATA['tuesday'][0], 'week_type':'denominator'}]}
    check(conn, '2026-11-03T08:30:00', data)
    assert conn.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 0
    working_days.set_working_saturday(conn, '2026-11-07', 'tuesday', 'denominator')
    # UTC 06:30 is Kyiv 08:30 in November.
    utc = int(datetime(2026, 11, 7, 6, 30, tzinfo=timezone.utc).timestamp())
    sync_schedule(conn, SUBJECTS, now=utc, schedule_data=data)
    assert attendance.active_run(conn, queue, now=utc)


def test_manual_start_is_adopted_without_duplicate_presence(conn, queue):
    rid = attendance.start(conn, 100, queue, minutes=90, now=at('2026-11-03T08:29:00'))
    check(conn, '2026-11-03T08:30:00')
    run = attendance.active_run(conn, queue, now=at('2026-11-03T08:30:00'))
    assert run['id'] == rid and run['ends_at'] == at('2026-11-03T09:50:00')
    assert conn.execute('SELECT COUNT(*) FROM lesson_messages').fetchone()[0] == 1


def test_second_subgroup_and_next_scheduled_pair_have_separate_launches(conn, queue):
    other = queues.create_session(conn, 100, 1, 2)
    queues.join_queue(conn, 1, other)
    data = {'tuesday': [{**DATA['tuesday'][0], 'teachers':[{'name':'Щуцький С.В.'},{'name':'Кондратюк Є.С.'}]},
                        {**DATA['tuesday'][0], 'pair':3}]}
    check(conn, '2026-11-03T08:30:00', data)
    assert attendance.active_run(conn, other, now=at('2026-11-03T08:30:00'))
    check(conn, '2026-11-03T11:50:00', data)
    run = attendance.active_run(conn, queue, now=at('2026-11-03T11:50:00'))
    assert run and run['ends_at'] == at('2026-11-03T13:10:00')
    assert len(attendance.members(conn, run['id'])) == 1


def test_unknown_teacher_weekend_and_cancelled_lesson_do_not_start(conn, queue):
    check(conn, '2026-11-08T08:30:00')
    check(conn, '2026-11-03T08:30:00', {'tuesday':[{'pair':1,'subject':'КПЗ','teacher':''}]})
    check(conn, '2026-11-03T08:30:00', {'tuesday':[{'pair':1,'subject':'Скасовано','teacher':'Щуцький С.В.'}]})
    assert conn.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 0


def test_empty_queue_starts_when_student_joins_during_scheduled_pair(conn, queue):
    queues.leave_queue(conn, 1, queue)
    check(conn, '2026-11-03T08:30:00')
    assert conn.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 0
    queues.join_queue(conn, 1, queue)
    check(conn, '2026-11-03T08:45:00')
    assert attendance.active_run(conn, queue, now=at('2026-11-03T08:45:00'))


@pytest.mark.asyncio
async def test_invalid_timetable_does_not_block_existing_timeouts_and_delivery(monkeypatch):
    import asyncio
    from unittest.mock import Mock
    from queue_bot.services import lesson_notifier, lesson_scheduler
    monkeypatch.setattr(lesson_scheduler, 'sync_schedule', Mock(side_effect=ValueError('invalid timetable')))
    tick = Mock()
    delivery = AsyncMock()
    monkeypatch.setattr(attendance, 'tick', tick)
    monkeypatch.setattr(lesson_notifier, 'deliver', delivery)
    async def stop_loop(_):
        raise asyncio.CancelledError
    monkeypatch.setattr(lesson_notifier.asyncio, 'sleep', stop_loop)
    with pytest.raises(asyncio.CancelledError):
        await lesson_notifier.watch_lessons(None, None, [])
    tick.assert_called_once()
    delivery.assert_awaited_once()
