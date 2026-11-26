import json
import sqlite3

import pytest

from queue_bot.services import journal, users
from queue_bot.services.errors import BotError, PermissionDenied
from queue_bot.services.journal_layout import layout, summary
from queue_bot.storage import database
from queue_bot.storage.journal_grid_schema import GRID_COPY


SUBJECTS = [{'id':1,'name':'Конструювання програмного забезпечення','teachers':[]}]
KEY = 'конструюванняпрограмногозабезпечення'


def values(**extra):
    return {'period':'2026-2027:1','subject_key':KEY,'kind':'lab','number':1,'part':1,'grade':5,**extra}


@pytest.mark.parametrize('name,counts',[
    ('Конструювання програмного забезпечення',[5]+[1]*10),
    ('Людино-машинна взаємодія',[1]*10),
    ('Машинне навчання',[3]*9),
    ('Програмування в Інтернет',[3]*7),
    ('Архітектура та проєкт ПЗ',[5]*3),
    ('Філософія',[1]*6),
    ('Фізичне виховання',[1]*4),
    ('Проєкт інтерфейсів користувача ПС',[1]*23),
    ('Курсова робота з КПЗ',[1]),
])
def test_exact_subject_columns(name,counts):
    assert [work['parts'] for work in layout(name)['labs']] == counts
    assert all(work['optional_last'] for work in layout('Машинне навчання')['labs'])


def test_empty_grid_has_entire_sorted_roster_and_subject_layouts(conn,make_user):
    for uid,name in [(1,'Євчук Михайло'),(2,'Жук Іван'),(3,'Емець Андрій')]:
        make_user(uid,name)
    result = journal.view(conn,1,SUBJECTS,period='2026-2027:1')
    assert result['records'] == []
    assert [s['name'] for s in result['students']] == ['Емець Андрій','Євчук Михайло','Жук Іван']
    assert next(s for s in result['subjects'] if s['key']==KEY)['labs'][0]['parts'] == 5
    assert result['viewer_id'] == users.get_user(conn,1)['id']


def test_each_grade_is_independent_and_only_the_owners_grade_is_editable(conn,make_user):
    make_user(1,'Петренко Іван');make_user(2,'Коваль Андрій')
    first = journal.submit(conn,1,SUBJECTS,values(part=1,note='Особиста примітка'))
    second = journal.submit(conn,1,SUBJECTS,values(part=2,grade=2))
    fifth = journal.submit(conn,1,SUBJECTS,values(part=5,grade=4))
    assert len({first,second,fifth}) == 3
    public = journal.view(conn,2,SUBJECTS,period='2026-2027:1')
    assert [r['part'] for r in public['records']] == [1,2,5]
    assert all(r['can_edit'] is False for r in public['records'])
    assert all('note' not in r and 'review_note' not in r for r in public['records'])
    with pytest.raises(PermissionDenied):
        journal.submit(conn,2,SUBJECTS,values(part=2,record_id=second,revision=0))
    journal.submit(conn,1,SUBJECTS,values(part=2,record_id=second,revision=0,grade=3))
    own = journal.view(conn,1,SUBJECTS,period='2026-2027:1')
    assert next(r for r in own['records'] if r['id']==second)['grade'] == 3
    assert next(r for r in own['records'] if r['id']==first)['note'] == 'Особиста примітка'
    assert next(r for r in own['records'] if r['id']==fifth)['grade'] == 4


@pytest.mark.parametrize('extra',[{'part':6},{'part':0},{'part':True},{'number':2,'part':2},{'number':12}])
def test_invalid_cell_is_rejected_without_creating_a_grade(conn,make_user,extra):
    make_user(1,'Петренко Іван')
    with pytest.raises(BotError):
        journal.submit(conn,1,SUBJECTS,values(**extra))
    assert conn.execute('SELECT COUNT(*) FROM journal_cells').fetchone()[0] == 0


