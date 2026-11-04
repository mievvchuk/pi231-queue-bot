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
    for migration in database.MIGRATIONS[:-2]:
        old.executescript(migration)
    old.execute(f'PRAGMA user_version = {len(database.MIGRATIONS)-2}')
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
async def test_journal_student_submission_review_edit_resets_approval_and_audit(client, conn):
    url = '/api/miniapp/journal'
    assert (await client.post(url,headers=headers(1),json=payload())).status == 200
    data = await (await client.get(url+'?period=2026-2027:1',headers=headers(1))).json()
    row = data['records'][0]
    assert row['grade'] == 5 and row['status'] == 'pending' and 'user_id' not in row
    review = {'action':'review','record_id':row['id'],'revision':0,'status':'approved'}
    assert (await client.post(url+'?mode=admin',headers=headers(100),json=review)).status == 200
    assert (await client.post(url+'?mode=admin',headers=headers(100),json=review)).status == 400
    current = journal.view(conn,1,SUBJECTS,period='2026-2027:1')['records'][0]
    assert current['status'] == 'approved'
    edited = payload(record_id=row['id'],revision=current['revision'],grade=4)
    assert (await client.post(url,headers=headers(1),json=edited)).status == 200
    latest = journal.view(conn,1,SUBJECTS,period='2026-2027:1')['records'][0]
    assert latest['status'] == 'pending' and latest['revision'] == 2
    assert conn.execute('SELECT COUNT(*) FROM journal_events').fetchone()[0] == 3


@pytest.mark.asyncio
async def test_journal_privacy_forged_identity_and_admin_mode(client, conn):
    journal.submit(conn,1,SUBJECTS,payload())
    target = users.get_user(conn,2)['id']
    row = journal.view(conn,1,SUBJECTS,period='2026-2027:1')['records'][0]
    url = '/api/miniapp/journal'
    own = await (await client.get(url+'?period=2026-2027:1',headers=headers(2))).json()
    assert own['records'] == [] and own['students'] == []
    assert (await client.get(url+'?scope=group',headers=headers(1))).status == 403
    assert (await client.get(url+'?scope=group',headers=headers(100))).status == 403
    assert (await client.post(url,headers=headers(1),json=payload(user_id=target))).status == 403
    assert (await client.post(url,headers=headers(2),json=payload(record_id=row['id'],revision=0))).status == 403
    review = {'action':'review','record_id':row['id'],'revision':0,'status':'approved'}
    assert (await client.post(url,headers=headers(1),json=review)).status == 403
    group = await (await client.get(url+'?scope=group&mode=admin&period=2026-2027:1',headers=headers(100))).json()
    assert len(group['records']) == 1 and len(group['students']) == 3
    assert (await client.post(url+'?mode=admin',headers=headers(100),json=payload(user_id=target))).status == 200


@pytest.mark.parametrize('grade', [-1,5.1,6,True,'5',None])
def test_journal_rejects_invalid_grades_without_writes(conn, make_user, grade):
    make_user(1,'Петренко Іван')
    with pytest.raises(BotError):
        journal.submit(conn,1,SUBJECTS,payload(grade=grade))
    assert conn.execute('SELECT COUNT(*) FROM journal_records').fetchone()[0] == 0


def test_rejection_requires_reason_duplicate_and_stale_edits_are_safe(conn, make_user):
    make_user(1,'Петренко Іван')
    rid = journal.submit(conn,1,SUBJECTS,payload())
    with pytest.raises(BotError):
        journal.submit(conn,1,SUBJECTS,payload())
    data = {'record_id':rid,'revision':0,'status':'rejected'}
    with pytest.raises(BotError):
        journal.review(conn,100,data)
    journal.review(conn,100,{**data,'note':'Уточни номер лабораторної'})
    with pytest.raises(BotError):
        journal.submit(conn,1,SUBJECTS,payload(record_id=rid,revision=0,grade=2))
    journal.submit(conn,1,SUBJECTS,payload(record_id=rid,revision=1,grade=2,number=2))
    updated = journal.view(conn,1,SUBJECTS,period='2026-2027:1')['records'][0]
    assert updated['status'] == 'pending' and updated['number'] == 2


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
    assert {'Фізичне виховання','Філософія'} <= names


def test_periods_are_separate_and_queue_completion_does_not_approve_grades(conn, make_user):
    make_user(1,'Петренко Іван')
    journal.submit(conn,1,SUBJECTS,payload())
    journal.submit(conn,1,SUBJECTS,payload(period='2026-2027:2',kind='attestation',grade=3))
    sid = queues.create_session(conn,100,1,1)
    queues.join_queue(conn,1,sid)
    queues.finish_own_entry(conn,1,sid,passed=True)
    assert journal.view(conn,1,SUBJECTS,period='2026-2027:1')['records'][0]['status'] == 'pending'
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
    assert conn.execute('SELECT user_id FROM journal_records WHERE id = ?', (rid,)).fetchone()[0] == uid
    with pytest.raises(BotError):
        queues.join_queue(conn,1,sid,position=14)
    restored = users.register_user(conn,1,'Петренко Іван')
    assert restored['id'] == uid
    assert journal.view(conn,1,SUBJECTS,period='2026-2027:1')['records'][0]['id'] == rid
