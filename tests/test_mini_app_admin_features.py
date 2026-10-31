import base64
from io import BytesIO
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from pypdf import PdfReader

from queue_bot import config
from queue_bot.mini_app import admin_api, api
from queue_bot.services import admins, bugs, polls, queues, subjects, users
from queue_bot.web import create_web_app
from tests.test_mini_app import TOKEN, headers


@pytest_asyncio.fixture
async def admin_client(conn, make_user, monkeypatch, tmp_path):
    for uid, name in [(1, 'Студент Тестовий'), (100, 'Власник Тестовий'), (101, 'Адмін Тестовий')]:
        make_user(uid, name)
    admins.add_admin(conn, 100, 101)
    monkeypatch.setattr(config, 'SUBJECTS_PATH', str(tmp_path / 'subjects.json'))
    monkeypatch.setattr(config, 'SCHEDULE_PATH', str(tmp_path / 'schedule.json'))
    monkeypatch.setattr(config, 'MINI_APP_URL', 'https://example.test/app/')
    catalog = [{'id': 1, 'name': 'Програмування', 'teachers': [{'id': 1, 'name': 'Викладач'}]}]
    bot = AsyncMock()
    bot.get_me.return_value = SimpleNamespace(username='test_bot')
    app = create_web_app(conn=conn, subjects=catalog, bot=bot, bot_token=TOKEN)
    # Parent integrates setup; this fixture also works before that edit arrives.
    if not any('/admin/manage/' in str(route.resource) for route in app.router.routes()):
        admin_api.setup(app)
    async with TestClient(TestServer(app)) as client:
        yield client


async def post(client, section, data, uid=100):
    return await client.post('/api/miniapp/admin/manage/' + section + '?mode=admin', headers=headers(uid), json=data)


@pytest.mark.asyncio
@pytest.mark.parametrize('section', ['subjects', 'group', 'history', 'replacements', 'saturdays', 'reports', 'owner', 'admins', 'bugs', 'proposals', 'polls'])
async def test_admin_sections_require_signed_current_role_and_admin_mode(admin_client, conn, section):
    path = '/api/miniapp/admin/manage/' + section
    assert (await admin_client.get(path)).status == 401
    assert (await admin_client.get(path, headers=headers(1))).status == 403
    assert (await admin_client.get(path, headers=headers(100))).status == 403
    assert (await admin_client.get(path+'?mode=admin', headers=headers(100))).status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize('section', ['owner', 'admins', 'bugs', 'proposals'])
async def test_owner_only_sections_reject_regular_admin(admin_client, section):
    assert (await admin_client.get('/api/miniapp/admin/manage/'+section+'?mode=admin', headers=headers(101))).status == 403


@pytest.mark.asyncio
async def test_subject_operations_use_shared_catalog_and_owner_authority(admin_client):
    assert (await post(admin_client, 'subjects', {'action': 'add', 'name': 'Математика', 'teacher_name': 'Викладач Другий'}, 101)).status == 200
    assert (await post(admin_client, 'subjects', {'action': 'rename', 'subject_id': 2, 'name': 'Алгебра'}, 101)).status == 403
    assert (await post(admin_client, 'subjects', {'action': 'rename', 'subject_id': 2, 'name': 'Алгебра'})).status == 200
    assert subjects.find_subject(admin_client.app[api.CONTEXT]['subjects'], 2)['name'] == 'Алгебра'
    assert (await post(admin_client, 'subjects', {'action': 'remove_teacher', 'subject_id': 2, 'teacher_id': 2})).status == 400


@pytest.mark.asyncio
async def test_group_timed_ban_removes_active_entries_and_protects_admins(admin_client, conn):
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_numbers=[1], planned_minutes=10)
    user_id = users.get_user(conn, 1)['id']
    assert (await post(admin_client, 'group', {'action': 'ban', 'user_id': user_id, 'days': True})).status == 400
    assert (await post(admin_client, 'group', {'action': 'ban', 'user_id': user_id, 'days': 2})).status == 200
    assert not queues.get_entries(conn, sid)
    assert users.get_user(conn, 1)['banned_until']
    assert (await post(admin_client, 'group', {'action': 'block', 'user_id': users.get_user(conn, 100)['id']})).status == 400


