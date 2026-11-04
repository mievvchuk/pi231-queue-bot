"""Remember a student's list separately from short-lived input forms."""
import json


def remember(conn, telegram_id, session_id, source, page=1, *, query=''):
    value = json.dumps({'source': source, 'page': max(1, int(page)), 'query': query})
    conn.execute('''INSERT INTO bot_state(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value''',
        (f'queue_navigation:{telegram_id}:{session_id}', value))


def return_to(conn, telegram_id, session_id):
    row = conn.execute('SELECT value FROM bot_state WHERE key = ?',
                       (f'queue_navigation:{telegram_id}:{session_id}',)).fetchone()
    return json.loads(row['value']) if row else {'source': 'all', 'page': 1}
