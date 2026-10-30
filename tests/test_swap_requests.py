from datetime import datetime, timedelta, timezone

import pytest

from queue_bot.services import queues, swap_requests
from queue_bot.services.errors import BotError
from queue_bot.storage import database


@pytest.fixture
def pair(conn, make_user):
    sid = queues.create_session(conn, 100, 1, 1)
    make_user(1, 'Студент Один')
    make_user(2, 'Студент Два')
    queues.join_queue(conn, 1, sid)
    queues.join_queue(conn, 2, sid)
    a, b = queues.get_entries(conn, sid)
    return a['id'], b['id']


NOW = datetime(2026, 10, 29, 10, tzinfo=timezone.utc)


def test_pending_duplicate_and_reverse_blocked_until_exact_deadline(conn, pair):
    a, b = pair
    request = swap_requests.create(conn, 1, a, b, now=NOW)
    for actor, left, right in [(1, a, b), (2, b, a)]:
        with pytest.raises(BotError, match='10 хв'):
            swap_requests.create(conn, actor, left, right, now=NOW + timedelta(minutes=9, seconds=59))
    newer = swap_requests.create(conn, 1, a, b, now=NOW + timedelta(minutes=10))
    assert newer['token'] != request['token']
    assert conn.execute('SELECT status FROM swap_requests WHERE id = ?', (request['id'],)).fetchone()['status'] == 'expired'
    with pytest.raises(BotError, match='10 хв'):
        swap_requests.respond(conn, 2, request['token'], accept=True, now=NOW + timedelta(minutes=10))
    assert queues.get_entry(conn, a)['position'] == 1


def test_request_survives_restart_and_expires_for_both_answers(conn, pair, db_path):
    a, b = pair
    request = swap_requests.create(conn, 1, a, b, now=NOW)
    reopened = database.connect(db_path)
    try:
        for accept in (False, True):
            with pytest.raises(BotError, match='10 хв'):
                swap_requests.respond(reopened, 2, request['token'], accept=accept, now=NOW + timedelta(minutes=10))
        assert reopened.execute('SELECT status FROM swap_requests WHERE id = ?', (request['id'],)).fetchone()['status'] == 'expired'
    finally:
        reopened.close()


def test_only_recipient_can_answer_and_accepted_exchange_remains_repeatable(conn, pair):
    a, b = pair
    request = swap_requests.create(conn, 1, a, b, now=NOW)
    with pytest.raises(BotError):
        swap_requests.respond(conn, 1, request['token'], accept=True, now=NOW)
    swap_requests.respond(conn, 2, request['token'], accept=True, now=NOW)
    assert queues.get_entry(conn, a)['position'] == 2
    swap_requests.respond(conn, 2, request['token'], accept=True, now=NOW + timedelta(hours=1))
    assert queues.get_entry(conn, a)['position'] == 1
    fresh = swap_requests.create(conn, 1, a, b, now=NOW)
    swap_requests.respond(conn, 2, fresh['token'], accept=True, now=NOW)
    assert queues.get_entry(conn, a)['position'] == 2


def test_decline_and_failed_delivery_allow_retry(conn, pair):
    a, b = pair
    request = swap_requests.create(conn, 1, a, b, now=NOW)
    swap_requests.respond(conn, 2, request['token'], accept=False, now=NOW)
    fresh = swap_requests.create(conn, 1, a, b, now=NOW)
    swap_requests.cancel_delivery(conn, fresh['token'])
    assert swap_requests.create(conn, 1, a, b, now=NOW)['token'] != fresh['token']


def test_request_rejects_reused_slot_and_changed_queue(conn, pair):
    a, b = pair
    request = swap_requests.create(conn, 1, a, b, now=NOW)
    sid = queues.get_entry(conn, b)['session_id']
    queues.leave_queue(conn, 2, sid)
    queues.join_queue(conn, 2, sid)
    with pytest.raises(BotError):
        swap_requests.respond(conn, 2, request['token'], accept=True, now=NOW)
    assert queues.get_entry(conn, a)['position'] == 1


