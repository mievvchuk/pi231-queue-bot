import pytest
from services import maintenance, queues, users
from services.errors import BotError


def test_maintenance_flag_and_time(conn):
    assert not maintenance.is_maintenance_active(conn)
    assert maintenance.get_maintenance_end_time(conn) == "20:15"

    maintenance.set_maintenance_mode(conn, True, "20:15")
    assert maintenance.is_maintenance_active(conn)
    assert maintenance.get_maintenance_end_time(conn) == "20:15"

    maintenance.set_maintenance_mode(conn, False)
    assert not maintenance.is_maintenance_active(conn)


def test_queue_actions_blocked_during_maintenance(conn):
    u = users.register_user(conn, 100, "Тестовий Студент")
    sess_id = queues.create_queue(conn, 100, 1, 1, "Пара")

    # Turn on maintenance
    maintenance.set_maintenance_mode(conn, True, "20:15")

    # Joining queue must fail
    with pytest.raises(BotError) as exc:
        queues.join_queue(conn, 100, sess_id)
    assert "технічні роботи" in str(exc.value).lower()

    # Creating queue must fail
    with pytest.raises(BotError) as exc:
        queues.create_queue(conn, 100, 1, 1, "Ще пара")
    assert "технічні роботи" in str(exc.value).lower()

    # Turn off maintenance
    maintenance.set_maintenance_mode(conn, False)

    # Now joining succeeds
    pos, total = queues.join_queue(conn, 100, sess_id)
    assert pos == 1
    assert total == 1
