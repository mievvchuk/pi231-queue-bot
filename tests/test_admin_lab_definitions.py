from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.storage.base import StorageKey

from queue_bot import config
from queue_bot.services import lab_catalog, lab_definitions, queues
from queue_bot.services.subjects import load_subjects
from queue_bot.services.errors import BotError
from queue_bot.storage.database import connect
from tests.test_mini_app import client, headers
from tests.test_navigation_bans import send


@pytest.fixture
def catalog():
    return load_subjects(config.SUBJECTS_PATH)


def test_batch_updates_titles_adds_numbers_and_keeps_limits_and_other_labs(conn, catalog):
    before = lab_catalog.labs_for_session({'subject_id':1}, conn=conn)
    lab_definitions.save_batch(conn, 100, catalog, 1, '1. Тема першої роботи\n8 | Вебзастосунок | 30')
    labs = {l['number']:l for l in lab_catalog.labs_for_session({'subject_id':1}, conn=conn)}
    assert len(labs) == 8
    assert labs[1] == {'number':1, 'title':'Тема першої роботи', 'max_minutes':40}
    assert labs[8] == {'number':8, 'title':'Вебзастосунок', 'max_minutes':30}
    assert labs[2] == before[1]
    assert lab_catalog.labs_for_session({'subject_id':1}) == before
    assert conn.execute("SELECT action FROM admin_log WHERE action = 'save_lab_definitions'").fetchone()


def test_titles_and_new_subject_labs_survive_restart_without_cross_database_cache(conn, db_path, tmp_path, catalog):
    assert lab_catalog.labs_for_session({'subject_id':6}, conn=conn) == ()
    lab_definitions.save_batch(conn, 100, catalog, 6, '1. Проєктування інтерфейсу\n2. Прототип')
    reopened = connect(db_path)
    other = connect(str(tmp_path/'other.db'))
    try:
        assert [l['title'] for l in lab_catalog.labs_for_session({'subject_id':6}, conn=reopened)] == ['Проєктування інтерфейсу', 'Прототип']
        assert lab_catalog.selection_limit({'subject_id':6}, [1,2], conn=reopened) == 40
        assert lab_catalog.labs_for_session({'subject_id':6}, conn=other) == ()
    finally:
        reopened.close()
        other.close()


@pytest.mark.parametrize('text', ['', 'без номера', '0. Назва', '101. Назва', '1 | ',
    '1. '+('А'*101), '1. Назва\n1. Інша назва', '1 | Назва | 0', '1 | Назва | 241', '1 | Назва | abc'])
def test_invalid_batches_do_not_partially_save(conn, catalog, text):
    with pytest.raises(BotError):
        lab_definitions.save_batch(conn, 100, catalog, 6, text)
    assert lab_catalog.labs_for_session({'subject_id':6}, conn=conn) == ()


def test_batch_permissions_total_limit_and_audit_rollback(conn, catalog):
    with pytest.raises(BotError):
        lab_definitions.save_batch(conn, 1, catalog, 6, '1. Назва')
    with pytest.raises(BotError):
        lab_definitions.save_batch(conn, 100, catalog, 999, '1. Назва')
    lab_definitions.save_batch(conn, 100, catalog, 6, '\n'.join(f'{n}. Робота {n}' for n in range(1,21)))
    with pytest.raises(BotError):
        lab_definitions.save_batch(conn, 100, catalog, 6, '21. Зайва робота')
    conn.execute("CREATE TRIGGER fail_lab_audit BEFORE INSERT ON admin_log WHEN NEW.action = 'save_lab_definitions' BEGIN SELECT RAISE(ABORT, 'audit error'); END")
    with pytest.raises(Exception, match='audit error'):
        lab_definitions.save_batch(conn, 100, catalog, 6, '1. Оновлена назва\n2. Ще одна')
    assert lab_catalog.labs_for_session({'subject_id':6}, conn=conn)[0]['title'] == 'Робота 1'


