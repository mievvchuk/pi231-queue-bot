from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.storage.base import StorageKey

from queue_bot.handlers.consultations import parse_time
from queue_bot.handlers.admin.presentation import get_admin_queue_menu
from queue_bot.services import attendance, consultations, queues, admins
from queue_bot.services.errors import BotError
from queue_bot.services.schedule import UK_TZ
from queue_bot.storage.database import connect
from tests.test_navigation_bans import send


@pytest.fixture
def consultation(conn, make_user, monkeypatch):
    clock = [int(datetime(2026, 11, 26, 12, tzinfo=UK_TZ).timestamp())]
    monkeypatch.setattr(attendance, 'now_seconds', lambda: clock[0])
    for uid, name in [(100, 'Адмін Власник'), (1, 'Петренко Іван'), (2, 'Коваль Андрій')]:
        make_user(uid, name)
    sid = queues.create_session(conn, 100, 1, 1, queue_type='consultation')
    queues.join_queue(conn, 1, sid, lab_numbers=[1], planned_minutes=10)
    queues.join_queue(conn, 2, sid, lab_numbers=[1], planned_minutes=10)
    return sid, clock


@pytest.mark.asyncio
async def test_manual_start_uses_presence_and_existing_call_order(conn, consultation):
    from queue_bot.services.lesson_notifier import deliver
    sid, clock = consultation
    consultations.configure(conn, 100, sid, minutes=60)
    run = attendance.active_run(conn, sid)
    assert run['ends_at'] == clock[0] + 3600
    bot = AsyncMock()
    await deliver(bot, conn, [])
    assert bot.send_message.await_count == 2
    assert 'консультація' in bot.send_message.call_args.args[1]
    for uid in (2, 1):
        own = attendance.view(conn, sid, uid)['own']
        attendance.respond(conn, uid, own['token'], 'present')
    assert attendance.view(conn, sid, 1)['own']['state'] == 'called'
    assert attendance.view(conn, sid, 2)['own']['state'] == 'present'
    before = [dict(e) for e in queues.get_entries(conn, sid)]
    consultations.cancel(conn, 100, sid, stop=True)
    assert attendance.active_run(conn, sid) is None
    assert [dict(e) for e in queues.get_entries(conn, sid)] == before


def test_scheduled_start_survives_restart_and_is_consumed_once(conn, consultation, db_path):
    sid, clock = consultation
    begins = clock[0] + 600
    consultations.configure(conn, 100, sid, minutes=60, starts_at=begins)
    reopened = connect(db_path)
    try:
        consultations.tick(reopened, now=begins - 1)
        assert attendance.active_run(reopened, sid, now=begins) is None
        consultations.tick(reopened, now=begins)
        consultations.tick(reopened, now=begins + 5)
        assert attendance.active_run(reopened, sid, now=begins)['ends_at'] == begins + 3600
        assert reopened.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 1
        assert reopened.execute('SELECT COUNT(*) FROM lesson_messages').fetchone()[0] == 2
    finally:
        reopened.close()


def test_edit_cancel_immediate_start_and_delete_cannot_leave_old_timer(conn, consultation):
    sid, clock = consultation
    consultations.configure(conn, 100, sid, minutes=60, starts_at=clock[0]+600)
    consultations.configure(conn, 100, sid, minutes=30, starts_at=clock[0]+1200)
    consultations.tick(conn, now=clock[0]+600)
    assert attendance.active_run(conn, sid) is None
    consultations.cancel(conn, 100, sid)
    consultations.tick(conn, now=clock[0]+1200)
    assert attendance.active_run(conn, sid) is None
    consultations.configure(conn, 100, sid, minutes=30, starts_at=clock[0]+1200)
    consultations.configure(conn, 100, sid, minutes=60)
    assert consultations.get_plan(conn, sid) is None
    with pytest.raises(BotError):
        consultations.configure(conn, 100, sid, minutes=60)
    consultations.cancel(conn, 100, sid, stop=True)
    consultations.configure(conn, 100, sid, minutes=60, starts_at=clock[0]+600)
    queues.delete_session(conn, 100, sid)
    assert consultations.get_plan(conn, sid) is None
    consultations.tick(conn, now=clock[0]+600)


def test_timers_do_not_start_expired_archived_or_revoked_admin_plans(conn, consultation):
    sid, clock = consultation
    consultations.configure(conn, 100, sid, minutes=10, starts_at=clock[0]+60)
    consultations.tick(conn, now=clock[0]+661)
    assert consultations.get_plan(conn, sid)['status'] == 'expired'
    assert attendance.active_run(conn, sid, now=clock[0]+661) is None
    admins.add_admin(conn, 100, 2)
    consultations.configure(conn, 2, sid, minutes=60, starts_at=clock[0]+60)
    admins.remove_admin(conn, 100, 2)
    consultations.tick(conn, now=clock[0]+60)
    assert consultations.get_plan(conn, sid)['status'] == 'cancelled'
    consultations.configure(conn, 100, sid, minutes=60, starts_at=clock[0]+60)
    queues.finish_session(conn, 100, sid)
    consultations.tick(conn, now=clock[0]+60)
    assert consultations.get_plan(conn, sid) is None


