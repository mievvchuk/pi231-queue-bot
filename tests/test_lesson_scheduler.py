from datetime import datetime, timezone, timedelta
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
async def test_starts_at_0835_kyiv_once_and_stops_at_end(conn, queue):
    for clock in ('2026-11-03T08:29:59', '2026-11-03T08:30:00', '2026-11-03T08:34:59'):
        check(conn, clock)
        assert attendance.active_run(conn, queue, now=at(clock)) is None
        assert queues.get_session(conn, queue)['status'] == 'open'
        assert conn.execute('SELECT COUNT(*) FROM lesson_messages').fetchone()[0] == 0
    check(conn, '2026-11-03T08:35:00')
    assert queues.get_session(conn, queue)['status'] == 'open'
    run = attendance.active_run(conn, queue, now=at('2026-11-03T08:35:00'))
    assert run and run['ends_at'] == at('2026-11-03T09:50:00')
    bot = AsyncMock()
    await deliver(bot, conn, SUBJECTS, now=at('2026-11-03T08:35:01'))
    row = attendance.members(conn, run['id'])[0]
    assert row['deadline'] == at('2026-11-03T08:45:01')
    check(conn, '2026-11-03T08:35:05')
    await deliver(bot, conn, SUBJECTS, now=at('2026-11-03T08:35:05'))
    assert bot.send_message.await_count == 1
    check(conn, '2026-11-03T09:50:00')
    assert attendance.active_run(conn, queue, now=at('2026-11-03T09:50:00')) is None
    assert queues.get_session(conn, queue)['status'] == 'open'
    assert queues.get_user_position(conn, 1, queue) == 1


def test_restart_during_pair_does_not_reset_attendance(conn, queue, db_path):
    check(conn, '2026-11-03T08:35:00')
    run = attendance.active_run(conn, queue, now=at('2026-11-03T08:35:00'))
    row = attendance.members(conn, run['id'])[0]
    attendance.respond(conn, 1, row['token'], 'late', minutes=15, now=at('2026-11-03T08:35:00'))
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
    check(conn, '2026-11-03T08:35:00')
    monkeypatch.setattr(attendance, 'now_seconds', lambda: at('2026-11-03T08:35:00'))
    attendance.stop(conn, 100, queue)
    check(conn, '2026-11-03T08:36:00')
    assert attendance.active_run(conn, queue, now=at('2026-11-03T08:36:00')) is None
    check(conn, '2026-11-10T08:35:00')
    assert attendance.active_run(conn, queue, now=at('2026-11-10T08:35:00'))


def test_only_matching_subject_teacher_and_lesson_queue_start(conn, queue):
    other_teacher = queues.create_session(conn, 100, 1, 2)
    other_subject = queues.create_session(conn, 100, 2, 1)
    consultation = queues.create_session(conn, 100, 1, 1, queue_type='consultation')
    for sid in (other_teacher, other_subject, consultation):
        queues.join_queue(conn, 1, sid)
    check(conn, '2026-11-03T08:35:00')
    assert conn.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 1
    assert attendance.active_run(conn, queue, now=at('2026-11-03T08:35:00'))
    assert all(queues.get_session(conn, sid)['status'] == 'open'
               for sid in (other_teacher, other_subject, consultation))


@pytest.mark.parametrize('number', list(schedule.PAIR_TIMES))
def test_each_pair_starts_presence_exactly_five_minutes_later(conn, queue, number):
    begin = datetime.fromisoformat('2026-11-03T' + schedule.PAIR_TIMES[number][0]).replace(tzinfo=schedule.UK_TZ)
    launch = int((begin + timedelta(minutes=5)).timestamp())
    data = {'tuesday':[{**DATA['tuesday'][0], 'pair':number}]}
    sync_schedule(conn, SUBJECTS, now=launch-1, schedule_data=data)
    assert queues.get_session(conn, queue)['status'] == 'open'
    assert attendance.active_run(conn, queue, now=launch-1) is None
    sync_schedule(conn, SUBJECTS, now=launch, schedule_data=data)
    assert queues.get_session(conn, queue)['status'] == 'open'
    assert attendance.active_run(conn, queue, now=launch)


def test_archived_queue_is_not_reopened(conn, queue):
    queues._archive(conn, queue)
    check(conn, '2026-11-03T08:35:00')
    assert queues.get_session(conn, queue)['status'] == 'archived'
    assert conn.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 0


