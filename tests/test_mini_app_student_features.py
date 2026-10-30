from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from tests.test_mini_app import headers
from queue_bot.services import queues, users, restrictions
from queue_bot.web import create_web_app

@pytest_asyncio.fixture
async def feature_client(conn, make_user):
    make_user(1, 'Петренко Іван')
    make_user(2, 'Коваль Андрій')
    make_user(100, 'Адмін Власник')
    catalog=[{'id':1,'name':'Програмування в Інтернет','teachers':[{'id':1,'name':'Викладач'}]},
             {'id':7,'name':'Курсова робота з КПЗ','teachers':[{'id':1,'name':'Викладач'}]}]
    app=create_web_app(conn=conn, subjects=catalog, bot=AsyncMock(), bot_token='123456:offline-test-token')
    async with TestClient(TestServer(app)) as client:
        yield client

@pytest.mark.asyncio
async def test_miniapp_registration_and_catalog_use_verified_identity(feature_client, conn):
    response=await feature_client.get('/api/miniapp/bootstrap',headers=headers(999))
    assert response.status==200
    payload=await response.json()
    assert payload['me']['is_registered'] is False and payload['queues']==[]
    assert (await feature_client.get('/api/miniapp/catalog',headers=headers(999))).status==403
    response=await feature_client.post('/api/miniapp/register',headers=headers(999),json={'full_name':'Мельник Олена','telegram_id':2})
    assert response.status==200
    assert users.get_user(conn,999)['full_name']=='Мельник Олена'
    assert users.get_user(conn,2)['full_name']=='Коваль Андрій'
    catalog=await (await feature_client.get('/api/miniapp/catalog',headers=headers(999))).json()
    assert catalog['subjects'][0]['id']==1

@pytest.mark.asyncio
async def test_registration_does_not_lift_placeholder_ban(feature_client, conn):
    placeholder=users.admin_create_placeholder(conn,100,'Мельник Олена')
    restrictions.set_restriction(conn,100,placeholder['id'],days=2)
    response=await feature_client.post('/api/miniapp/register',headers=headers(999),json={'full_name':'Мельник Олена'})
    assert response.status==403 and users.get_user(conn,999) is None

@pytest.mark.asyncio
async def test_student_creates_valid_queue_and_duplicate_returns_existing(feature_client,conn):
    payload={'subject_id':1,'teacher_id':1,'type':'normal'}
    response=await feature_client.post('/api/miniapp/queues',headers=headers(),json=payload)
    assert response.status==200
    sid=(await response.json())['session_id']
    again=await feature_client.post('/api/miniapp/queues',headers=headers(),json=payload)
    assert (await again.json())['session_id']==sid
    assert (await feature_client.post('/api/miniapp/queues',headers=headers(),json={**payload,'teacher_id':999})).status==400

@pytest.mark.asyncio
async def test_wish_and_not_passed_preserve_labs_and_own_entry(feature_client,conn):
    sid=queues.create_session(conn,100,1,1)
    queues.join_queue(conn,1,sid,lab_numbers=[1,2],planned_minutes=15)
    queues.join_queue(conn,2,sid,lab_numbers=[1],planned_minutes=10)
    entry=queues._own_active_entry(conn,1,sid)
    ref={'entry_id':entry['id'],'revision':entry['entry_revision']}
    url=f'/api/miniapp/queues/{sid}'
    assert (await feature_client.post(url+'/wish',headers=headers(),json={**ref,'wish':'higher'})).status==400
    assert (await feature_client.post(url+'/wish',headers=headers(),json={**ref,'wish':'lower'})).status==200
    assert (await feature_client.post(url+'/not-passed',headers=headers(2),json=ref)).status==400
    assert (await feature_client.post(url+'/not-passed',headers=headers(),json=ref)).status==200
    after=queues._own_active_entry(conn,1,sid)
    assert after['position']==2 and after['selected_labs']==entry['selected_labs']

