from datetime import date, datetime
from zoneinfo import ZoneInfo

from queue_bot.services import schedule, working_days


def test_saturday_moves_to_archive_at_1330_without_losing_history(conn, monkeypatch):
    working_days.set_working_saturday(conn, '2026-10-31', 'monday', 'denominator')
    working_days.set_working_saturday(conn, '2026-11-07', 'tuesday', 'numerator')
    tz = ZoneInfo('Europe/Kyiv')
    monkeypatch.setattr(working_days, 'now_in_kyiv', lambda: datetime(2026, 10, 31, 13, 29, 59, tzinfo=tz))
    active, archived = working_days.split_working_saturdays(conn)
    assert len(active) == 2 and archived == []
    assert working_days.get_next_saturday() == date(2026, 10, 31)
    monkeypatch.setattr(working_days, 'now_in_kyiv', lambda: datetime(2026, 10, 31, 13, 30, tzinfo=tz))
    active, archived = working_days.split_working_saturdays(conn)
    assert [r['date'] for r in active] == ['2026-11-07']
    assert [r['date'] for r in archived] == ['2026-10-31']
    assert working_days.get_next_saturday() == date(2026, 11, 7)
    assert working_days.get_working_saturday(conn, '2026-10-31')['replaces_day'] == 'monday'
    lessons, _ = schedule.get_actual_schedule(conn, {'monday': [{'pair': 1, 'subject': 'Історичний предмет'}]}, date(2026, 10, 31))
    assert lessons[0]['subject'] == 'Історичний предмет'
    assert working_days.get_working_saturday(conn, '2026-11-14') is None


def test_archive_survives_restart_and_uses_kyiv_clock(conn, db_path):
    from queue_bot.storage.database import connect
    working_days.set_working_saturday(conn, '2026-10-31', 'monday')
    # 11:30 UTC is 13:30 Kyiv after the October clock change.
    now = datetime.fromisoformat('2026-10-31T11:30:00+00:00')
    reopened = connect(db_path)
    try:
        active, archived = working_days.split_working_saturdays(reopened, now=now)
        assert active == [] and archived[0]['date'] == '2026-10-31'
    finally:
        reopened.close()

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

@pytest.mark.asyncio
@pytest.mark.parametrize('handler,data', [
    ('admin_set_working_saturday_day','ws_set:2026-10-31:tuesday'),
    ('admin_set_parity_working_saturday','ws_setpar:2026-10-31:numerator'),
    ('admin_cancel_working_saturday','ws_cancel:2026-10-31'),
])
async def test_stale_telegram_controls_cannot_change_archived_history(conn, monkeypatch, handler, data):
    from queue_bot.handlers.admin import schedule as handlers
    working_days.set_working_saturday(conn,'2026-10-31','monday','denominator')
    monkeypatch.setattr(working_days,'now_in_kyiv',lambda:datetime(2026,10,31,13,30,tzinfo=working_days.ARCHIVE_TZ))
    callback=SimpleNamespace(data=data,from_user=SimpleNamespace(id=100),answer=AsyncMock(),message=AsyncMock())
    await getattr(handlers,handler)(callback,conn)
    row=working_days.get_working_saturday(conn,'2026-10-31')
    assert row is not None and row['replaces_day']=='monday' and row['week_type']=='denominator'
    assert 'архів' in callback.answer.await_args.args[0].lower()


def test_pairs_count_keeps_first_pairs_and_survives_day_change(conn):
    data = {'monday': [{'pair': n, 'subject': f'P{n}'} for n in (2, 3, 4, 5)]}
    working_days.set_working_saturday(conn, '2026-11-14', 'monday')
    working_days.set_working_saturday_pairs(conn, '2026-11-14', 2)
    working_days.set_working_saturday(conn, '2026-11-14', 'monday', 'numerator')
    lessons, _ = schedule.get_actual_schedule(conn, data, date(2026, 11, 14))
    assert [l['number'] for l in lessons] == [2, 3]
    working_days.set_working_saturday_pairs(conn, '2026-11-14', None)
    lessons, _ = schedule.get_actual_schedule(conn, data, date(2026, 11, 14))
    assert len(lessons) == 4