def test_replacement_moves_launch_to_actual_subject_and_cancels_old_run(conn, queue):
    other = queues.create_session(conn, 100, 2, 1)
    queues.join_queue(conn, 1, other)
    check(conn, '2026-11-03T08:35:00')
    # Use the same source of replacements as the bot's timetable.
    conn.execute('''INSERT INTO replacements(date, group_name, lesson_number, new_subject, teacher)
                    VALUES (?, ?, ?, ?, ?)''',
                 ('2026-11-03', 'ПІ-231', 1, 'ЛМВ', 'Щуцький С.В.'))
    check(conn, '2026-11-03T08:35:00')
    assert attendance.active_run(conn, queue, now=at('2026-11-03T08:35:00')) is None
    assert attendance.active_run(conn, other, now=at('2026-11-03T08:35:00'))


@pytest.mark.parametrize('actual, expected', [
    ('Людино-машинна взаємодія (ЛМВ)', 'Людино-машинна взаємодія'),
    ('ЛМВ', 'Людино-машинна взаємодія'),
    ('Конструювання програмного забезпечення (КПЗ)', 'Конструювання програмного забезпечення'),
    ('Проект інтерфейсів', 'Проєкт інтерфейсів користувача ПС'),
    ('Проєкт інтерфейсів', 'Проєкт інтерфейсів користувача ПС'),
    ('Проєкт інтерфейсів користувача ПС (ПІКПС)', 'Проєкт інтерфейсів користувача ПС'),
    ('Архітектура та проект ПЗ (АТПЗ)', 'Архітектура та проєкт ПЗ'),
    ('Машинне навчання (МН)', 'Машинне навчання'),
    ('Програмування в Інтернет (ПІ)', 'Програмування в Інтернет'),
])
def test_recognized_subject_names_match_queue_names(actual, expected):
    from queue_bot.services.lesson_scheduler import subject_name
    assert subject_name(actual) == subject_name(expected)


def test_subject_qualifiers_are_not_discarded():
    from queue_bot.services.lesson_scheduler import subject_name
    assert subject_name('Людино-машинна взаємодія (курсова)') != subject_name('Людино-машинна взаємодія')
    assert subject_name('Людино-машинна взаємодія (КПЗ)') != subject_name('Людино-машинна взаємодія')


@pytest.mark.asyncio
async def test_friday_replacement_starts_lmv_timer_at_1155_and_catches_up(conn, make_user):
    subjects = [{'id': 2, 'name': 'Людино-машинна взаємодія',
                 'teachers': [{'id': 1, 'name': 'Щуцький С.В.'}, {'id': 2, 'name': 'Кондратюк Є.С.'}]}]
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 2, 2)
    wrong_teacher = queues.create_session(conn, 100, 2, 1)
    queues.join_queue(conn, 1, sid)
    queues.join_queue(conn, 1, wrong_teacher)
    conn.execute('''INSERT INTO replacements(date, group_name, lesson_number, new_subject, teacher)
                    VALUES (?, ?, ?, ?, ?)''',
                 ('2026-11-06', 'ПІ-231', 3, 'Людино-машинна взаємодія (ЛМВ)', 'Кондратюк Є.С.'))
    # Friday has no third pair in the base timetable; the replacement adds it.
    sync_schedule(conn, subjects, now=at('2026-11-06T11:54:59'), schedule_data={})
    assert attendance.active_run(conn, sid, now=at('2026-11-06T11:54:59')) is None
    sync_schedule(conn, subjects, now=at('2026-11-06T11:55:00'), schedule_data={})
    run = attendance.active_run(conn, sid, now=at('2026-11-06T11:55:00'))
    assert run and run['ends_at'] == at('2026-11-06T13:10:00')
    assert attendance.active_run(conn, wrong_teacher, now=at('2026-11-06T11:55:00')) is None
    bot = AsyncMock()
    await deliver(bot, conn, subjects, now=at('2026-11-06T11:55:01'))
    assert bot.send_message.await_count == 1
    sync_schedule(conn, subjects, now=at('2026-11-06T12:30:00'), schedule_data={})
    assert attendance.active_run(conn, sid, now=at('2026-11-06T12:30:00'))['id'] == run['id']
    assert conn.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('replacement_teacher', [None, '—', 'Кондратюк'])
