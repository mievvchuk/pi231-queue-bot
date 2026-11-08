from queue_bot.services import queues


def test_move_entry_beyond_queue_end_lands_on_requested_position(conn, make_user):
    for t, name in ((1, "Студент Перший"), (2, "Студент Другий"), (3, "Студент Третій")):
        make_user(t, name)
    s = queues.create_queue(conn, 1, 1, 1)
    for t in (1, 2, 3):
        queues.join_queue(conn, t, s)
    first = queues.get_entries(conn, s)[0]["id"]

    queues.move_entry(conn, 100, first, 6)

    assert queues.get_entry(conn, first)["position"] == 6
