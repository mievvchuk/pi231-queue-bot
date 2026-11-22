import json

import pytest

from queue_bot.services import admins, journal, queues, users
from queue_bot.services.errors import BotError, PermissionDenied
from tests.test_mini_app import client, headers


SUBJECTS = [{'id':1,'name':'Програмування','teachers':[]}]


def test_sqlite_additive_migration_preserves_existing_users(tmp_path):
    import sqlite3
    from queue_bot.storage import database
    path = str(tmp_path / 'legacy.db')
    old = sqlite3.connect(path)
    cutoff = next(i for i,s in enumerate(database.MIGRATIONS) if 'ALTER TABLE users ADD COLUMN deleted_at' in s)
    for migration in database.MIGRATIONS[:cutoff]:
        old.executescript(migration)
    old.execute(f'PRAGMA user_version = {cutoff}')
    old.execute("INSERT INTO users(telegram_id,full_name) VALUES (1,'Петренко Іван')")
    old.commit()
    old.close()
    migrated = database.connect(path)
    try:
        assert users.get_user(migrated,1)['full_name'] == 'Петренко Іван'
        assert users.get_user(migrated,1)['deleted_at'] is None
        journal.submit(migrated,1,SUBJECTS,payload())
        assert journal.view(migrated,1,SUBJECTS,period='2026-2027:1')['records'][0]['grade'] == 5
    finally:
        migrated.close()


def payload(**values):
    return {'period':'2026-2027:1','subject_key':'програмування','kind':'lab','number':1,
            'grade':5,'note':'Захистив на парі',**values}


@pytest.mark.asyncio
async def test_journal_student_submission_edit_and_admin_edit_without_approval(client, conn):
    url = '/api/miniapp/journal'
    assert (await client.post(url,headers=headers(1),json=payload())).status == 200
    data = await (await client.get(url+'?period=2026-2027:1',headers=headers(1))).json()
    row = data['records'][0]
    assert row['grade'] == 5 and row['can_edit'] is True and 'status' not in row
    review = {'action':'review','record_id':row['id'],'revision':0,'status':'approved'}
    assert (await client.post(url+'?mode=admin',headers=headers(100),json=review)).status == 400
    target = row['user_id']
    by_admin = payload(user_id=target,record_id=row['id'],revision=0,grade=3)
    assert (await client.post(url+'?mode=admin',headers=headers(100),json=by_admin)).status == 200
    edited = payload(record_id=row['id'],revision=1,grade=4)
    assert (await client.post(url,headers=headers(1),json=edited)).status == 200
    latest = journal.view(conn,1,SUBJECTS,period='2026-2027:1')['records'][0]
    assert latest['grade'] == 4 and latest['revision'] == 2
    assert conn.execute('SELECT COUNT(*) FROM journal_cell_events').fetchone()[0] == 3


@pytest.mark.asyncio
async def test_journal_privacy_forged_identity_and_admin_mode(client, conn):
    journal.submit(conn,1,SUBJECTS,payload())
    target = users.get_user(conn,2)['id']
    row = journal.view(conn,1,SUBJECTS,period='2026-2027:1')['records'][0]
    url = '/api/miniapp/journal'
    own = await (await client.get(url+'?period=2026-2027:1',headers=headers(2))).json()
    assert len(own['records']) == 1 and len(own['students']) == 3
    assert own['records'][0]['can_edit'] is False
    assert 'note' not in own['records'][0] and 'review_note' not in own['records'][0]
    assert (await client.get(url+'?scope=group',headers=headers(1))).status == 403
    assert (await client.get(url+'?scope=group',headers=headers(100))).status == 403
    assert (await client.post(url,headers=headers(1),json=payload(user_id=target))).status == 403
    assert (await client.post(url,headers=headers(2),json=payload(record_id=row['id'],revision=0))).status == 403
    group = await (await client.get(url+'?scope=group&mode=admin&period=2026-2027:1',headers=headers(100))).json()
    assert len(group['records']) == 1 and len(group['students']) == 3
    assert (await client.post(url+'?mode=admin',headers=headers(100),json=payload(user_id=target))).status == 200


@pytest.mark.parametrize('grade', [-1,5.1,6,True,'5',None])
def test_journal_rejects_invalid_grades_without_writes(conn, make_user, grade):
    make_user(1,'Петренко Іван')
    with pytest.raises(BotError):
        journal.submit(conn,1,SUBJECTS,payload(grade=grade))
    assert conn.execute('SELECT COUNT(*) FROM journal_cells').fetchone()[0] == 0


def test_duplicate_and_stale_edits_are_safe(conn, make_user):
    make_user(1,'Петренко Іван')
    rid = journal.submit(conn,1,SUBJECTS,payload())
    with pytest.raises(BotError):
        journal.submit(conn,1,SUBJECTS,payload())
    journal.submit(conn,1,SUBJECTS,payload(record_id=rid,revision=0,grade=3))
    with pytest.raises(BotError):
        journal.submit(conn,1,SUBJECTS,payload(record_id=rid,revision=0,grade=2))
    journal.submit(conn,1,SUBJECTS,payload(record_id=rid,revision=1,grade=2,number=2,kind='attestation'))
    updated = journal.view(conn,1,SUBJECTS,period='2026-2027:1')['records'][0]
    assert updated['grade'] == 2 and updated['number'] == 2