async def test_replacement_delivers_presence_when_teacher_is_missing_or_only_surname(
    conn, make_user, replacement_teacher,
):
    from queue_bot.services import replacements

    subject = SUBJECTS[0]
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 1, 2)
    other = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid)
    schedule.replace_schedule_from_backup(conn, {
        'tuesday': [{'pair': 1, 'subject': 'ЛМВ', 'teacher': 'Щуцький С.В.'}],
    })
    replacements.save_replacement(conn, {
        'date': '2026-11-03', 'lesson_number': 1,
        'new_subject': 'Конструювання програмного забезпечення (КПЗ)',
        'teacher': replacement_teacher,
    })

    now = at('2026-11-03T08:35:00')
    sync_schedule(conn, [subject], now=now)
    assert attendance.active_run(conn, sid, now=now)
    assert attendance.active_run(conn, other, now=now) is None
    bot = AsyncMock()
    await deliver(bot, conn, [subject], now=now)
    bot.send_message.assert_awaited_once()
    user_id, text = bot.send_message.await_args.args
    assert user_id == 1 and 'Ви вже на місці?' in text


@pytest.mark.parametrize('replacement_teacher', [None, 'Кондратюк А.Б.'])
def test_ambiguous_or_unmatched_replacement_does_not_start_wrong_queue(conn, make_user, replacement_teacher):
    from queue_bot.services import replacements

    make_user(1, 'Петренко Іван')
    first = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, first)
    if replacement_teacher is None:
        second = queues.create_session(conn, 100, 1, 2)
        queues.join_queue(conn, 1, second)
    replacements.save_replacement(conn, {
        'date': '2026-11-03', 'lesson_number': 1, 'new_subject': 'КПЗ', 'teacher': replacement_teacher,
    })

    sync_schedule(conn, SUBJECTS, now=at('2026-11-03T08:35:00'), schedule_data={})
    assert conn.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 0


def test_room_only_replacement_uses_planned_teacher(conn, queue):
    from queue_bot.services import replacements

    other = queues.create_session(conn, 100, 1, 2)
    queues.join_queue(conn, 1, other)
    replacements.save_replacement(conn, {
        'date': '2026-11-03', 'lesson_number': 1,
        'new_subject': 'Конструювання програмного забезпечення', 'room': '230',
    })
    now = at('2026-11-03T08:35:00')
    sync_schedule(conn, SUBJECTS, now=now, schedule_data=DATA)
    assert attendance.active_run(conn, queue, now=now)
    assert attendance.active_run(conn, other, now=now) is None


def test_missed_replacement_launch_recovers_during_class(conn, make_user):
    subjects = [{'id': 2, 'name': 'Людино-машинна взаємодія',
                 'teachers': [{'id': 2, 'name': 'Кондратюк Є.С.'}]}]
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 2, 2)
    queues.join_queue(conn, 1, sid)
    data = {'friday': [{'pair': 3, 'subject': 'Людино-машинна взаємодія (ЛМВ)', 'teacher': 'Кондратюк Є.С.'}]}
    sync_schedule(conn, subjects, now=at('2026-11-06T12:30:00'), schedule_data=data)
    run = attendance.active_run(conn, sid, now=at('2026-11-06T12:30:00'))
    assert run and run['ends_at'] == at('2026-11-06T13:10:00')


def test_week_parity_working_saturday_and_timezone(conn, queue):
    # 3 November is ISO week 45 (numerator). Denominator-only must not start.
    data = {'tuesday': [{**DATA['tuesday'][0], 'week_type':'denominator'}]}
    check(conn, '2026-11-03T08:35:00', data)
    assert conn.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 0
    working_days.set_working_saturday(conn, '2026-11-07', 'tuesday', 'denominator')
    # UTC 06:35 is Kyiv 08:35 in November.
    utc = int(datetime(2026, 11, 7, 6, 35, tzinfo=timezone.utc).timestamp())
    sync_schedule(conn, SUBJECTS, now=utc, schedule_data=data)
    assert attendance.active_run(conn, queue, now=utc)


def test_manual_start_is_adopted_without_duplicate_presence(conn, queue):
    rid = attendance.start(conn, 100, queue, minutes=90, now=at('2026-11-03T08:29:00'))
    check(conn, '2026-11-03T08:35:00')
    run = attendance.active_run(conn, queue, now=at('2026-11-03T08:35:00'))
    assert run['id'] == rid and run['ends_at'] == at('2026-11-03T09:50:00')
    assert conn.execute('SELECT COUNT(*) FROM lesson_messages').fetchone()[0] == 1