def test_student_can_join_new_lab_complete_it_and_keep_existing_entry_when_title_changes(conn, catalog, make_user):
    make_user(1, 'Петренко Іван')
    lab_definitions.save_batch(conn, 100, catalog, 6, '1. Дизайн\n2 | Прототип | 30')
    sid = queues.create_session(conn, 100, 6, 6)
    queues.join_queue(conn, 1, sid, lab_numbers=[1,2], planned_minutes=35)
    entry = dict(queues.get_entries(conn, sid)[0])
    lab_definitions.save_batch(conn, 100, catalog, 6, '1. Візуальний дизайн')
    assert dict(queues.get_entries(conn, sid)[0]) == entry
    remaining = queues.record_own_passed_lab_numbers(conn, 1, sid, [1], entry_id=entry['id'], expected_revision=entry['entry_revision'])
    assert remaining == [2]
    assert queues.get_user_position(conn, 1, sid) == 1


@pytest.mark.asyncio
async def test_miniapp_student_receives_titles_and_server_enforces_added_lab_limit(conn, client, catalog):
    lab_definitions.save_batch(conn, 100, catalog, 1, '8 | API та маршрути | 30')
    sid = queues.create_session(conn, 100, 1, 1)
    url = f'/api/miniapp/queues/{sid}'
    payload = await (await client.get(url, headers=headers(1))).json()
    assert next(l for l in payload['labs'] if l['number']==8)['title'] == 'API та маршрути'
    assert (await client.post(url+'/join', headers=headers(1), json={'labs':[8],'minutes':31})).status == 400
    assert (await client.post(url+'/join', headers=headers(1), json={'labs':[8],'minutes':30})).status == 200


@pytest.fixture
def labs_app(conn, make_user, catalog):
    from queue_bot.routing import ordered_routers, create_dispatcher
    make_user(100, 'Адмін Власник')
    make_user(1, 'Петренко Іван')
    for router in ordered_routers():
        router._parent_router = None
    dp = create_dispatcher(conn, catalog)
    bot = AsyncMock()
    bot.id = 5
    return dp, bot


@pytest.mark.asyncio
async def test_admin_subject_batch_preview_confirmation_and_student_picker(conn, labs_app):
    app = labs_app
    await send(app, '🧪 Лабораторні')
    await send(app, '🧪 Проєкт інтерфейсів користувача ПС')
    await send(app, '➕ Додати / змінити лабораторні')
    await send(app, '1. Макет інтерфейсу\n2 | Інтерактивний прототип | 30')
    assert lab_catalog.labs_for_session({'subject_id':6}, conn=conn) == ()
    assert 'Інтерактивний прототип' in app[1].call_args.args[0].text
    await send(app, '✅ Зберегти лабораторні')
    assert len(lab_catalog.labs_for_session({'subject_id':6}, conn=conn)) == 2
    sid = queues.create_session(conn, 100, 6, 6)
    from queue_bot.handlers.lab_counts import begin_lab_count
    from aiogram.fsm.context import FSMContext
    from aiogram.types import Message, Chat, User
    from datetime import datetime
    state = FSMContext(storage=app[0].storage, key=StorageKey(bot_id=5,chat_id=1,user_id=1))
    message = Message(message_id=1, date=datetime.now(), chat=Chat(id=1,type='private'),
        from_user=User(id=1,is_bot=False,first_name='Іван')).as_(app[1])
    await begin_lab_count(message, state, conn, sid, subjects=app[0]['subjects'])
    assert 'Макет інтерфейсу' in app[1].call_args.args[0].text
    assert 'Інтерактивний прототип' in app[1].call_args.args[0].text


@pytest.mark.asyncio
async def test_cancelling_preview_and_non_admin_cannot_save(conn, labs_app):
    app = labs_app
    await send(app, '🧪 Лабораторні')
    await send(app, '🧪 Проєкт інтерфейсів користувача ПС')
    await send(app, '➕ Додати / змінити лабораторні')
    await send(app, '1. Макет')
    await send(app, '🔙 До лабораторних')
    await send(app, '✅ Зберегти лабораторні')
    await send(app, '🧪 Лабораторні', uid=1)
    await send(app, '1. Чужа робота', uid=1)
    await send(app, '✅ Зберегти лабораторні', uid=1)
    assert lab_catalog.labs_for_session({'subject_id':6}, conn=conn) == ()