def test_dates_are_explicit_owner_only_and_journal_covers_schedule_subjects(conn, make_user):
    make_user(1,'Петренко Іван')
    admins.add_admin(conn,100,200)
    values = payload(number=1,due_date='2026-11-20')
    for uid in (1,200):
        with pytest.raises(PermissionDenied):
            journal.set_date(conn,uid,SUBJECTS,values)
    journal.set_date(conn,100,SUBJECTS,values)
    assert journal.view(conn,1,SUBJECTS,period='2026-2027:1')['dates'][0]['due_date'] == '2026-11-20'
    journal.set_date(conn,100,SUBJECTS,{**values,'due_date':''})
    assert journal.view(conn,1,SUBJECTS,period='2026-2027:1')['dates'][0]['due_date'] is None
    with pytest.raises(BotError):
        journal.set_date(conn,100,SUBJECTS,{**values,'due_date':'2026-02-30'})
    names = {s['name'] for s in journal.catalog(SUBJECTS)}
    assert 'Філософія' in names  # comes from the timetable only, not from subjects.json


def test_periods_are_separate_and_queue_completion_does_not_approve_grades(conn, make_user):
    make_user(1,'Петренко Іван')
    journal.submit(conn,1,SUBJECTS,payload())
    journal.submit(conn,1,SUBJECTS,payload(period='2026-2027:2',kind='attestation',grade=3))
    sid = queues.create_session(conn,100,1,1)
    queues.join_queue(conn,1,sid)
    queues.finish_own_entry(conn,1,sid,passed=True)
    assert journal.view(conn,1,SUBJECTS,period='2026-2027:1')['records'][0]['grade'] == 5
    assert journal.view(conn,1,SUBJECTS,period='2026-2027:2')['records'][0]['grade'] == 3


def test_removed_user_disappears_but_retains_queue_and_journal_history(conn, make_user):
    make_user(1,'Петренко Іван')
    uid = users.get_user(conn,1)['id']
    rid = journal.submit(conn,1,SUBJECTS,payload())
    sid = queues.create_session(conn,100,1,1)
    queues.join_queue(conn,1,sid,position=14)
    with pytest.raises(BotError):
        users.admin_delete_user(conn,100,uid)
    queues.leave_queue(conn,1,sid)
    history = [dict(r) for r in queues.get_history(conn,sid)]
    users.admin_delete_user(conn,100,uid)
    assert users.get_user(conn,1) is None and users.get_user_by_id(conn,uid) is None
    assert users.list_users(conn) == []
    assert [dict(r) for r in queues.get_history(conn,sid)] == history
    assert conn.execute('SELECT user_id FROM journal_cells WHERE id = ?', (rid,)).fetchone()[0] == uid
    with pytest.raises(BotError):
        queues.join_queue(conn,1,sid,position=14)
    restored = users.register_user(conn,1,'Петренко Іван')
    assert restored['id'] == uid
    assert journal.view(conn,1,SUBJECTS,period='2026-2027:1')['records'][0]['id'] == rid


@pytest.mark.asyncio
async def test_physical_education_grades_can_be_saved_without_timetable_entry(client, conn):
    from queue_bot.services import schedule

    schedule.replace_schedule_from_backup(conn, {})
    url = '/api/miniapp/journal'
    data = await (await client.get(url + '?period=2026-2027:1', headers=headers(1))).json()
    subject = next(s for s in data['subjects'] if s['name'] == 'Фізичне виховання')
    assert [work['title'] for work in subject['labs']] == ['Норматив 1', 'Норматив 2', 'Норматив 3', 'Норматив 4']
    response = await client.post(url, headers=headers(1), json=payload(subject_key=subject['key'], number=4, grade=5))
    assert response.status == 200
    updated = await (await client.get(url + '?period=2026-2027:1', headers=headers(1))).json()
    assert updated['records'][0]['grade'] == 5
    assert updated['averages'][str(updated['viewer_id'])][subject['key']]['2']['average'] == 5


def test_kpz_coursework_is_one_grade_counted_only_in_second_attestation_and_semester(conn, make_user):
    from queue_bot.services.lesson_scheduler import subject_name

    make_user(1, 'Петренко Іван')
    kpz, coursework = 'Конструювання програмного забезпечення', 'Курсова робота з КПЗ'
    subjects = [{'id': 5, 'name': kpz}, {'id': 7, 'name': coursework}]
    first = payload(subject_key=subject_name(kpz), number=1, grade=5)
    second = payload(subject_key=subject_name(kpz), number=8, grade=3)
    course = payload(subject_key=subject_name(coursework), grade=4)
    for row in (first, second, course):
        journal.submit(conn, 1, subjects, row)
    view = journal.view(conn, 1, subjects, period=course['period'])
    averages = view['averages'][str(view['viewer_id'])]
    assert {key: value['average'] for key, value in averages[subject_name(kpz)].items()} == {
        '1': 5, '2': 3.5, 'all': 4,
    }
    assert averages[subject_name(coursework)] == {'all': {'average': 4, 'filled': 1, 'total': 1}}
    with pytest.raises(BotError, match='одна оцінка'):
        journal.submit(conn, 1, subjects, {**course, 'kind': 'attestation'})
    with pytest.raises(BotError, match='немає атестацій'):
        journal.set_date(conn, 100, subjects, {**course, 'due_date': '2026-12-01'})