def test_permissions_type_and_duration_are_checked(conn, consultation):
    sid, clock = consultation
    with pytest.raises(BotError):
        consultations.configure(conn, 1, sid, minutes=60)
    with pytest.raises(BotError):
        consultations.cancel(conn, 1, sid, stop=True)
    lesson = queues.create_session(conn, 100, 2, 1)
    with pytest.raises(BotError):
        consultations.configure(conn, 100, lesson, minutes=60)
    for minutes in (0, 9, 241, True, '60'):
        with pytest.raises(BotError):
            consultations.configure(conn, 100, sid, minutes=minutes)
    with pytest.raises(BotError):
        consultations.configure(conn, 100, sid, minutes=60, starts_at=clock[0])


def test_time_input_uses_kyiv_and_rejects_past_and_invalid_values():
    now = datetime(2026, 11, 26, 23, 50, tzinfo=UK_TZ)
    timestamp = int(now.timestamp())
    assert parse_time('relative', '15', timestamp) == timestamp+900
    assert parse_time('today', '23:55', timestamp) == timestamp+300
    assert parse_time('date', '27.11.2026 00:05', timestamp) == timestamp+900
    for mode, text in [('relative', '0'), ('relative', 'abc'), ('today', '00:05'), ('today', '25:00'),
                       ('date', '31.11.2026 18:00'), ('date', '26.11.2026 12:00')]:
        with pytest.raises(BotError):
            parse_time(mode, text, timestamp)


@pytest.fixture
def consultation_app(conn, consultation):
    from queue_bot.routing import ordered_routers, create_dispatcher
    for router in ordered_routers():
        router._parent_router = None
    catalog = [{'id': 1, 'name': 'Програмування', 'teachers': [{'id': 1, 'name': 'Викладач'}]}]
    dp = create_dispatcher(conn, catalog)
    bot = AsyncMock()
    bot.id = 5
    return dp, bot


async def select(app, sid, uid=100):
    key = StorageKey(bot_id=5, chat_id=uid, user_id=uid)
    await app[0].storage.set_data(key, {'session_id':sid, 'ui_mode':'admin'})


@pytest.mark.asyncio
@pytest.mark.parametrize('method, value', [('▶️ Почати зараз', None), ('⏳ Через кілька хвилин', '15'),
    ('🕒 Сьогодні о певній годині', '18:00'), ('📅 Указати дату й час', '27.11.2026 18:00')])
async def test_admin_can_choose_each_start_method(conn, consultation, consultation_app, method, value):
    sid, clock = consultation
    app = consultation_app
    await select(app, sid)
    await send(app, '▶️ Запуск консультації')
    await send(app, method)
    if value:
        await send(app, value)
    await send(app, '60 хв')
    assert attendance.active_run(conn, sid) is None
    await send(app, '✅ Підтвердити запуск')
    if value:
        plan = consultations.get_plan(conn, sid)
        assert plan['status'] == 'pending' and plan['minutes'] == 60
    else:
        assert attendance.active_run(conn, sid)


@pytest.mark.asyncio
async def test_cancelled_form_does_not_change_plan_and_non_admin_cannot_start(conn, consultation, consultation_app):
    sid, clock = consultation
    app = consultation_app
    consultations.configure(conn, 100, sid, minutes=60, starts_at=clock[0]+600)
    original = consultations.get_plan(conn, sid)
    await select(app, sid)
    await send(app, '✏️ Змінити запуск консультації')
    await send(app, '⏳ Через кілька хвилин')
    await send(app, '🔙 Назад до черги')
    assert consultations.get_plan(conn, sid) == original
    await select(app, sid, uid=1)
    await send(app, '▶️ Запуск консультації', uid=1)
    assert attendance.active_run(conn, sid) is None
    assert consultations.get_plan(conn, sid) == original


def test_consultation_controls_are_absent_from_normal_lessons():
    normal = [b.text for r in get_admin_queue_menu(True).keyboard for b in r]
    consultation = [b.text for r in get_admin_queue_menu(True, consultation=True).keyboard for b in r]
    assert '▶️ Запуск консультації' not in normal
    assert '▶️ Запуск консультації' in consultation


@pytest.mark.asyncio
async def test_custom_duration_stop_and_refresh_after_timer_start(conn, consultation, consultation_app):
    from queue_bot.handlers.admin.presentation import open_admin_queue_manager
    from aiogram.types import Message, Chat, User
    sid, clock = consultation
    app = consultation_app
    await select(app, sid)
    await send(app, '▶️ Запуск консультації')
    await send(app, '▶️ Почати зараз')
    await send(app, '✏️ Інша тривалість')
    await send(app, '45')
    await send(app, '✅ Підтвердити запуск')
    assert attendance.active_run(conn, sid)['ends_at'] == clock[0]+2700
    await send(app, '⏹ Зупинити консультацію')
    assert attendance.active_run(conn, sid)
    await send(app, '⏹ Підтвердити зупинку')
    assert attendance.active_run(conn, sid) is None
    assert len(queues.get_entries(conn, sid)) == 2
    consultations.configure(conn, 100, sid, minutes=60, starts_at=clock[0]+60)
    clock[0] += 60
    consultations.tick(conn)
    message = Message(message_id=1, date=datetime.now(), chat=Chat(id=100, type='private'),
                      from_user=User(id=100, first_name='Адмін', is_bot=False)).as_(app[1])
    await open_admin_queue_manager(message, conn, app[0]['subjects'], sid, refresh=True)
    menu = next(call.args[0].reply_markup for call in reversed(app[1].call_args_list)
                if getattr(call.args[0], 'reply_markup', None))
    assert '⏹ Зупинити консультацію' in [b.text for row in menu.keyboard for b in row]
