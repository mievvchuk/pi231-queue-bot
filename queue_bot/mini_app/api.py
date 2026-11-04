"""Authenticated HTTP adapter; domain mutations contain no network awaits."""
import html
import logging
import json
from contextlib import suppress
from datetime import date

from aiohttp import web
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from queue_bot import config
from queue_bot.mini_app.auth import authenticate, AuthenticationError
from queue_bot.mini_app.browser_auth import browser_identity
from queue_bot.mini_app.payloads import names, queue_view, swap_views
from queue_bot.services import admins, lab_catalog, maintenance, queues, restrictions, schedule, swap_requests, users, working_days
from queue_bot.services.errors import BotError, PermissionDenied

CONTEXT = web.AppKey('mini_app_context', dict)
_RequestKey = getattr(web, 'RequestKey', web.AppKey)
REQ_CONTEXT = _RequestKey('mini_app_request_context', dict)
REQ_UID = _RequestKey('mini_app_actor', int)
REQ_USER = _RequestKey('mini_app_user', object)
REQ_ADMIN = _RequestKey('mini_app_admin_mode', bool)
REQ_BODY = _RequestKey('mini_app_json_body', dict)
log = logging.getLogger(__name__)


def integer(value, *, minimum=1, maximum=2**31-1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise BotError('Потрібне коректне ціле число.')
    return value


def path_id(request, name='sid'):
    try:
        return integer(int(request.match_info[name]))
    except (ValueError, KeyError):
        raise BotError('Некоректний номер запису.')


def session_for(request):
    session = queues.get_session(request[REQ_CONTEXT]['conn'], path_id(request))
    if not session or session['status'] == 'archived':
        raise BotError('Ця черга вже завершена або недоступна.')
    return session


def current_entry(request, data):
    conn, uid = request[REQ_CONTEXT]['conn'], request[REQ_UID]
    session = session_for(request)
    entry = queues._own_active_entry(conn, uid, session['id'])
    queues._check_revision(entry, integer(data.get('entry_id')), integer(data.get('revision'), minimum=0))
    return session, entry


async def body(request):
    if REQ_BODY in request:
        return request[REQ_BODY]
    try:
        if request.content_type != 'application/json':
            raise ValueError
        limit = 15 * 1024 * 1024 if request.path.endswith('/admin/manage/replacements') else 2 * 1024 * 1024 if request.path.endswith('/bugs') else 128 * 1024
        if request.content_length is not None and request.content_length > limit:
            raise BotError('Запит завеликий. Зменште розмір вкладень.')
        raw = await request.read()
        if len(raw) > limit:
            raise BotError('Запит завеликий. Зменште розмір вкладень.')
        data = json.loads(raw.decode('utf-8'))
        if not isinstance(data, dict):
            raise ValueError
        request[REQ_BODY] = data
        return data
    except (ValueError, TypeError, UnicodeDecodeError):
        raise BotError('Невірний формат запиту. Оновіть екран.')


def require_admin(request):
    admins.require_admin(request[REQ_CONTEXT]['conn'], request[REQ_UID])
    if not request[REQ_ADMIN]:
        raise PermissionDenied('Перейдіть у режим адміністратора.')


def revalidate(request, *, owner=False):
    """Recheck after network/recognition awaits, before any subsequent write."""
    ctx = request[REQ_CONTEXT]
    if not ctx.get('ready'):
        raise BotError('Бот перезапускається. Повторіть запит.')
    uid = request_identity(request, ctx)
    if uid != request[REQ_UID]:
        raise AuthenticationError('Повторно відкрийте мініапку з Telegram.')
    restrictions.require_available(ctx['conn'], uid)
    if not users.get_user(ctx['conn'], uid):
        raise PermissionDenied('Реєстрація вже недоступна. Оновіть екран.')
    if maintenance.is_maintenance_active(ctx['conn']) and not admins.is_admin(ctx['conn'], uid):
        raise PermissionDenied('Бот на технічних роботах.')
    if request[REQ_ADMIN]:
        require_admin(request)
    if owner:
        admins.require_owner(ctx['conn'], uid)


def request_identity(request, context):
    auth = request.headers.get('Authorization', '')
    if auth:
        if not auth.startswith('tma '):
            raise AuthenticationError('Повторіть вхід через Telegram.')
        return authenticate(auth[4:], context['token'])
    return browser_identity(request)


@web.middleware
async def guard(request, handler):
    if not request.path.startswith('/api/miniapp/'):
        return await handler(request)
    try:
        context = request.app[CONTEXT]
        if not context.get('ready'):
            return web.json_response({'error': 'Бот запускається. Спробуйте за кілька секунд.'}, status=503)
        request[REQ_CONTEXT] = context
        uid = request_identity(request, context)
        if request.method == 'POST':
            # Read/cache the body before final access checks. Handlers then reuse it
            # without yielding between current permissions and synchronous writes.
            await body(request)
            uid = request_identity(request, context)
            if not context.get('ready'):
                return web.json_response({'error': 'Бот перезапускається. Спробуйте ще раз.'}, status=503)
        request[REQ_UID] = uid
        conn = context['conn']
        restrictions.require_available(conn, uid)
        user = users.get_user(conn, uid)
        if not user and request.path not in ('/api/miniapp/register', '/api/miniapp/bootstrap'):
            return web.json_response({'error': 'Спочатку зареєструйтесь у боті: /start', 'code': 'registration_required'}, status=403)
        request[REQ_USER] = user
        is_admin = admins.is_admin(conn, uid)
        if maintenance.is_maintenance_active(conn) and not is_admin:
            return web.json_response({'error': 'Бот на технічних роботах. Спробуйте пізніше.'}, status=503)
        mode = request.query.get('mode', 'student')
        if mode not in ('student', 'admin'):
            raise BotError('Невідомий режим.')
        if mode == 'admin':
            admins.require_admin(conn, uid)
        request[REQ_ADMIN] = mode == 'admin'
        response = await handler(request)
    except AuthenticationError as error:
        response = web.json_response({'error': str(error)}, status=401)
    except PermissionDenied as error:
        response = web.json_response({'error': str(error)}, status=403)
    except BotError as error:
        status = 403 if 'заблоковано' in str(error).lower() or 'забанено' in str(error).lower() else 400
        response = web.json_response({'error': str(error)}, status=status)
    except web.HTTPException:
        raise
    except Exception as error:
        from queue_bot.services import diagnostics
        ctx = request.get(REQ_CONTEXT, {})
        route = request.match_info.route.resource
        record = await diagnostics.report(error, bot=ctx.get('bot'), conn=ctx.get('conn'),
            telegram_id=request.get(REQ_UID), context={'source':'mini_app', 'method':request.method,
                'route':getattr(route, 'canonical', 'unknown')})
        response = web.json_response({'error': diagnostics.explanation(error) + f' Код помилки: {record["code"]}.',
                                      'error_code':record['code']}, status=500)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


async def bootstrap(request):
    ctx, uid = request[REQ_CONTEXT], request[REQ_UID]
    sessions = queues.list_sessions(ctx['conn'], ('open', 'closed'))
    if request[REQ_USER] is None:
        return web.json_response({'me': {'name': None, 'is_registered': False, 'is_admin': False, 'is_owner': False}, 'queues': [], 'swaps': [], 'today': schedule.get_current_date().isoformat()})
    try:
        next_lesson = schedule.get_upcoming_lesson(ctx['conn'], schedule.load_schedule_data())
    except (ValueError, TypeError, KeyError, AttributeError):
        # A malformed imported timetable must not make personal queues unusable.
        log.warning('Upcoming lesson unavailable: invalid schedule format')
        next_lesson = None
    return web.json_response({'me': {'name': request[REQ_USER]['full_name'], 'is_registered': True, 'is_admin': admins.is_admin(ctx['conn'], uid), 'is_owner': admins.is_owner(ctx['conn'], uid)},
        'queues': [queue_view(ctx['conn'], ctx['subjects'], row, uid) for row in sessions],
        'swaps': swap_views(ctx['conn'], uid), 'outgoing_swaps': swap_views(ctx['conn'], uid, outgoing=True), 'today': schedule.get_current_date().isoformat(),
        'next_lesson': next_lesson})


async def detail(request):
    ctx = request[REQ_CONTEXT]
    return web.json_response(queue_view(ctx['conn'], ctx['subjects'], session_for(request), request[REQ_UID],
                                        detail=True, admin=request[REQ_ADMIN]))


async def notify_next(request, sid, old_position):
    from queue_bot.handlers.common import notify_next_students
    ctx = request[REQ_CONTEXT]
    with suppress(Exception):
        await notify_next_students(ctx['bot'], ctx['conn'], ctx['subjects'], sid, removed_position=old_position)


async def student_action(request):
    ctx, uid = request[REQ_CONTEXT], request[REQ_UID]
    conn, action = ctx['conn'], request.match_info['action']
    data = await body(request)
    session = session_for(request)
    sid = session['id']
    if action == 'attendance':
        from queue_bot.services import attendance
        token = data.get('token')
        if not isinstance(token, str):
            raise BotError('Оновіть чергу.')
        row = conn.execute('SELECT r.session_id FROM lesson_attendance a JOIN lesson_runs r ON r.id = a.run_id WHERE a.token = ?', (token,)).fetchone()
        if not row or row['session_id'] != sid:
            raise BotError('Ця кнопка застаріла.')
        attendance.respond(conn, uid, token, data.get('response'), minutes=data.get('minutes'))
        return web.json_response({'message': 'Відповідь збережено.'})
    if action == 'join':
        labs = lab_catalog.labs_for_session(session)
        numbers = lab_catalog.validate_numbers(session, data.get('labs')) if labs else None
        minutes = integer(data.get('minutes'), minimum=5) if labs else None
        position = integer(data['position'], maximum=1000) if data.get('position') is not None else None
        if position is not None and not queues.has_free_slots(conn, sid) and queues.are_first_10_occupied(conn, sid):
            raise BotError('Перші 10 місць зайняті. Запишіться в кінець черги.')
        place, _ = queues.join_queue(conn, uid, sid, position, lab_numbers=numbers, planned_minutes=minutes)
        return web.json_response({'message': f'Ви записалися. Ваше місце: {place}.', 'position': place, 'queue_id': sid})
    session, entry = current_entry(request, data)
    if action == 'finish-turn':
        queues.finish_own_entry(conn, uid, sid, passed=not bool(lab_catalog.labs_for_session(session)))
        await notify_next(request, sid, entry['position'])
        return web.json_response({'message': 'Підхід завершено. Решта робіт збережена, ви в кінці черги.'})
    if action == 'not-passed':
        if session['queue_type'] == 'consultation' or 'Курсова' in names(ctx['subjects'], session)[0]:
            raise BotError('Для консультації скористайтеся завершенням запису.')
        place = queues.finish_own_entry(conn, uid, sid, passed=False)
        await notify_next(request, sid, entry['position'])
        return web.json_response({'message': f'Вас переміщено в кінець. Місце: {place}.'})
    if action == 'wish':
        wish = data.get('wish')
        if wish not in ('higher', 'lower', None):
            raise BotError('Оберіть коректне побажання обміну.')
        rows = queues.get_entries(conn, sid)
        if wish == 'higher' and entry['position'] == 1:
            raise BotError('Ви вже на першому місці.')
        if wish == 'lower' and len(rows) > 1 and entry['position'] == len(rows):
            raise BotError('Ви вже останні в черзі.')
        new_wish = queues.set_swap_wish(conn, uid, sid, wish)
        if new_wish:
            from queue_bot.handlers.common import notify_swap_wishes
            with suppress(Exception):
                await notify_swap_wishes(ctx['bot'], conn, ctx['subjects'], sid, uid, new_wish)
        return web.json_response({'message': 'Побажання обміну оновлено.'})
    if action == 'edit':
        numbers = lab_catalog.validate_numbers(session, data.get('labs'))
        minutes = integer(data.get('minutes'), minimum=5)
        queues.set_own_lab_selection(conn, uid, sid, numbers, planned_minutes=minutes,
                                    expected_entry_id=entry['id'], expected_revision=entry['entry_revision'])
        return web.json_response({'message': 'Лабораторні й час оновлено. Місце збережено.'})
    if action == 'complete':
        pending = lab_catalog.read_numbers(entry['selected_labs']) if lab_catalog.labs_for_session(session) else None
        if pending:
            selected = pending if len(pending) == 1 else data.get('labs')
            remaining = queues.record_own_passed_lab_numbers(conn, uid, sid, selected,
                                  entry_id=entry['id'], expected_revision=entry['entry_revision'])
        else:
            queues.finish_own_entry(conn, uid, sid, passed=True)
            remaining = []
        if not remaining:
            await notify_next(request, sid, entry['position'])
        return web.json_response({'message': 'Здачу позначено. Продовжуйте або натисніть «Завершити підхід», щоб перейти з рештою робіт у кінець.' if remaining else 'Готово! Запис завершено.'})
    if action == 'leave':
        queues.leave_queue(conn, uid, sid)
        await notify_next(request, sid, entry['position'])
        return web.json_response({'message': 'Ви вийшли з черги.'})
    if action == 'swap':
        target_id = integer(data.get('target_entry_id'))
        target_entry = queues.get_entry(conn, target_id)
        if target_entry is None:
            raise BotError('Запис адресата вже змінився. Оновіть чергу.')
        queues._check_revision(target_entry, target_id, integer(data.get('target_revision'), minimum=0))
        invitation = swap_requests.create(conn, uid, entry['id'], target_id)
        target = users.get_user_by_id(conn, invitation['target_user_id'])
        subject, _ = names(ctx['subjects'], session)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='✅ Погодитися', callback_data=f"sw_yes:{invitation['token']}"),
            InlineKeyboardButton(text='❌ Відхилити', callback_data=f"sw_no:{invitation['token']}")]])
        try:
            await ctx['bot'].send_message(target['telegram_id'],
                f"🔁 <b>Пропозиція обміну</b>\n{html.escape(request[REQ_USER]['full_name'])} — {html.escape(subject)}.\n"
                f"Ваше місце: {invitation['target_position']}, запропоноване: {invitation['sender_position']}.\n⏳ На відповідь є 10 хв.", reply_markup=keyboard)
        except Exception:
            swap_requests.cancel_delivery(conn, invitation['token'])
            raise BotError('Не вдалося доставити запит. Спробуйте ще раз.')
        return web.json_response({'message': 'Запит надіслано. На відповідь є 10 хв.'})
    raise BotError('Невідома дія.')