@pytest.mark.asyncio
async def test_history_finish_and_reports_are_json_serializable(admin_client, conn):
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_numbers=[1], planned_minutes=10)
    entry = queues.get_entries(conn, sid)[0]
    result = await post(admin_client, 'history', {'action': 'finish', 'session_id': sid, 'create_new': True, 'carry': {str(entry['id']): 2}})
    assert result.status == 200
    new_id = (await result.json())['session_id']
    assert queues.get_user_position(conn, 1, new_id) == 2
    active_history = await (await admin_client.get(f'/api/miniapp/admin/manage/history?sid={new_id}&mode=admin', headers=headers(100))).json()
    assert len(active_history['entries']) == 1 and active_history['entries'][0]['status'] == 'active'
    for section in (f'history?sid={sid}&', 'reports?subject_id=1&'):
        result = await admin_client.get('/api/miniapp/admin/manage/'+section+'mode=admin', headers=headers(100))
        assert result.status == 200
        assert isinstance(await result.json(), dict)
    csv = await admin_client.get('/api/miniapp/admin/manage/reports?mode=admin&format=csv', headers=headers(100))
    assert csv.status == 200 and csv.content_type == 'text/csv'
    global_csv = await csv.text()
    subject_csv = await admin_client.get('/api/miniapp/admin/manage/reports?mode=admin&format=csv&subject_id=1', headers=headers(100))
    assert subject_csv.status == 200
    subject_text = await subject_csv.text()
    assert subject_text != global_csv and 'Місце в поточній черзі' in subject_text
    assert 'Студент Тестовий' in subject_text


@pytest.mark.asyncio
async def test_pdf_exports_are_readable_and_admin_only(admin_client, conn):
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_numbers=[1], planned_minutes=10)
    for suffix in ('', '&subject_id=1'):
        path = '/api/miniapp/admin/manage/reports?format=pdf' + suffix
        assert (await admin_client.get(path, headers=headers(100))).status == 403
        assert (await admin_client.get(path + '&mode=admin', headers=headers(1))).status == 403
        result = await admin_client.get(path + '&mode=admin', headers=headers(100))
        assert result.status == 200 and result.content_type == 'application/pdf'
        assert '.pdf' in result.headers['Content-Disposition']
        assert result.headers['Cache-Control'] == 'no-store'
        reader = PdfReader(BytesIO(await result.read()))
        text = '\n'.join(page.extract_text() for page in reader.pages)
        assert 'Студент Тестовий' in text
        assert 'Програмування' in text


@pytest.mark.asyncio
async def test_history_detail_identifies_selected_session_and_rejects_unknown(admin_client, conn):
    sid = queues.create_session(conn, 100, 1, 1)
    result = await admin_client.get(f'/api/miniapp/admin/manage/history?sid={sid}&mode=admin', headers=headers(101))
    assert result.status == 200
    assert (await result.json())['session_id'] == sid
    result = await admin_client.get('/api/miniapp/admin/manage/history?sid=999999&mode=admin', headers=headers(101))
    assert result.status == 400


@pytest.mark.asyncio
async def test_replacement_validation_is_atomic_and_recognition_only_drafts(admin_client, conn, monkeypatch):
    from queue_bot.services import recognition, replacements
    draft = {'date': '2026-11-02', 'confidence': .9, 'replacements': [{'date': '2026-11-02', 'lesson_number': 2, 'new_subject': 'Алгебра', 'source_note': 'З шапки'}]}
    fake = AsyncMock(return_value=draft)
    monkeypatch.setattr(recognition, 'recognize_replacements_from_images', fake)
    image = base64.b64encode(b'\x89PNG\r\n\x1a\nplaceholder').decode()
    result = await post(admin_client, 'replacements', {'action': 'recognize', 'images': [image]})
    assert result.status == 200 and (await result.json())['draft'] == draft
    assert replacements.get_replacements(conn, date='2026-11-02') == []
    assert (await post(admin_client, 'replacements', {'action': 'save', 'items': [draft['replacements'][0], {'date': 'oops'}]})).status == 400
    assert replacements.get_replacements(conn, date='2026-11-02') == []
    assert (await post(admin_client, 'replacements', {'action': 'save', 'items': draft['replacements']})).status == 200
    assert replacements.get_replacements(conn, date='2026-11-02')[0]['new_subject'] == 'Алгебра'


@pytest.mark.asyncio
async def test_recognition_rechecks_role_after_network_wait(admin_client, conn, monkeypatch):
    from queue_bot.services import recognition
    async def revoke(*args, **kwargs):
        conn.execute('DELETE FROM admins WHERE telegram_id = 101')
        return {'replacements': []}
    monkeypatch.setattr(recognition, 'recognize_replacements_from_images', revoke)
    image = base64.b64encode(b'\xff\xd8\xffplaceholder').decode()
    assert (await post(admin_client, 'replacements', {'action': 'recognize', 'images': [image]}, 101)).status == 403


