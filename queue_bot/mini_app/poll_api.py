"""Poll HTTP projections and voting against the bot's shared vote records."""
import json
from datetime import datetime, timedelta

from aiohttp import web

from queue_bot import config
from queue_bot.services import polls
from queue_bot.services.errors import BotError
from queue_bot.storage.database import transaction
from .admin_api import boolean, checked, response, text


def poll_view(row):
    return {'id': row['id'], 'question': row['question'], 'options': json.loads(row['options_json']),
            'is_anonymous': bool(row['is_anonymous']), 'allows_multiple': bool(row['allows_multiple']),
            'status': row['status'], 'closes_at': str(row['closes_at']) if row['closes_at'] else None, 'target': row['target']}


def vote_reason(conn, row):
    if row['status'] != 'active': return 'Опитування завершено.'
    if row['closes_at']:
        closes = datetime.fromisoformat(str(row['closes_at']))
        if closes.tzinfo is None: closes = closes.replace(tzinfo=polls.UK_TZ)
        if datetime.now(polls.UK_TZ) >= closes: return 'Час голосування завершився.'
    if row['target'] == 'group' and row['is_anonymous'] and not polls.is_miniapp_poll(conn, row['id']):
        return 'Це анонімне опитування Telegram. Голосуйте у повідомленні групи; тут доступні результати.'
    return None


async def listing(request):
    from . import api
    return response({'items': [poll_view(row) for row in polls.list_polls(request[api.REQ_CONTEXT]['conn'], limit=100)]})


async def detail(request):
    from . import api
    conn, uid = request[api.REQ_CONTEXT]['conn'], request[api.REQ_UID]
    pid = api.path_id(request, 'pid')
    row = polls.get_poll(conn, pid)
    if not row: raise BotError('Опитування не знайдено.')
    result = polls.get_poll_results(conn, pid)
    result.pop('poll', None)
    # Public result names come from an intentionally public nonanonymous poll.
    # Never expose Telegram's fallback ID string to a student.
    for option in result['options']:
        option['voters'] = [] if row['is_anonymous'] else [name for name in option['voters'] if not name.startswith('ID:')]
    if row['target'] == 'group' and row['is_anonymous'] and row['allows_multiple'] and not polls.is_miniapp_poll(conn, pid):
        # Telegram's stored per-option counts cannot reveal how many distinct
        # people selected several options. Do not present a guessed denominator.
        result['total_voters'] = None
        for option in result['options']:
            option['percent'] = None
    mine = [r['option_index'] for r in conn.execute('SELECT option_index FROM poll_votes WHERE poll_id = ? AND voter_telegram_id = ? ORDER BY option_index', (pid, uid)).fetchall()]
    reason = vote_reason(conn, row)
    channel_note = None
    if row['target'] == 'private' and row['is_anonymous'] and not polls.is_miniapp_poll(conn, pid):
        channel_note = 'Після голосування в мініапці змінюйте або відкликайте голос тут: старий анонімний бюлетень Telegram більше не змінюватиме ваш вибір.'
    return response({'poll': poll_view(row), 'results': result, 'my_options': mine, 'can_vote': reason is None, 'can_vote_reason': reason, 'vote_channel_note': channel_note})


async def vote(request):
    from . import api
    data = await api.body(request)
    conn, uid = request[api.REQ_CONTEXT]['conn'], request[api.REQ_UID]
    pid = api.path_id(request, 'pid')
    row = polls.get_poll(conn, pid)
    if not row: raise BotError('Опитування не знайдено.')
    reason = vote_reason(conn, row)
    if reason: raise BotError(reason)
    choices = data.get('options')
    count = len(json.loads(row['options_json']))
    if not isinstance(choices, list) or len(choices) > count or any(type(v) is not int or not 0 <= v < count for v in choices) or len(set(choices)) != len(choices):
        raise BotError('Оберіть коректні варіанти.')
    if not row['allows_multiple'] and len(choices) > 1: raise BotError('Оберіть один варіант.')
    with transaction(conn):
        if row['target'] == 'private' and row['is_anonymous'] and not polls.is_miniapp_poll(conn, pid):
            conn.execute("INSERT INTO bot_state (key, value) VALUES (?, '1') ON CONFLICT (key) DO UPDATE SET value = excluded.value", (f'poll_web_vote:{pid}:{uid}',))
        conn.execute('DELETE FROM poll_votes WHERE poll_id = ? AND voter_telegram_id = ?', (pid, uid))
        for choice in choices:
            conn.execute('INSERT INTO poll_votes (poll_id, voter_telegram_id, voter_name, option_index) VALUES (?, ?, ?, ?)',
                         (pid, uid, '' if row['is_anonymous'] else request[api.REQ_USER]['full_name'], choice))
    return response({'message': 'Голос збережено.' if choices else 'Голос відкликано.'})


async def admin_polls(request):
    from . import api
    ctx, conn, uid = checked(request)
    if request.method == 'GET':
        return response({'items': [poll_view(row) for row in polls.list_polls(conn, limit=100)], 'group_chat_id': polls.get_group_chat_id(conn)})
    data = await api.body(request)
    ctx, conn, uid = checked(request)
    action = data.get('action')
    if action == 'group':
        chat_id = api.integer(data.get('chat_id'), minimum=-(2**53-1), maximum=-1)
        polls.set_group_chat_id(conn, chat_id)
    elif action == 'close':
        pid = api.integer(data.get('poll_id'))
        if not polls.get_poll(conn, pid): raise BotError('Опитування не знайдено.')
        await polls.close_poll_and_summarize(ctx['bot'], conn, pid)
    elif action == 'create':
        if not config.MINI_APP_URL: raise BotError('Мініапку ще не налаштовано.')
        question = text(data.get('question'), 300)
        options = data.get('options')
        if not isinstance(options, list) or not 2 <= len(options) <= 10: raise BotError('Потрібно від 2 до 10 варіантів.')
        options = [text(option, 100) for option in options]
        if len(set(options)) != len(options): raise BotError('Варіанти мають відрізнятися.')
        target = data.get('target')
        if target not in ('private', 'group'): raise BotError('Оберіть одержувачів.')
        if target == 'group' and not polls.get_group_chat_id(conn): raise BotError('Спочатку налаштуйте ID групи.')
        minutes = data.get('minutes')
        closes_at = (datetime.now(polls.UK_TZ) + timedelta(minutes=api.integer(minutes, maximum=525600))).isoformat() if minutes is not None else None
        pid = polls.create_poll(conn, uid, question, options, boolean(data.get('is_anonymous')), boolean(data.get('allows_multiple')), target, closes_at)
        sent, failed = await polls.publish_poll(ctx['bot'], conn, pid)
        return response({'message': 'Опитування створено.', 'poll_id': pid, 'sent': sent, 'failed': failed})
    else: raise BotError('Невідома дія.')
    return response({'message': 'Зміни збережено.'})


def setup(app):
    app.router.add_get('/api/miniapp/polls', listing)
    app.router.add_get('/api/miniapp/polls/{pid}', detail)
    app.router.add_post('/api/miniapp/polls/{pid}/vote', vote)