async def respond_swap(request):
    data = await body(request)
    if type(data.get('accept')) is not bool:
        raise BotError('Оберіть відповідь на запит.')
    ctx = request[REQ_CONTEXT]
    invitation, positions = swap_requests.respond(ctx['conn'], request[REQ_UID], request.match_info['token'], accept=data['accept'])
    sender = users.get_user_by_id(ctx['conn'], invitation['sender_user_id'])
    with suppress(Exception):
        await ctx['bot'].send_message(sender['telegram_id'], '✅ На обмін погодилися. Місця оновлено.' if positions else '❌ Запит на обмін відхилено.')
    return web.json_response({'message': 'Місця оновлено.' if positions else 'Запит відхилено.'})


async def timetable(request):
    try:
        dt = date.fromisoformat(request.query.get('date', schedule.get_current_date().isoformat()))
    except ValueError:
        raise BotError('Оберіть правильну дату.')
    lessons, replacements = schedule.get_actual_schedule(request[REQ_CONTEXT]['conn'], schedule.load_schedule_data(), dt, config.STUDENT_GROUP)
    working_day = working_days.get_working_saturday(request[REQ_CONTEXT]['conn'], dt.isoformat())
    # Expose timetable fields only, not DB metadata (PG created_at is a datetime).
    public_replacements = [{key: item.get(key) for key in
        ('date', 'lesson_number', 'old_subject', 'new_subject', 'teacher', 'room')}
        for item in replacements]
    return web.json_response({'date': dt.isoformat(), 'day': schedule.DAY_NAMES_UK[schedule.DAY_KEYS[dt.weekday()]],
        'week_type': (working_day.get('week_type') if working_day else None) or schedule.get_week_type(dt),
        'source_day': schedule.DAY_NAMES_UK[working_day['replaces_day']] if working_day else None,
        'working_day_archived': bool(working_day and working_days.is_saturday_archived(dt.isoformat())),
        'lessons': lessons, 'slots': schedule.get_day_slots(lessons), 'replacements': public_replacements})