@pytest.mark.asyncio
async def test_schedule_import_is_validated_and_exports_saved_data(admin_client):
    from queue_bot.services import schedule
    invalid = {'monday': [{'pair': True, 'subject': 'Назва'}]}
    assert (await post(admin_client, 'replacements', {'action': 'import_schedule', 'schedule': invalid})).status == 400
    assert schedule.load_schedule_data()['monday'] == []
    document = {'monday': [{'pair': 2, 'subject': 'Алгебра', 'teachers': [{'name': 'Викладач', 'room': '231'}], 'week_type': 'both'}]}
    assert (await post(admin_client, 'replacements', {'action': 'import_schedule', 'schedule': document, 'filename': '../../untrusted.json'})).status == 200
    assert schedule.load_schedule_data() == document
    downloaded = await admin_client.get('/api/miniapp/admin/manage/replacements?format=schedule&mode=admin', headers=headers(100))
    assert downloaded.status == 200 and await downloaded.json() == document
    assert 'schedule.json' in downloaded.headers['Content-Disposition']


@pytest.mark.asyncio
async def test_owner_proposals_bugs_and_maintenance_validate_and_persist(admin_client, conn):
    rid = subjects.create_subject_request(conn, 1, 'Студент Тестовий', 'add_subject', None, 'Алгебра', 'Викладач Другий')['id']
    assert (await post(admin_client, 'proposals', {'action': 'approve', 'id': rid})).status == 200
    assert subjects.get_subject_request(conn, rid)['status'] == 'approved'
    assert (await post(admin_client, 'proposals', {'action': 'approve', 'id': rid})).status == 400
    bug = bugs.create_bug_report(conn, 1, 'Не працює кнопка')
    assert (await post(admin_client, 'bugs', {'action': 'reply', 'report_id': bug, 'text': '<b>Виправлено</b>'})).status == 200
    assert bugs.get_bug_report(conn, bug)['admin_reply'] == '<b>Виправлено</b>'
    assert (await post(admin_client, 'owner', {'action': 'maintenance', 'active': True, 'end_time': '99:88'})).status == 400
    assert (await post(admin_client, 'owner', {'action': 'maintenance', 'active': True, 'end_time': '20:00'})).status == 200


@pytest.mark.asyncio
async def test_saturdays_admin_management_and_group_invitations(admin_client, conn, monkeypatch):
    from queue_bot.services import working_days
    monkeypatch.setattr(working_days, 'now_in_kyiv', lambda: datetime(2026, 10, 31, 12, 0, tzinfo=working_days.UK_TZ))
    assert (await post(admin_client, 'saturdays', {'action': 'save', 'date': '2026-11-02', 'source_day': 'monday'})).status == 400
    assert (await post(admin_client, 'saturdays', {'action': 'save', 'date': '2026-10-31', 'source_day': 'monday', 'week_type': 'denominator'})).status == 200
    assert working_days.get_working_saturday(conn, '2026-10-31')['week_type'] == 'denominator'
    assert (await post(admin_client, 'saturdays', {'action': 'notify', 'date': '2026-10-31'})).status == 200
    assert (await post(admin_client, 'admins', {'action': 'add', 'telegram_id': 123456789012})).status == 200
    assert admins.is_admin(conn, 123456789012)
    assert (await post(admin_client, 'admins', {'action': 'remove', 'telegram_id': 100})).status == 400
    assert (await post(admin_client, 'admins', {'action': 'remove', 'telegram_id': 123456789012})).status == 200
    assert (await post(admin_client, 'polls', {'action': 'group', 'chat_id': -1001234567890})).status == 200
    result = await post(admin_client, 'polls', {'action': 'create', 'question': 'У групі?', 'options': ['Так', 'Ні'], 'target': 'group', 'is_anonymous': True, 'allows_multiple': False})
    assert result.status == 200
    pid = (await result.json())['poll_id']
    assert polls.is_miniapp_poll(conn, pid)
    assert (await (await admin_client.get(f'/api/miniapp/polls/{pid}', headers=headers(1))).json())['can_vote']
    bot = admin_client.app[api.CONTEXT]['bot']
    bot.send_poll.assert_not_awaited()
    assert bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard[0][0].url == 'https://t.me/test_bot'