def test_second_subgroup_and_next_scheduled_pair_have_separate_launches(conn, queue):
    other = queues.create_session(conn, 100, 1, 2)
    queues.join_queue(conn, 1, other)
    data = {'tuesday': [{**DATA['tuesday'][0], 'teachers':[{'name':'Щуцький С.В.'},{'name':'Кондратюк Є.С.'}]},
                        {**DATA['tuesday'][0], 'pair':3}]}
    check(conn, '2026-11-03T08:35:00', data)
    assert attendance.active_run(conn, other, now=at('2026-11-03T08:35:00'))
    check(conn, '2026-11-03T11:55:00', data)
    run = attendance.active_run(conn, queue, now=at('2026-11-03T11:55:00'))
    assert run and run['ends_at'] == at('2026-11-03T13:10:00')
    assert len(attendance.members(conn, run['id'])) == 1


def test_unknown_teacher_weekend_and_cancelled_lesson_do_not_start(conn, queue):
    check(conn, '2026-11-08T08:35:00')
    check(conn, '2026-11-03T08:35:00', {'tuesday':[{'pair':1,'subject':'КПЗ','teacher':''}]})
    check(conn, '2026-11-03T08:35:00', {'tuesday':[{'pair':1,'subject':'Скасовано','teacher':'Щуцький С.В.'}]})
    assert conn.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 0


def test_empty_queue_is_active_before_student_joins_during_scheduled_pair(conn, queue):
    queues.leave_queue(conn, 1, queue)
    check(conn, '2026-11-03T08:35:00')
    assert conn.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 1
    assert queues.get_session(conn, queue)['status'] == 'open'
    queues.join_queue(conn, 1, queue)
    check(conn, '2026-11-03T08:45:00')
    assert attendance.active_run(conn, queue, now=at('2026-11-03T08:45:00'))
    assert conn.execute('SELECT COUNT(*) FROM lesson_messages').fetchone()[0] == 1


def test_permanent_signup_survives_restart_and_next_pair(conn, queue, db_path):
    check(conn, '2026-11-03T08:35:00')
    reopened = connect(db_path)
    try:
        check(reopened, '2026-11-03T08:40:00')
        assert queues.get_session(reopened, queue)['status'] == 'open'
        check(reopened, '2026-11-10T08:34:59')
        assert queues.get_session(reopened, queue)['status'] == 'open'
        check(reopened, '2026-11-10T08:35:00')
        assert queues.get_session(reopened, queue)['status'] == 'open'
    finally:
        reopened.close()


def test_stopping_presence_does_not_disable_permanent_signup(conn, queue, monkeypatch):
    monkeypatch.setattr(attendance, 'now_seconds', lambda: at('2026-11-03T08:32:00'))
    attendance.stop(conn, 100, queue)
    check(conn, '2026-11-03T08:35:00')
    assert queues.get_session(conn, queue)['status'] == 'open'
    assert conn.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 0


def test_schedule_adoption_keeps_existing_approach(conn, queue):
    from queue_bot.services.lesson_scheduler import key_for
    rid = attendance.start(conn, 100, queue, now=at('2026-11-03T08:30:00'))
    row = attendance.members(conn, rid)[0]
    attendance.respond(conn, 1, row['token'], 'present', now=at('2026-11-03T08:31:00'))
    row = attendance.members(conn, rid)[0]
    attendance.respond(conn, 1, row['token'], 'ready', minutes=20, now=at('2026-11-03T08:32:00'))
    before = dict(attendance.members(conn, rid)[0])
    conn.execute('INSERT INTO bot_state(key, value) VALUES (?, ?)',
                 (key_for(queue, datetime(2026, 11, 3).date(), 1), str(rid)))
    check(conn, '2026-11-03T08:35:00')
    assert queues.get_session(conn, queue)['status'] == 'open'
    assert dict(attendance.members(conn, rid)[0]) == before