async def admin_queue(request):
    require_admin(request)
    data = await body(request)
    action = data.get('action')
    if action in ('start-lesson', 'stop-lesson'):
        from queue_bot.services import attendance
        conn, uid, sid = request[REQ_CONTEXT]['conn'], request[REQ_UID], path_id(request)
        if action == 'start-lesson':
            attendance.start(conn, uid, sid, minutes=data.get('minutes', 90))
        else:
            attendance.stop(conn, uid, sid)
        return web.json_response({'message': 'Здачу запущено. На відповіді є 10 хвилин.' if action == 'start-lesson' else 'Здачу зупинено. Пріоритет збережено.'})
    if action == 'add_students':
        names_list = data.get('names')
        if not isinstance(names_list, list) or not 1 <= len(names_list) <= 100:
            raise BotError('Вкажіть від 1 до 100 студентів.')
        if any(not isinstance(name, str) for name in names_list):
            raise BotError('Вкажіть коректні прізвище та ім’я.')
        clean = [users.validate_full_name(name) for name in names_list]
        if len(set(name.casefold() for name in clean)) != len(clean):
            raise BotError('Імена у списку не повинні повторюватися.')
        conn, uid = request[REQ_CONTEXT]['conn'], request[REQ_UID]
        queues.add_students_by_admin(conn, uid, path_id(request), clean)
        return web.json_response({'message': f'Додано студентів: {len(clean)}.'})
    if action in ('open', 'close'):
        raise BotError('Черга постійна: запис доступний завжди, відкривати чи закривати його не потрібно.')
    raise BotError('Невідома дія адміністратора.')


