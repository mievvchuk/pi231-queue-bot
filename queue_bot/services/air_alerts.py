"""Silent, persistent attendance gate for Khmelnytskyi Raion."""
import json
import logging
import time

import aiohttp

from queue_bot import config
from queue_bot.storage.database import transaction

log = logging.getLogger(__name__)
STATE_KEY = 'attendance_air_alert'
GRACE_SECONDS = 300
POLL_SECONDS = 15


def state(conn):
    row = conn.execute('SELECT value FROM bot_state WHERE key = ?', (STATE_KEY,)).fetchone()
    return json.loads(row['value']) if row else {}


def blocked(conn, now=None):
    if not config.ALERTS_API_TOKEN:
        return False
    now = int(time.time()) if now is None else now
    saved = state(conn)
    return (saved.get('active', True) or now < saved.get('resume_at', 0)
            or now - saved.get('checked_at', 0) > 60)


def relevant_alert(alert):
    """An oblast-wide alert covers the district; alerts in other districts do not."""
    if alert.get('alert_type') != 'air_raid' or alert.get('alert_level') == 'yellow':
        return False
    if alert.get('finished_at'):
        return False
    return ((alert.get('location_type') == 'oblast' and str(alert.get('location_uid')) == '3')
            or (alert.get('location_type') == 'raion'
                and alert.get('location_title') == 'Хмельницький район'))


def record(conn, active, now):
    old = state(conn)
    resume_at = old.get('resume_at', 0)
    # Unknown state is treated as a pause too. A fresh successful clear reading
    # after startup/failure starts a full grace period rather than assuming safety.
    if active:
        resume_at = 0
    elif old.get('active', True):
        resume_at = now + GRACE_SECONDS
    value = dict(old, active=active, resume_at=resume_at, checked_at=now,
                 frozen_at=old.get('frozen_at', now))
    conn.execute('''INSERT INTO bot_state(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value''', (STATE_KEY, json.dumps(value)))


async def refresh(conn, *, now=None):
    if not config.ALERTS_API_TOKEN:
        return
    now = int(time.time()) if now is None else now
    saved = state(conn)
    if now - saved.get('checked_at', 0) < POLL_SECONDS:
        return
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as client:
            async with client.get('https://api.alerts.in.ua/v1/alerts/active.json',
                    headers={'Authorization': f'Bearer {config.ALERTS_API_TOKEN}'}) as response:
                response.raise_for_status()
                data = await response.json()
        if not isinstance(data, dict) or not isinstance(data.get('alerts'), list):
            raise ValueError('Invalid alerts response')
        active = any(relevant_alert(alert) for alert in data['alerts'])
    except Exception:
        # Do not log request details, which may contain the authorization token.
        log.warning('Alerts API unavailable; attendance remains paused')
        active = True
    record(conn, active, now)


def freeze_timers(conn, *, now=None):
    """Keep response time while paused, including across process restarts.

    Lesson end times stay fixed: an ended class must never send a delayed check-in.
    """
    if not config.ALERTS_API_TOKEN:
        return
    now = int(time.time()) if now is None else now
    with transaction(conn):
        saved = state(conn)
        previous = saved.get('frozen_at', now)
        was_paused = saved.get('paused', False)
        paused = blocked(conn, now)
        if was_paused:
            for run in conn.execute('SELECT * FROM lesson_runs WHERE active = 1 AND ends_at > ?', (now,)).fetchall():
                delta = max(0, now - max(previous, run['started_at']))
                conn.execute('''UPDATE lesson_attendance SET
                    deadline = CASE WHEN deadline IS NULL THEN NULL ELSE deadline + ? END,
                    turn_due = CASE WHEN turn_due > 0 THEN turn_due + ? ELSE turn_due END
                    WHERE run_id = ?''', (delta, delta, run['id']))
                conn.execute('UPDATE lesson_runs SET started_at = started_at + ? WHERE id = ?', (delta, run['id']))
        saved.update(frozen_at=now, paused=paused)
        conn.execute('''INSERT INTO bot_state(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value''', (STATE_KEY, json.dumps(saved)))