def test_sender_ownership_and_cross_queue_rejected(conn, pair, make_user):
    a, b = pair
    with pytest.raises(BotError):
        swap_requests.create(conn, 2, a, b, now=NOW)
    sid = queues.create_session(conn, 100, 2, 1)
    queues.join_queue(conn, 2, sid)
    other = queues._own_active_entry(conn, 2, sid)
    with pytest.raises(BotError):
        swap_requests.create(conn, 1, a, other['id'], now=NOW)


def test_rejoining_another_slot_cannot_duplicate_pending_student_pair(conn, pair):
    a, b = pair
    sid = queues.get_entry(conn, a)['session_id']
    swap_requests.create(conn, 1, a, b, now=NOW)
    queues.leave_queue(conn, 1, sid)
    queues.join_queue(conn, 1, sid)
    new_entry = queues._own_active_entry(conn, 1, sid)
    assert new_entry['id'] != a
    with pytest.raises(BotError, match='10 хв'):
        swap_requests.create(conn, 1, new_entry['id'], b, now=NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['direct', 'notification'])
async def test_both_send_paths_share_duplicate_guard_and_delivery_retry(conn, pair, monkeypatch, path):
    from unittest.mock import AsyncMock, MagicMock
    from aiogram.types import Message
    from queue_bot.handlers import student
    a, b = pair
    monkeypatch.setattr(student, 'open_queue_screen', AsyncMock())
    event = MagicMock()
    event.from_user.id = 1
    event.bot.send_message = AsyncMock()
    event.message = MagicMock(spec=Message)
    event.message.edit_text = AsyncMock()
    event.text = '✅ Підтвердити обмін'
    event.data = f'sw_req:{a}:{b}'
    state = AsyncMock()
    state.get_data.return_value = {'session_id': queues.get_entry(conn, a)['session_id'], 'target_entry_id': b}
    subjects = [{'id': 1, 'name': 'Предмет', 'teachers': [{'id': 1, 'name': 'Викладач'}]}]

    async def send():
        if path == 'direct':
            await student.swap_confirm_action(event, state, conn, subjects)
        else:
            await student.swap_request_from_notification(event, conn, subjects)

    event.bot.send_message.side_effect = RuntimeError('delivery failed')
    with pytest.raises(BotError):
        await send()
    event.bot.send_message.side_effect = None
    await send()
    assert '10 хв' in event.bot.send_message.call_args.args[1]
    request = conn.execute("SELECT * FROM swap_requests WHERE status = 'pending'").fetchone()
    keyboard = event.bot.send_message.call_args.kwargs['reply_markup']
    assert keyboard.inline_keyboard[0][0].callback_data == f"sw_yes:{request['token']}"
    with pytest.raises(BotError, match='10 хв'):
        await send()
    assert event.bot.send_message.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('accept', [True, False])
async def test_answer_handlers_reject_expired_and_preupgrade_buttons(conn, pair, monkeypatch, accept):
    from unittest.mock import AsyncMock, MagicMock
    from queue_bot.handlers import student
    a, b = pair
    request = swap_requests.create(conn, 1, a, b, now=NOW)
    monkeypatch.setattr(swap_requests, 'utc_now', lambda: NOW + timedelta(minutes=10))
    event = MagicMock()
    event.from_user.id = 2
    event.bot.send_message = AsyncMock()
    prefix = 'sw_yes' if accept else 'sw_no'
    event.data = f"{prefix}:{request['token']}"
    handler = student.swap_accept if accept else student.swap_decline
    with pytest.raises(BotError, match='10 хв'):
        await handler(event, conn, [])
    event.data = f'{prefix}:{a}:{b}'
    with pytest.raises(BotError, match='застарів'):
        await handler(event, conn, [])
    event.bot.send_message.assert_not_awaited()
    assert queues.get_entry(conn, a)['position'] == 1