async def admin_entry(request):
    require_admin(request)
    data = await body(request)
    conn, uid = request[REQ_CONTEXT]['conn'], request[REQ_UID]
    eid = path_id(request, 'eid')
    entry = queues.get_entry(conn, eid)
    if not entry:
        raise BotError('Запис не знайдено.')
    queues._check_revision(entry, eid, integer(data.get('revision'), minimum=0))
    action = data.get('action')
    if action == 'attendance':
        from queue_bot.services import attendance
        attendance.correct(conn, uid, eid, data.get('response'), run_id=integer(data.get('run_id')))
    elif action == 'move':
        queues.move_entry(conn, uid, eid, integer(data.get('position')))
    elif action == 'rename':
        from .student_api import text
        if entry['user_id'] is None:
            raise BotError('Це вільне місце.')
        users.admin_update_full_name(conn, uid, entry['user_id'], text(data.get('name'), 'Прізвище та ім’я', maximum=100))
    elif action in ('complete', 'remove'):
        (queues.mark_done if action == 'complete' else queues.remove_entry)(conn, uid, eid)
        await notify_next(request, entry['session_id'], entry['position'])
    else:
        raise BotError('Невідома дія адміністратора.')
    return web.json_response({'message': 'Чергу оновлено.'})


async def admin_users(request):
    require_admin(request)
    restrictions.expire_due_restrictions(request[REQ_CONTEXT]['conn'])
    conn = request[REQ_CONTEXT]['conn']
    protected = admins.get_owner_ids(conn) | {row['telegram_id'] for row in admins.list_admins(conn)}
    rows = users.sort_users(conn.execute('SELECT id, telegram_id, full_name, is_blocked, banned_until FROM users').fetchall())
    return web.json_response({'users': [{**dict(row), 'protected': row['telegram_id'] in protected} for row in rows]})