@pytest.mark.asyncio
async def test_private_bug_reports_and_proposals_never_leak_between_students(feature_client,conn):
    assert (await feature_client.post('/api/miniapp/bugs',headers=headers(),json={'text':'Не працює кнопка'})).status==200
    mine=await (await feature_client.get('/api/miniapp/bugs',headers=headers())).json()
    peer=await (await feature_client.get('/api/miniapp/bugs',headers=headers(2))).json()
    assert len(mine['items'])==1 and peer['items']==[]
    assert 'user_telegram_id' not in mine['items'][0]
    assert (await feature_client.post('/api/miniapp/proposals',headers=headers(),json={'kind':'teacher','subject_id':1,'teacher_name':'Інший Викладач'})).status==200
    own=await (await feature_client.get('/api/miniapp/proposals',headers=headers())).json()
    peer=await (await feature_client.get('/api/miniapp/proposals',headers=headers(2))).json()
    assert len(own['items'])==1 and peer['items']==[]
    assert 'user_telegram_id' not in own['items'][0]

@pytest.mark.asyncio
async def test_admin_add_students_batch_validates_and_can_use_existing_names(feature_client,conn):
    sid=queues.create_session(conn,100,1,1)
    response=await feature_client.post(f'/api/miniapp/admin/queues/{sid}?mode=admin',headers=headers(100),json={'action':'add_students','names':['Петренко Іван','Мельник Олена']})
    assert response.status==200
    assert len(queues.get_entries(conn,sid))==2
    response=await feature_client.post(f'/api/miniapp/admin/queues/{sid}?mode=admin',headers=headers(100),json={'action':'add_students','names':['Новий Студент','X']})
    assert response.status==400 and len(queues.get_entries(conn,sid))==2
@pytest.mark.asyncio
async def test_normal_queue_without_lab_catalog_can_retry(feature_client,conn):
    from queue_bot.mini_app import api
    feature_client.server.app[api.CONTEXT]['subjects'].append({'id':99,'name':'Новий предмет','teachers':[{'id':1,'name':'Викладач'}]})
    sid=queues.create_session(conn,100,99,1)
    queues.join_queue(conn,1,sid)
    queues.join_queue(conn,2,sid)
    entry=queues._own_active_entry(conn,1,sid)
    response=await feature_client.post(f'/api/miniapp/queues/{sid}/not-passed',headers=headers(),json={'entry_id':entry['id'],'revision':entry['entry_revision']})
    assert response.status==200 and queues.get_user_position(conn,1,sid)==2

@pytest.mark.asyncio
async def test_bug_screenshot_is_private_and_large_description_reaches_owner(feature_client,conn):
    import base64
    from types import SimpleNamespace
    from queue_bot.mini_app import api
    bot=feature_client.server.app[api.CONTEXT]['bot']
    bot.send_photo.return_value=SimpleNamespace(photo=[SimpleNamespace(file_id='test-photo')])
    png=base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'offline-photo').decode()
    response=await feature_client.post('/api/miniapp/bugs',headers=headers(),json={'text':'Опис '*350,'image':png})
    assert response.status==200
    assert all(len(call.kwargs.get('caption',''))<=1024 for call in bot.send_photo.await_args_list)
    assert bot.send_message.await_count>=1
    rid=(await response.json())['report_id']
    assert (await feature_client.get(f'/api/miniapp/bugs/{rid}/photo',headers=headers(2))).status==403

@pytest.mark.asyncio
async def test_bug_screenshot_cannot_write_after_ban_during_upload(feature_client,conn):
    import base64
    from types import SimpleNamespace
    from queue_bot.mini_app import api
    bot=feature_client.server.app[api.CONTEXT]['bot']
    async def upload(*args,**kwargs):
        restrictions.set_restriction(conn,100,users.get_user(conn,1)['id'],days=1)
        return SimpleNamespace(photo=[SimpleNamespace(file_id='test-photo')])
    bot.send_photo.side_effect=upload
    png=base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'offline-photo').decode()
    response=await feature_client.post('/api/miniapp/bugs',headers=headers(),json={'text':'Скріншот','image':png})
    assert response.status==403 and conn.execute('SELECT COUNT(*) FROM bug_reports').fetchone()[0]==0