@pytest.mark.asyncio
async def test_polls_new_delivery_shared_votes_privacy_and_deadlines(admin_client, conn):
    result = await post(admin_client, 'polls', {'action': 'create', 'question': 'Коли?', 'options': ['Сьогодні', 'Завтра'], 'is_anonymous': True, 'allows_multiple': False, 'target': 'private'})
    assert result.status == 200
    pid = (await result.json())['poll_id']
    assert polls.is_miniapp_poll(conn, pid)
    bot = admin_client.app[api.CONTEXT]['bot']
    bot.send_poll.assert_not_awaited()
    path = f'/api/miniapp/polls/{pid}'
    assert (await admin_client.post(path+'/vote', headers=headers(1), json={'options': [True]})).status == 400
    assert (await admin_client.post(path+'/vote', headers=headers(1), json={'options': [0]})).status == 200
    assert (await admin_client.post(path+'/vote', headers=headers(1), json={'options': [1]})).status == 200
    details = await (await admin_client.get(path, headers=headers(1))).json()
    assert details['results']['total_voters'] == 1 and details['my_options'] == [1]
    assert all(not option['voters'] for option in details['results']['options'])
    assert 'Студент Тестовий' not in str(details) and 'telegram_id' not in str(details)
    conn.execute('UPDATE polls SET closes_at = ? WHERE id = ?', ((datetime.now(polls.UK_TZ)-timedelta(seconds=1)).isoformat(), pid))
    assert (await admin_client.post(path+'/vote', headers=headers(1), json={'options': [0]})).status == 400
    assert not (await (await admin_client.get(path, headers=headers(1))).json())['can_vote']


@pytest.mark.asyncio
async def test_legacy_anonymous_group_readonly_and_native_identity_shared(admin_client, conn):
    pid = polls.create_poll(conn, 100, 'Питання', ['Так', 'Ні'], True, False, 'group')
    polls.register_poll_instance(conn, pid, -100, 1, 'anonymous')
    polls.record_poll_state_update(conn, 'anonymous', [3, 2])
    path = f'/api/miniapp/polls/{pid}'
    details = await (await admin_client.get(path, headers=headers(1))).json()
    assert not details['can_vote'] and details['can_vote_reason']
    assert details['results']['total_votes_cast'] == 5
    assert (await admin_client.post(path+'/vote', headers=headers(1), json={'options': [0]})).status == 400
    pid = polls.create_poll(conn, 100, 'Питання', ['Так', 'Ні'], False)
    polls.register_poll_instance(conn, pid, 1, 2, 'identified')
    polls.record_poll_answer(conn, 'identified', 1, 'Native', [0])
    assert (await admin_client.post(f'/api/miniapp/polls/{pid}/vote', headers=headers(1), json={'options': [1]})).status == 200
    assert polls.get_poll_results(conn, pid)['total_voters'] == 1
    conn.execute('UPDATE polls SET closes_at = ? WHERE id = ?', ((datetime.now(polls.UK_TZ)-timedelta(seconds=1)).isoformat(), pid))
    assert polls.record_poll_answer(conn, 'identified', 1, 'Native', [0]) is None
    assert polls.record_poll_state_update(conn, 'identified', [5, 5]) is None


@pytest.mark.asyncio
async def test_old_private_anonymous_votes_share_identity_and_closed_votes_rejected(admin_client, conn):
    pid = polls.create_poll(conn, 100, 'Питання', ['Так', 'Ні'], True)
    polls.register_poll_instance(conn, pid, 1, 2, 'private-anonymous')
    polls.record_poll_state_update(conn, 'private-anonymous', [1, 0])
    path = f'/api/miniapp/polls/{pid}/vote'
    assert (await admin_client.post(path, headers=headers(1), json={'options': [1]})).status == 200
    assert polls.get_poll_results(conn, pid)['total_voters'] == 1
    conn.execute("UPDATE polls SET status = 'closed' WHERE id = ?", (pid,))
    assert (await admin_client.post(path, headers=headers(1), json={'options': [0]})).status == 400


@pytest.mark.asyncio
async def test_legacy_multiple_anonymous_group_does_not_invent_unique_voters(admin_client, conn):
    pid = polls.create_poll(conn, 100, 'Питання', ['Так', 'Ні'], True, True, 'group')
    polls.register_poll_instance(conn, pid, -100, 1, 'legacy-multiple')
    polls.record_poll_state_update(conn, 'legacy-multiple', [3, 2])
    result = await (await admin_client.get(f'/api/miniapp/polls/{pid}', headers=headers(1))).json()
    assert result['results']['total_voters'] is None
    assert result['results']['total_votes_cast'] == 5
    assert [option['percent'] for option in result['results']['options']] == [None, None]