async def admin_user(request):
    require_admin(request)
    data = await body(request)
    conn, uid = request[REQ_CONTEXT]['conn'], request[REQ_UID]
    user_id = path_id(request, 'user_id')
    if data.get('action') == 'ban':
        restrictions.set_restriction(conn, uid, user_id, days=integer(data.get('days'), maximum=36500))
    elif data.get('action') == 'unban':
        restrictions.clear_restriction(conn, uid, user_id)
    else:
        raise BotError('Невідома дія адміністратора.')
    return web.json_response({'message': 'Обмеження оновлено.'})


def setup(app):
    prefix = '/api/miniapp'
    from . import student_api, admin_api, avatars
    student_api.setup(app)
    admin_api.setup(app)
    avatars.setup(app)
    app.router.add_get(prefix+'/bootstrap', bootstrap)
    app.router.add_get(prefix+'/schedule', timetable)
    app.router.add_get(prefix+'/queues/{sid}', detail)
    app.router.add_post(prefix+'/queues/{sid}/{action}', student_action)
    app.router.add_post(prefix+'/swaps/{token}', respond_swap)
    app.router.add_post(prefix+'/admin/queues/{sid}', admin_queue)
    app.router.add_post(prefix+'/admin/entries/{eid}', admin_entry)
    app.router.add_get(prefix+'/admin/users', admin_users)
    app.router.add_post(prefix+'/admin/users/{user_id}', admin_user)
