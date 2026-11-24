"""Administrative Mini App adapters. Services remain the source of truth."""
import base64
import binascii
import html
import json
import re
from datetime import date, datetime

from aiohttp import web

from queue_bot import config
from queue_bot.services import admins, bugs, maintenance, queues, recognition, replacements, restrictions, schedule, subjects, users, working_days
from queue_bot.services.errors import BotError
from queue_bot.storage.database import transaction


def public(value):
    """Convert database rows/timestamps recursively without leaking implicit fields."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, 'keys'):
        return {str(k): public(value[k]) for k in value.keys()}
    if isinstance(value, (list, tuple)):
        return [public(item) for item in value]
    return value


def text(value, maximum=2000, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise BotError('Введіть текст коректної довжини.')
    return value.strip()


def boolean(value):
    if type(value) is not bool:
        raise BotError('Потрібне логічне значення.')
    return value


def iso_date(value):
    try:
        return date.fromisoformat(text(value, 10, 10)).isoformat()
    except ValueError:
        raise BotError('Некоректна дата.')


def checked(request, owner=False):
    from . import api
    api.revalidate(request, owner=owner)
    api.require_admin(request)
    ctx, uid = request[api.REQ_CONTEXT], request[api.REQ_UID]
    restrictions.require_available(ctx['conn'], uid)
    if owner:
        admins.require_owner(ctx['conn'], uid)
    return ctx, ctx['conn'], uid


def response(data):
    return web.json_response(public(data))


async def broadcast(ctx, message):
    from queue_bot.services.updates import broadcast_message_to_users, get_active_user_ids
    success, errors = await broadcast_message_to_users(ctx['bot'], get_active_user_ids(ctx['conn']), message)
    return {'sent': success, 'failed': errors}


def replacement_item(item):
    from .api import integer
    if not isinstance(item, dict):
        raise BotError('Некоректний рядок заміни.')
    result = {'date': iso_date(item.get('date')), 'group_name': config.STUDENT_GROUP,
              'lesson_number': integer(item.get('lesson_number'), maximum=10),
              'new_subject': text(item.get('new_subject'), 200)}
    for key in ('old_subject', 'teacher', 'room', 'source_note'):
        result[key] = text(item[key], 500) if item.get(key) is not None and item[key] != '' else None
    return result


def validate_schedule(value):
    """Bound and validate both list and parity-specific schedule formats."""
    from .api import integer
    if not isinstance(value, dict) or not value or set(value) - set(schedule.DAY_KEYS):
        raise BotError('JSON має містити лише дні тижня monday–sunday.')
    if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > 65536:
        raise BotError('Розклад завеликий (максимум 64 КБ).')
    for content in value.values():
        if isinstance(content, dict):
            if set(content) - {'regular', 'numerator', 'denominator'}:
                raise BotError('Некоректні типи тижня.')
            lists = list(content.values())
        else:
            lists = [content]
        for lessons in lists:
            if not isinstance(lessons, list) or len(lessons) > 20:
                raise BotError('День має містити список до 20 пар.')
            for lesson in lessons:
                if not isinstance(lesson, dict): raise BotError('Некоректна пара.')
                integer(lesson.get('pair', lesson.get('number')), maximum=10)
                text(lesson.get('subject'), 200)
                if lesson.get('week_type', 'both') not in ('both', 'numerator', 'denominator'):
                    raise BotError('Некоректний тип тижня.')
                for key in ('teacher', 'room', 'lessons', 'startTime', 'endTime'):
                    if key in lesson: text(lesson[key], 200, 0)
                teachers = lesson.get('teachers', [])
                if not isinstance(teachers, list) or len(teachers) > 10: raise BotError('Некоректний список викладачів.')
                for teacher in teachers:
                    if not isinstance(teacher, dict): raise BotError('Некоректний викладач.')
                    text(teacher.get('name'), 100)
                    if teacher.get('room') is not None: text(teacher['room'], 100, 0)
    return value


async def manage(request):
    from . import api
    section = request.match_info['section']
    owner = section in ('owner', 'admins', 'bugs', 'proposals')
    ctx, conn, uid = checked(request, owner)
    catalog = ctx['subjects']
    if section == 'reports':
        return web.json_response({'error':'Розділ звітів і статистики прибрано.'}, status=410)
    if request.method == 'GET':
        if section == 'subjects':
            return response({'subjects': catalog})
        if section == 'history':
            sessions = []
            from .payloads import names
            pair_plans = schedule.read_queue_pair_plans(conn)
            for row in queues.list_sessions(conn, ('open',)):
                subject, teacher = names(catalog, row)
                sessions.append({'id': row['id'], 'subject_id': row['subject_id'], 'teacher_id': row['teacher_id'], 'subject': subject, 'teacher': teacher, 'status': row['status'],
                                 'date': str(row['created_at']), 'type': row['queue_type'],
                                 'pair_plan': schedule.queue_pair_plan(row, subject, pair_plans)})
            entries = []
            sid = None
            if 'sid' in request.query:
                try:
                    sid = api.integer(int(request.query['sid']))
                except ValueError:
                    raise BotError('Некоректна черга.')
                session = queues.get_session(conn, sid)
                if not session or session['status'] != 'open':
                    raise BotError('Активну чергу не знайдено.')
                entries = conn.execute('''SELECT e.id, e.user_id, e.position, e.status, e.created_at,
                    e.completed_at, e.lab_count, e.planned_minutes, e.labs_passed, e.selected_labs,
                    e.passed_labs, u.full_name FROM queue_entries e LEFT JOIN users u ON u.id = e.user_id
                    WHERE e.session_id = ? AND e.user_id IS NOT NULL ORDER BY e.id''', (sid,)).fetchall()
            return response({'sessions': sessions, 'entries': entries, 'session_id': sid})
        if section == 'group':
            return await api.admin_users(request)
        if section == 'replacements':
            if request.query.get('format') == 'schedule':
                return web.json_response(schedule.read_schedule(conn), headers={'Content-Disposition': 'attachment; filename="schedule.json"'})
            keys = ('date', 'lesson_number', 'old_subject', 'new_subject', 'teacher', 'room')
            project = lambda rows: [{k: row.get(k) for k in keys} for row in rows]
            from queue_bot.services.channel_import import confirmed_results, format_no_replacements
            confirmed = [dict(date=item['date'], no_replacements=not item['replacements'],
                              message=format_no_replacements(item['date']) if not item['replacements'] else '')
                         for item in confirmed_results(conn) if item['date'] >= replacements.get_today_key()]
            return response({'confirmed': confirmed, 'active': project(replacements.get_replacements(conn, group_name=config.STUDENT_GROUP))})
        if section == 'saturdays':
            active, archived = working_days.split_working_saturdays(conn)
            project = lambda rows: [{'date': r['date'], 'source_day': r['replaces_day'], 'week_type': r['week_type'],
                                     'status': r['status'], 'archived_at': r['archived_at'],
                                     'archive_at': working_days.saturday_archive_time(r['date']).isoformat()} for r in rows]
            return response({'items': project(active), 'archived': project(archived),
                             'server_time': working_days.now_in_kyiv().isoformat()})
        if section == 'owner':
            return response({'maintenance': maintenance.is_maintenance_active(conn), 'end_time': maintenance.get_maintenance_end_time(conn),
                             'admins': admins.list_admins(conn)})
        if section == 'admins':
            return response({'items': admins.list_admins(conn)})
        if section == 'bugs':
            keys = ('id', 'user_telegram_id', 'user_name', 'username', 'text', 'status', 'admin_reply', 'created_at')
            return response({'items': [{**{k: row[k] for k in keys}, 'has_photo': bool(row['photo_file_id'])} for row in bugs.list_bug_reports(conn, status=None, limit=100)]})
        if section == 'proposals':
            return response({'items': [{**r, 'kind': 'teacher' if r['type'] == 'add_teacher' else 'subject'} for r in subjects._load_requests(conn).values()]})
        raise BotError('Невідомий розділ.')

    data = await api.body(request)
    # Body has been consumed by the guard, but retain this final role check for
    # direct invocation and future body readers that may yield.
    ctx, conn, uid = checked(request, owner)
    action = data.get('action')
    if section == 'history' and action == 'delete':
        queues.finish_session(conn, uid, api.integer(data.get('session_id')))
        return response({'message': 'Чергу видалено з активного списку.'})
    if section == 'history' and action == 'finish':
        raise BotError('Щоб прибрати чергу з активного списку, відкрийте її картку й натисніть «Видалити чергу».')
    if section == 'group':
        if action == 'add':
            users.admin_create_placeholder(conn, uid, text(data.get('full_name'), 61))
        else:
            target = api.integer(data.get('user_id'))
            if action == 'rename': users.admin_update_full_name(conn, uid, target, text(data.get('name'), 61))
            elif action == 'delete': users.admin_delete_user(conn, uid, target)
            elif action == 'block': restrictions.set_restriction(conn, uid, target)
            elif action == 'ban': restrictions.set_restriction(conn, uid, target, days=api.integer(data.get('days'), maximum=36500))
            elif action == 'unban': restrictions.clear_restriction(conn, uid, target)
            else: raise BotError('Невідома дія.')
    elif section == 'subjects':
        path = ctx.get('subjects_path', config.SUBJECTS_PATH)
        if action in ('rename', 'remove_teacher', 'delete'): checked(request, True)
        if action == 'add': subjects.add_new_subject(conn, catalog, text(data.get('name'), 80, 2), text(data.get('teacher_name'), 60, 3), path=path)
        elif action == 'add_teacher': subjects.add_teacher_to_subject(conn, catalog, api.integer(data.get('subject_id')), text(data.get('teacher_name'), 60, 3), path=path)
        elif action == 'rename': subjects.rename_subject(conn, catalog, api.integer(data.get('subject_id')), text(data.get('name'), 80, 2), path=path)
        elif action == 'remove_teacher': subjects.remove_teacher_from_subject(conn, catalog, api.integer(data.get('subject_id')), api.integer(data.get('teacher_id')), path=path)
        elif action == 'delete': subjects.delete_subject(conn, catalog, api.integer(data.get('subject_id')), path=path)
        else: raise BotError('Невідома дія.')
        admins.log_action(conn, uid, 'miniapp_subjects', action)
    elif section == 'replacements':
        if action == 'import_schedule':
            parsed = validate_schedule(data.get('schedule'))
            schedule.replace_schedule_from_backup(conn, parsed)
            admins.log_action(conn, uid, 'upload_schedule', 'miniapp')
            return response({'message': 'Розклад оновлено.'})
        if action == 'recognize':
            images = data.get('images')
            if not isinstance(images, list) or not 1 <= len(images) <= 2:
                raise BotError('Надішліть одне або два фото.')
            decoded = []
            for value in images:
                if not isinstance(value, str) or len(value) > 4_000_000:
                    raise BotError('Фото завелике.')
                try:
                    image = base64.b64decode(value, validate=True)
                except (ValueError, binascii.Error):
                    raise BotError('Некоректне фото.')
                if not image or len(image) > 3_000_000:
                    raise BotError('Фото порожнє або завелике.')
                if not (image.startswith(b'\xff\xd8\xff') or image.startswith(b'\x89PNG\r\n\x1a\n') or image.startswith(b'RIFF') and image[8:12] == b'WEBP'):
                    raise BotError('Підтримуються JPEG, PNG та WebP.')
                decoded.append(image)
            draft = await recognition.recognize_replacements_from_images(decoded, group_name=config.STUDENT_GROUP, conn=conn)
            checked(request)
            return response({'draft': draft})
        if action == 'clear':
            raise BotError('Масове очищення замін вимкнено. Скористайтеся редагуванням результату в боті.')
        elif action == 'save':
            items = data.get('items')
            if not isinstance(items, list) or not 1 <= len(items) <= 50:
                raise BotError('Оберіть від 1 до 50 замін.')
            items = [replacement_item(item) for item in items]
            if len({item['date'] for item in items}) != 1:
                raise BotError('Зберігайте заміни однієї дати разом.')
            replacements.validate_publication_date(items[0]['date'])
            with transaction(conn):
                replacements.archive_previous_replacements(conn, items[0]['date'], config.STUDENT_GROUP)
                for item in items: replacements.save_replacement(conn, item, auto_archive_previous=False)
                admins.log_action(conn, uid, 'save_replacements', str(len(items)))
            delivery = await broadcast(ctx, replacements.format_replacements_broadcast(items, items[0]['date']))
            return response({'message': 'Заміни збережено.', **delivery})
        else: raise BotError('Невідома дія.')
    elif section == 'saturdays':
        day = iso_date(data.get('date'))
        if date.fromisoformat(day).weekday() != 5: raise BotError('Оберіть суботу.')
        if action in ('save', 'delete') and working_days.is_saturday_archived(day):
            raise BotError('Ця субота вже в архіві. Історичний запис доступний лише для перегляду.')
        if action == 'delete': working_days.remove_working_saturday(conn, day)
        elif action == 'save':
            source, week = data.get('source_day'), data.get('week_type')
            if source not in working_days.DAY_KEYS_ORDER or week not in (None, 'numerator', 'denominator'):
                raise BotError('Оберіть день і тип тижня.')
            working_days.set_working_saturday(conn, day, source, week)
        elif action == 'notify':
            row = working_days.get_working_saturday(conn, day)
            if not row: raise BotError('Суботу не налаштовано.')
            if working_days.is_saturday_archived(day): raise BotError('Ця робоча субота вже в архіві.')
            result = await broadcast(ctx, f"📢 Робоча субота {day}: {working_days.DAY_NAMES_UK[row['replaces_day']]}. Розклад доступний у боті.")
            return response({'message': 'Оголошення надіслано.', **result})
        else: raise BotError('Невідома дія.')
        admins.log_action(conn, uid, 'miniapp_saturday', f'{action} {day}')
    elif section == 'owner':
        if action == 'announce':
            message = html.escape(text(data.get('text'), 4000))
            admins.log_action(conn, uid, 'manual_broadcast', 'miniapp')
            return response({'message': 'Оголошення надіслано.', **await broadcast(ctx, message)})
        if action != 'maintenance': raise BotError('Невідома дія.')
        active, end = boolean(data.get('active')), text(data.get('end_time', '20:15'), 5, 5)
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', end): raise BotError('Час у форматі ГГ:ХХ.')
        maintenance.set_maintenance_mode(conn, active, end)
        admins.log_action(conn, uid, 'maintenance', str(active))
        result = await (maintenance.broadcast_maintenance_notice(ctx['bot'], conn, end) if active else maintenance.broadcast_maintenance_end(ctx['bot'], conn))
        return response({'message': 'Режим оновлено.', 'sent': result[0], 'failed': result[1]})
    elif section == 'admins':
        target = api.integer(data.get('telegram_id'), maximum=2**53-1)
        if action == 'add': admins.add_admin(conn, uid, target)
        elif action == 'remove':
            if target in admins.get_owner_ids(conn): raise BotError('Не можна видалити власника.')
            admins.remove_admin(conn, uid, target)
        else: raise BotError('Невідома дія.')
    elif section == 'bugs':
        rid = api.integer(data.get('report_id'))
        report = bugs.get_bug_report(conn, rid)
        if not report: raise BotError('Звіт не знайдено.')
        if action not in ('reply', 'resolve'): raise BotError('Невідома дія.')
        reply = text(data.get('text'), 2000) if action == 'reply' else None
        bugs.resolve_bug_report(conn, rid, reply)
        if reply:
            try:
                await ctx['bot'].send_message(report['user_telegram_id'], '💬 Відповідь адміністратора:\n' + html.escape(reply))
            except Exception:
                return response({'message': 'Відповідь збережено; доставити не вдалося.'})
    elif section == 'proposals':
        rid = text(data.get('id'), 40)
        item = subjects.get_subject_request(conn, rid)
        if not item or item['status'] != 'pending': raise BotError('Заявку вже оброблено або не знайдено.')
        if action not in ('approve', 'reject'): raise BotError('Невідома дія.')
        if action == 'approve':
            if item['type'] == 'add_teacher': subjects.add_teacher_to_subject(conn, catalog, item['subject_id'], item['teacher_name'], path=ctx.get('subjects_path', config.SUBJECTS_PATH))
            else: subjects.add_new_subject(conn, catalog, item['subject_name'], item['teacher_name'], path=ctx.get('subjects_path', config.SUBJECTS_PATH))
        subjects.update_subject_request_status(conn, rid, 'approved' if action == 'approve' else 'rejected')
        try:
            await ctx['bot'].send_message(item['user_telegram_id'], 'Вашу заявку схвалено.' if action == 'approve' else 'Вашу заявку відхилено.')
        except Exception: pass
    else: raise BotError('Невідома дія.')
    return response({'message': 'Зміни збережено.'})


def setup(app):
    app.router.add_get('/api/miniapp/admin/manage/{section}', manage)
    app.router.add_post('/api/miniapp/admin/manage/{section}', manage)

