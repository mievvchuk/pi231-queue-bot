"""Durable attendance messages and restart-safe lesson deadlines."""
import asyncio
import html
import logging

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from queue_bot.services import attendance
from queue_bot.services.subjects import find_subject, find_teacher
from queue_bot.storage.database import transaction

log = logging.getLogger(__name__)


def keyboard(row):
    token, state = row['token'], row['state']
    def button(text, action):
        return InlineKeyboardButton(text=text, callback_data=f'att:{token}:{action}')
    if state == 'serving':
        rows = [[button('Завершити підхід', 'finish')]]
    elif state == 'called':
        rows = [[button('Готовий здавати', 'ready')], [button('Пропустити мене', 'skip')]]
    else:
        rows = [[button('Я вже на місці', 'present')],
                [button('Запізнююсь', 'late')], [button('На цій парі не буду', 'absent')]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def late_keyboard(token):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f'{n} хв', callback_data=f'att:{token}:late{n}') for n in (5, 10, 15)],
        [InlineKeyboardButton(text='30 хв', callback_data=f'att:{token}:late30'),
         InlineKeyboardButton(text='Інший час', callback_data=f'att:{token}:custom')]])


def message_content(row, kind, title):
    if kind == 'warning':
        return f'{title}\nПеред вами залишилося двоє людей. Готуйтеся до здачі. Відповідати на це попередження не потрібно.'
    if kind == 'call':
        return f'{title}\nВаша черга. Чи готові зараз здавати? На відповідь є 10 хвилин. Без відповіді вас тимчасово пропустять зі збереженням пріоритету.'
    if kind == 'presence':
        return f'{title}\nНа цій парі розпочато здачу. Ви вже на місці? На відповідь є 10 хвилин. Якщо не відповісте, готові студенти проходитимуть перед вами. Ваш пріоритет збережеться.'
    if kind == 'reminder':
        return f'{title}\nВказаний час запізнення минув. Ви вже прийшли? Підтвердьте присутність або уточніть час. На відповідь є 10 хвилин; поки інші можуть здавати.'
    if kind == 'skipped':
        return f'{title}\nЗа 10 хвилин відповіді не було. Вас тимчасово пропущено. Місце збережене; коли прийдете, підтвердьте присутність.'
    return f"{title}\n{attendance.LABELS[row['state']]}. Пріоритет збережено. Того, хто вже здає або отримав виклик, не перериваємо."


async def deliver(bot, conn, subjects, *, now=None):
    """Claim one message at a time; stale revisions are never delivered."""
    simulated = now is not None
    now = attendance.now_seconds() if now is None else now
    ids = conn.execute('SELECT id FROM lesson_messages WHERE sent = 0 AND retry_at <= ? ORDER BY id LIMIT 50', (now,)).fetchall()
    for item in ids:
        with transaction(conn):
            event = conn.execute('SELECT m.*, r.session_id FROM lesson_messages m JOIN lesson_attendance a ON a.id = m.attendance_id JOIN lesson_runs r ON r.id = a.run_id WHERE m.id = ?', (item['id'],)).fetchone()
            if not event:
                continue
            attendance._lock(conn, event['session_id'])
            event = conn.execute('SELECT * FROM lesson_messages WHERE id = ? AND sent = 0 AND retry_at <= ?', (item['id'], now)).fetchone()
            if not event:
                continue
            row = conn.execute('''SELECT a.*, u.telegram_id, r.session_id FROM lesson_attendance a
                JOIN lesson_runs r ON r.id = a.run_id AND r.active = 1
                JOIN queue_sessions s ON s.id = r.session_id AND s.status IN ('open', 'closed')
                JOIN queue_entries e ON e.id = a.entry_id AND e.user_id = a.user_id AND e.status = 'active'
                JOIN users u ON u.id = a.user_id WHERE a.id = ? AND r.ends_at > ?''',
                (event['attendance_id'], now)).fetchone()
            if not row or row['revision'] != event['revision']:
                conn.execute('UPDATE lesson_messages SET sent = 1 WHERE id = ?', (event['id'],))
                continue
            conn.execute('UPDATE lesson_messages SET retry_at = ?, attempts = attempts + 1 WHERE id = ?', (now + 60, event['id']))
        session = attendance.queues.get_session(conn, row['session_id'])
        subject = find_subject(subjects, session['subject_id'])
        teacher = find_teacher(subject, session['teacher_id']) if subject else None
        title = html.escape((subject['name'] if subject else 'Черга') + ' — ' + (teacher['name'] if teacher else 'Викладач'))
        try:
            if not row['telegram_id'] or row['telegram_id'] < 0:
                raise ValueError('У студента ще немає Telegram-профілю')
            await bot.send_message(row['telegram_id'], message_content(row, event['kind'], title),
                                   reply_markup=None if event['kind'] == 'warning' else keyboard(row))
        except TelegramRetryAfter as exc:
            conn.execute('UPDATE lesson_messages SET retry_at = ? WHERE id = ?', (now + int(exc.retry_after) + 1, event['id']))
            continue
        except (TelegramForbiddenError, ValueError):
            log.warning('Attendance message unavailable for entry %s', row['entry_id'])
            with transaction(conn):
                attendance._lock(conn, row['session_id'])
                conn.execute('UPDATE lesson_messages SET sent = 1 WHERE id = ?', (event['id'],))
                # No successful delivery: do not reserve a call indefinitely.
                conn.execute("UPDATE lesson_attendance SET deadline = ? WHERE id = ? AND revision = ? AND state IN ('pending', 'called')", (now, row['id'], row['revision']))
            continue
        except Exception:
            log.exception('Attendance delivery failed for entry %s; retrying', row['entry_id'])
            continue
        with transaction(conn):
            attendance._lock(conn, row['session_id'])
            conn.execute('UPDATE lesson_messages SET sent = 1 WHERE id = ?', (event['id'],))
            if event['kind'] in ('presence', 'reminder', 'call'):
                conn.execute('UPDATE lesson_attendance SET deadline = ? WHERE id = ? AND revision = ? AND deadline IS NULL',
                             ((now if simulated else attendance.now_seconds()) + attendance.RESPONSE_SECONDS, row['id'], row['revision']))


async def watch_lessons(bot, conn, subjects, interval=5):
    while True:
        try:
            from queue_bot.services.lesson_scheduler import sync_schedule
            sync_schedule(conn, subjects)
        except Exception:
            log.exception('Could not read the effective timetable; retrying')
        try:
            attendance.tick(conn)
            await deliver(bot, conn, subjects)
        except Exception:
            log.exception('Lesson watcher failed; retrying')
        await asyncio.sleep(interval)