@pytest.mark.asyncio
async def test_end_keeps_signup_and_preserves_partial_results_and_pending_approach(conn, queue, monkeypatch):
    from queue_bot.services.errors import BotError
    queues.leave_queue(conn, 1, queue)
    queues.join_queue(conn, 1, queue, lab_numbers=[1, 2], planned_minutes=20)
    check(conn, '2026-11-03T08:35:00')
    now = at('2026-11-03T08:36:00')
    monkeypatch.setattr(attendance, 'now_seconds', lambda: now)
    row = attendance.view(conn, queue, 1)['own']
    attendance.respond(conn, 1, row['token'], 'present')
    row = attendance.view(conn, queue, 1)['own']
    attendance.respond(conn, 1, row['token'], 'ready', minutes=120)
    entry = queues._own_active_entry(conn, 1, queue)
    queues.record_own_passed_lab_numbers(conn, 1, queue, [1], entry_id=entry['id'], expected_revision=entry['entry_revision'])
    before = dict(queues.get_entry(conn, entry['id']))
    token = attendance.view(conn, queue, 1)['own']['token']
    check(conn, '2026-11-03T09:49:59')
    assert queues.get_session(conn, queue)['status'] == 'open'
    check(conn, '2026-11-03T09:50:00')
    assert queues.get_session(conn, queue)['status'] == 'open'
    assert dict(queues.get_entry(conn, entry['id'])) == before
    assert before['selected_labs'] == '[2]' and before['passed_labs'] == '[1]'
    with pytest.raises(BotError):
        attendance.respond(conn, 1, token, 'extend', minutes=10, now=at('2026-11-03T09:50:00'))
    bot = AsyncMock()
    await deliver(bot, conn, SUBJECTS, now=at('2026-11-03T09:50:00'))
    bot.send_message.assert_not_awaited()
    check(conn, '2026-11-10T08:35:00')
    assert queues.get_session(conn, queue)['status'] == 'open'
    assert dict(queues.get_entry(conn, entry['id'])) == before
    run = attendance.active_run(conn, queue, now=at('2026-11-10T08:35:00'))
    assert attendance.members(conn, run['id'])[0]['state'] == 'pending'


def test_stopped_lesson_keeps_signup_after_restart(conn, queue, db_path, monkeypatch):
    check(conn, '2026-11-03T08:35:00')
    monkeypatch.setattr(attendance, 'now_seconds', lambda: at('2026-11-03T08:40:00'))
    attendance.stop(conn, 100, queue)
    assert queues.get_session(conn, queue)['status'] == 'open'
    reopened = connect(db_path)
    try:
        # Direct tick stops only the lesson when timetable JSON is unavailable.
        attendance.tick(reopened, now=at('2026-11-03T09:50:00'))
        assert queues.get_session(reopened, queue)['status'] == 'open'
        assert queues.get_user_position(reopened, 1, queue) == 1
        # Repeated ticks also leave the permanent queue available.
        attendance.tick(reopened, now=at('2026-11-03T09:51:00'))
        assert queues.get_session(reopened, queue)['status'] == 'open'
    finally:
        reopened.close()


def test_overdue_closure_does_not_close_newer_pair_after_downtime(conn, queue):
    data = {'tuesday':[DATA['tuesday'][0], {**DATA['tuesday'][0], 'pair':2}]}
    check(conn, '2026-11-03T08:35:00', data)
    check(conn, '2026-11-03T10:05:00', data)
    assert queues.get_session(conn, queue)['status'] == 'open'
    run = attendance.active_run(conn, queue, now=at('2026-11-03T10:05:00'))
    assert run and run['ends_at'] == at('2026-11-03T11:20:00')
    check(conn, '2026-11-03T11:20:00', data)
    assert queues.get_session(conn, queue)['status'] == 'open'


def test_legacy_signup_marker_and_manual_end_keep_permanent_queue(conn, queue):
    from queue_bot.services.lesson_scheduler import key_for
    key = key_for(queue, datetime(2026, 11, 3).date(), 1) + ':signup'
    conn.execute("INSERT INTO bot_state(key, value) VALUES (?, 'opened')", (key,))
    attendance.tick(conn, now=at('2026-11-03T09:50:00'))
    assert queues.get_session(conn, queue)['status'] == 'open'
    attendance.start(conn, 100, queue, minutes=10, now=at('2026-11-03T10:00:00'))
    attendance.tick(conn, now=at('2026-11-03T10:10:00'))
    assert queues.get_session(conn, queue)['status'] == 'open'
    assert queues.get_user_position(conn, 1, queue) == 1


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
