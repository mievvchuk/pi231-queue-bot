from unittest.mock import AsyncMock

import pytest

from queue_bot import config
from queue_bot.services import air_alerts as alerts, attendance, queues
from queue_bot.services.lesson_notifier import deliver


@pytest.mark.parametrize('threat', ['air_raid', 'chemical', 'nuclear', 'artillery_shelling', 'urban_fights'])
@pytest.mark.parametrize('level', ['yellow', 'red', None, 'future_level'])
def test_region_and_threat_filter(threat, level):
    base = dict(alert_type=threat, alert_level=level, location_type='raion',
                location_title='Хмельницький район')
    assert alerts.relevant_alert(base)
    assert not alerts.relevant_alert(dict(base, location_type='oblast', location_uid='3'))
    assert not alerts.relevant_alert(dict(base, location_title='Шепетівський район'))
    assert not alerts.relevant_alert(dict(base, location_title='Кам’янець-Подільський район'))
    assert not alerts.relevant_alert(dict(base, location_type='city', location_title='Хмельницький'))
    assert not alerts.relevant_alert(dict(base, finished_at='2026-11-17T06:00:00Z'))


def test_repeated_alert_and_stale_state(conn, monkeypatch):
    monkeypatch.setattr(config, 'ALERTS_API_TOKEN', 'test')
    assert alerts.blocked(conn, 1000)
    alerts.record(conn, True, 1000)
    alerts.record(conn, False, 1100)
    assert alerts.state(conn)['resume_at'] == 1400
    alerts.record(conn, True, 1300)
    alerts.record(conn, False, 1500)
    assert alerts.state(conn)['resume_at'] == 1800
    alerts.record(conn, False, 1790)
    assert alerts.blocked(conn, 1799)
    assert not alerts.blocked(conn, 1800)
    assert alerts.blocked(conn, 1900)


@pytest.mark.asyncio
async def test_pause_delivery_deadlines_and_expiry(conn, make_user, monkeypatch):
    make_user(1, 'Петренко Іван')
    sid = queues.create_session(conn, 100, 1, 1)
    queues.join_queue(conn, 1, sid, lab_numbers=[1], planned_minutes=15)
    attendance.start(conn, 100, sid, minutes=90, now=1000)
    run = attendance.active_run(conn, sid, now=1000)
    aid = attendance.members(conn, run['id'])[0]['id']
    conn.execute('UPDATE lesson_attendance SET deadline = 1600 WHERE id = ?', (aid,))
    monkeypatch.setattr(config, 'ALERTS_API_TOKEN', 'test')
    alerts.record(conn, True, 1100)
    alerts.freeze_timers(conn, now=1100)
    bot = AsyncMock()
    await deliver(bot, conn, [], now=1100)
    bot.send_message.assert_not_called()
    alerts.record(conn, False, 1500)
    alerts.freeze_timers(conn, now=1500)
    alerts.record(conn, False, 1800)
    alerts.freeze_timers(conn, now=1800)
    assert not alerts.blocked(conn, 1800)
    row = attendance.members(conn, run['id'])[0]
    assert row['deadline'] == 2300
    assert attendance.active_run(conn, sid, now=1800)['ends_at'] == 6400
    await deliver(bot, conn, [], now=1800)
    assert bot.send_message.call_count == 1
    # A class that ends during the pause must not deliver old check-ins.
    conn.execute('UPDATE lesson_messages SET sent = 0')
    await deliver(bot, conn, [], now=6500)
    assert bot.send_message.call_count == 1


@pytest.mark.asyncio
async def test_api_failure_pauses_and_retries_without_exposing_token(conn, monkeypatch, caplog):
    monkeypatch.setattr(config, 'ALERTS_API_TOKEN', 'secret-token')
    def fail(**kwargs):
        raise OSError('offline')
    monkeypatch.setattr(alerts.aiohttp, 'ClientSession', fail)
    await alerts.refresh(conn, now=1000)
    assert alerts.blocked(conn, 1000)
    assert alerts.state(conn)['checked_at'] == 1000
    await alerts.refresh(conn, now=1005)
    assert alerts.state(conn)['checked_at'] == 1000
    assert 'secret-token' not in caplog.text


def test_schedule_does_not_start_during_pause(conn, monkeypatch):
    from queue_bot.services.lesson_scheduler import sync_schedule
    monkeypatch.setattr(config, 'ALERTS_API_TOKEN', 'test')
    # No timetable load or run creation is allowed until the gate opens.
    sync_schedule(conn, [], now=1000, schedule_data={})
    assert conn.execute('SELECT COUNT(*) FROM lesson_runs').fetchone()[0] == 0
