from unittest.mock import MagicMock

import pytest

from services import queues
from services.errors import BotError


def test_move_entry_beyond_queue_end_lands_on_requested_position(conn, make_user):
    for t in (1, 2, 3):
        make_user(t, f"Студент Номер{'абв'[t - 1]}")
    s = queues.create_queue(conn, 1, 1, 1)
    for t in (1, 2, 3):
        queues.join_queue(conn, t, s)
    first = queues.get_entries(conn, s)[0]["id"]

    queues.move_entry(conn, 100, first, 6)

    assert queues.get_entry(conn, first)["position"] == 6


def test_finish_session_keeps_queue_type(conn, make_user):
    make_user(1, "Студент Перший")
    s = queues.create_queue(conn, 1, 1, 1, queue_type="consultation")

    new_id = queues.finish_session(conn, 100, s, create_new=True)

    assert queues.get_session(conn, new_id)["queue_type"] == "consultation"


@pytest.mark.asyncio
async def test_swap_accept_rejects_when_sender_slot_taken_by_someone_else(conn, make_user):
    from handlers.student import swap_accept

    for t, name in ((1, "Студент Перший"), (2, "Студент Другий"), (3, "Студент Третій"), (4, "Студент Четвертий")):
        make_user(t, name)
    s = queues.create_queue(conn, 1, 1, 1)
    for t in (1, 2, 3):
        queues.join_queue(conn, t, s)
    entries = queues.get_entries(conn, s)
    sender, target = entries[0], entries[2]

    queues.leave_queue(conn, 1, s)            # відправник вийшов, місце №1 стало вільним
    queues.join_queue(conn, 4, s, position=1)  # інший студент зайняв той самий запис

    callback = MagicMock()
    callback.from_user.id = 3
    callback.data = f"sw_yes:{sender['id']}:{target['id']}:{sender['user_id']}"
    with pytest.raises(BotError):
        await swap_accept(callback, conn, [])
    assert queues.get_user_position(conn, 4, s) == 1