def test_v1_records_and_audit_migrate_without_overwriting_later_edits(tmp_path):
    path = str(tmp_path/'old-journal.db')
    old=sqlite3.connect(path)
    cutoff = next(i for i, migration in enumerate(database.MIGRATIONS) if 'CREATE TABLE IF NOT EXISTS journal_cells' in migration)
    for migration in database.MIGRATIONS[:cutoff]: old.executescript(migration)
    old.execute(f'PRAGMA user_version = {cutoff}')
    old.execute("INSERT INTO users(id,telegram_id,full_name) VALUES (1,1,'Петренко Іван')")
    old.execute('''INSERT INTO journal_records(id,user_id,period,subject_key,subject_name,kind,number,grade,status)
        VALUES (7,1,'2026-2027:1',?,'Конструювання програмного забезпечення','lab',1,4,'approved')''',(KEY,))
    old.execute("INSERT INTO journal_events(record_id,actor_id,action,snapshot) VALUES (7,100,'approved','{}')")
    old.commit();old.close()
    conn=database.connect(path)
    try:
        result=journal.view(conn,1,SUBJECTS,period='2026-2027:1')
        assert result['records'][0]['id']=='legacy:7'
        assert result['records'][0]['part']==1
        journal.submit(conn,1,SUBJECTS,values(record_id=7,revision=0,number=2,grade=5))
        new=journal.submit(conn,1,SUBJECTS,values(grade=3))
        conn.executescript(GRID_COPY)
        assert conn.execute('SELECT COUNT(*) FROM journal_cells').fetchone()[0] == 2
        assert conn.execute('SELECT COUNT(*) FROM journal_cell_events').fetchone()[0] == 3
        assert conn.execute('SELECT grade FROM journal_records WHERE id=7').fetchone()[0] == 4
        assert conn.execute('SELECT grade FROM journal_cells WHERE id=?',(new,)).fetchone()[0] == 3
        result=journal.view(conn,1,SUBJECTS,period='2026-2027:1')
        assert next(r for r in result['records'] if r['id']=='legacy:7')['number']==2
    finally: conn.close()


@pytest.mark.parametrize('name,first,second',[
    ('Програмування в Інтернет',[1,2,3,4],[5,6,7]),
    ('Людино-машинна взаємодія',[1,2,3,4],[5,6,7,8,9,10]),
    ('Машинне навчання',[1,2,3],[4,5,6,7,8,9]),
    ('Архітектура та проєкт ПЗ',[1],[2,3]),
    ('Конструювання програмного забезпечення',list(range(1,8)),[8,9,10,11]),
    ('Філософія',[1,2,3],[4,5,6]),
    ('Фізичне виховання',[1,2],[3,4]),
    ('Проєкт інтерфейсів користувача',list(range(1,8))+[51,52,53,54],list(range(8,15))+[55,56,57,58,59]),
])
def test_attestation_split(name,first,second):
    labs = layout(name)['labs']
    assert [w['number'] for w in labs if w['attestation']==1] == first
    assert [w['number'] for w in labs if w['attestation']==2] == second
    pik = layout('Проєкт інтерфейсів користувача')['labs']
    assert [w['title'] for w in pik][13:16] == ['ЛР 14','Тест 1','Тест 2']


def test_summary_averages_only_filled_cells():
    subject = layout('Програмування в Інтернет')
    cell = lambda number, grade, kind='lab': {'kind':kind,'number':number,'grade':grade}
    assert summary(subject, []) == {k:{'average':None,'filled':0,'total':t} for k,t in (('1',12),('2',9),('all',21))}
    result = summary(subject, [cell(1,5), cell(1,4), cell(2,4), cell(5,3), cell(6,2), cell(1,1,kind='attestation'), cell(99,5)])
    assert result['1'] == {'average':4.33,'filled':3,'total':12}
    assert result['2'] == {'average':2.5,'filled':2,'total':9}
    assert result['all']['average'] == 3.6


def test_view_returns_averages_per_student(conn,make_user):
    make_user(1,'Петренко Іван');make_user(2,'Коваль Андрій')
    journal.submit(conn,1,SUBJECTS,values(part=1,grade=5))
    journal.submit(conn,1,SUBJECTS,values(part=2,grade=4))
    journal.submit(conn,1,SUBJECTS,values(number=8,part=1,grade=3))
    result = journal.view(conn,2,SUBJECTS,period='2026-2027:1')
    own_id, other_id = users.get_user(conn,1)['id'], users.get_user(conn,2)['id']
    mine = result['averages'][str(own_id)][KEY]
    assert (mine['1']['average'], mine['1']['filled'], mine['1']['total']) == (4.5, 2, 11)
    assert (mine['2']['average'], mine['2']['total']) == (3.0, 4)
    assert result['averages'][str(other_id)][KEY]['all']['average'] is None
