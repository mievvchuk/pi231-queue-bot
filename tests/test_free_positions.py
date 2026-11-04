import pytest

from queue_bot.services import attendance, queues, users
from queue_bot.services.errors import BotError
from tests.test_mini_app import client, headers


@pytest.mark.asyncio
async def test_seven_students_can_choose_fourteenth_and_reuse_last_vacancy(client, conn, make_user):
    sid = queues.create_session(conn,100,1,1)
    for n in range(10,17):
        make_user(n,'Студент Тест'+chr(1040+n))
        queues.join_queue(conn,n,sid)
    path = f'/api/miniapp/queues/{sid}'
    post = {'position':14,'labs':[1],'minutes':10}
    assert (await client.post(path+'/join',headers=headers(1),json=post)).status == 200
    assert queues.get_user_position(conn,1,sid) == 14
    assert all(r['user_id'] is None for r in queues.get_entries(conn,sid)[7:13])
    before = [(r['id'],r['position']) for r in queues.get_entries(conn,sid)[:7]]
    entry = queues._own_active_entry(conn,1,sid)
    assert (await client.post(path+'/leave',headers=headers(1),json={'entry_id':entry['id'],'revision':entry['entry_revision']})).status == 200
    data = await (await client.get(path,headers=headers(2))).json()
    assert data['join_slots'][13] == {'number':14,'state':'free'}
    assert [(r['id'],r['position']) for r in queues.get_entries(conn,sid)[:7]] == before
    assert (await client.post(path+'/join',headers=headers(2),json=post)).status == 200
    row = queues._own_active_entry(conn,2,sid)
    assert row['id'] == entry['id'] and row['passed_labs'] == '[]'
    assert (await client.post(path+'/join',headers=headers(1),json=post)).status == 400


def test_hundredth_slot_and_admin_removal_preserve_number(conn, make_user):
    make_user(1,'Петренко Іван')
    make_user(2,'Коваль Андрій')
    sid = queues.create_session(conn,100,1,1)
    queues.join_queue(conn,1,sid,position=100)
    row = queues._own_active_entry(conn,1,sid)
    queues.remove_entry(conn,100,row['id'])
    assert queues.get_entries(conn,sid)[99]['user_id'] is None
    queues.join_queue(conn,2,sid,position=100)
    assert queues.get_user_position(conn,2,sid) == 100
    for pos in (0,101,True,1.5):
        with pytest.raises(BotError):
            queues.join_queue(conn,1,sid,position=pos)


def test_vacant_slot_never_inherits_attendance_or_laboratory_results(conn, make_user):
    make_user(1,'Петренко Іван')
    make_user(2,'Коваль Андрій')
    sid = queues.create_session(conn,100,1,1)
    queues.join_queue(conn,1,sid,position=14,lab_numbers=[1,2],planned_minutes=15)
    attendance.start(conn,100,sid)
    attendance.respond(conn,1,attendance.view(conn,sid,1)['own']['token'],'present')
    queues.leave_queue(conn,1,sid)
    queues.join_queue(conn,2,sid,position=14,lab_numbers=[3],planned_minutes=10)
    attendance.tick(conn)
    state = attendance.view(conn,sid,2)
    assert state['own']['state'] == 'pending'
    assert queues._own_active_entry(conn,2,sid)['selected_labs'] == '[3]'