@pytest.mark.asyncio
@pytest.mark.parametrize('choices,expected', [([1], [0, 1]), ([], [0, 0])])
async def test_closing_legacy_private_anonymous_poll_preserves_web_vote(admin_client, conn, choices, expected):
    pid = polls.create_poll(conn, 100, 'Питання', ['Так', 'Ні'], True)
    polls.register_poll_instance(conn, pid, 1, 2, 'old-private')
    polls.record_poll_state_update(conn, 'old-private', [1, 0])
    assert (await admin_client.post(f'/api/miniapp/polls/{pid}/vote', headers=headers(1), json={'options': choices})).status == 200
    assert polls.has_miniapp_vote(conn, pid, 1)
    # Even another native update before closing lacks a trustworthy timestamp.
    polls.record_poll_state_update(conn, 'old-private', [1, 0])
    bot = admin_client.app[api.CONTEXT]['bot']
    bot.stop_poll.return_value = SimpleNamespace(options=[SimpleNamespace(voter_count=1), SimpleNamespace(voter_count=0)])
    await polls.close_poll_and_summarize(bot, conn, pid)
    assert [option['count'] for option in polls.get_poll_results(conn, pid)['options']] == expected


@pytest.mark.asyncio
async def test_expired_legacy_close_captures_final_native_snapshot_and_rejects_late_events(admin_client, conn):
    closing = (datetime.now(polls.UK_TZ)-timedelta(seconds=1)).isoformat()
    pid = polls.create_poll(conn, 100, 'Питання', ['Так', 'Ні'], True, closes_at=closing)
    polls.register_poll_instance(conn, pid, 1, 2, 'expired-private')
    assert polls.record_poll_state_update(conn, 'expired-private', [1, 0]) is None
    bot = admin_client.app[api.CONTEXT]['bot']
    bot.stop_poll.return_value = SimpleNamespace(options=[SimpleNamespace(voter_count=0), SimpleNamespace(voter_count=1)])
    await polls.close_poll_and_summarize(bot, conn, pid)
    assert [option['count'] for option in polls.get_poll_results(conn, pid)['options']] == [0, 1]
    assert polls.record_poll_state_update(conn, 'expired-private', [1, 0]) is None


@pytest.mark.asyncio
async def test_owner_bug_projection_indicates_photo_without_telegram_file_id(admin_client, conn):
    bugs.create_bug_report(conn, 1, 'Фото', photo_file_id='sensitive-telegram-file-id')
    payload = await (await admin_client.get('/api/miniapp/admin/manage/bugs?mode=admin', headers=headers(100))).json()
    assert payload['items'][0]['has_photo'] is True
    assert 'sensitive-telegram-file-id' not in str(payload)

@pytest.mark.asyncio
async def test_archived_saturday_is_not_upcoming_or_repeated_next_week(admin_client, conn, monkeypatch):
    from queue_bot.services import working_days
    monkeypatch.setattr(working_days, 'now_in_kyiv', lambda: datetime(2026, 10, 31, 13, 30, tzinfo=working_days.UK_TZ))
    working_days.set_working_saturday(conn, '2026-10-31', 'monday', 'denominator')
    working_days.set_working_saturday(conn, '2026-11-14', 'tuesday', 'numerator')
    result = await admin_client.get('/api/miniapp/admin/manage/saturdays?mode=admin', headers=headers(100))
    data = await result.json()
    assert [item['date'] for item in data['items']] == ['2026-11-14']
    assert [item['date'] for item in data['archived']] == ['2026-10-31']
    assert data['server_time'].startswith('2026-10-31T13:30:00')
    assert data['archived'][0]['archive_at'] == '2026-10-31T13:30:00+02:00'
    assert (await post(admin_client, 'saturdays', {'action': 'notify', 'date': '2026-10-31'})).status == 400
    future = await (await admin_client.get('/api/miniapp/schedule?date=2026-11-07', headers=headers(1))).json()
    assert future['source_day'] is None and not future['working_day_archived']
    history = await (await admin_client.get('/api/miniapp/schedule?date=2026-10-31', headers=headers(1))).json()
    assert history['source_day'] == 'Понеділок' and history['working_day_archived']

@pytest.mark.asyncio
@pytest.mark.parametrize('action',['delete','save'])
async def test_stale_mini_app_form_cannot_mutate_archived_saturday(admin_client, conn, monkeypatch, action):
    from queue_bot.services import working_days
    working_days.set_working_saturday(conn,'2026-10-31','monday','denominator')
    monkeypatch.setattr(working_days,'now_in_kyiv',lambda:datetime(2026,10,31,13,30,tzinfo=working_days.ARCHIVE_TZ))
    result=await post(admin_client,'saturdays',{'action':action,'date':'2026-10-31','source_day':'tuesday','week_type':'numerator'})
    assert result.status==400
    row=working_days.get_working_saturday(conn,'2026-10-31')
    assert row and row['replaces_day']=='monday' and row['week_type']=='denominator'
