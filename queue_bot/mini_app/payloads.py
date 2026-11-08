"""Public JSON views: Telegram IDs are included only for verified admin mode."""
from datetime import datetime, timezone

from queue_bot.services import lab_catalog, queues
from queue_bot.services.subjects import find_subject, find_teacher


def names(subjects, session):
    subject = find_subject(subjects, session['subject_id'])
    teacher = find_teacher(subject, session['teacher_id']) if subject else None
    return (subject['name'] if subject else 'Предмет', teacher['name'] if teacher else 'Викладач')


def entry_view(row, uid, admin=False):
    item = {k: row[k] for k in ('id', 'position', 'planned_minutes', 'lab_count', 'entry_revision', 'labs_passed')}
    item.update(name=row['full_name'] or 'Вільне місце', is_me=row['telegram_id'] == uid,
                vacant=row['user_id'] is None, labs=lab_catalog.read_numbers(row['selected_labs']),
                passed_labs=lab_catalog.read_numbers(row['passed_labs']) or [],
                ready_to_swap=bool(row['ready_to_swap']), swap_wish=row['swap_wish'],
                coursework=bool(row['coursework_priority']))
    if admin:
        item.update(user_id=row['user_id'], telegram_id=row['telegram_id'], blocked=bool(row['is_blocked']))
    return item


def queue_view(conn, subjects, session, uid, *, detail=False, admin=False):
    rows = queues.get_entries(conn, session['id'])
    subject, teacher = names(subjects, session)
    result = dict(id=session['id'], subject=subject, teacher=teacher, status='archived' if session['status'] == 'archived' else 'open',
                  type=session['queue_type'], title=session['lesson_title'], count=sum(r['user_id'] is not None for r in rows),
                  my_position=queues.get_user_position(conn, uid, session['id']))
    from queue_bot.services import attendance
    lesson = attendance.view(conn, session['id'], uid)
    if lesson:
        lesson['states'] = {str(k): v for k, v in lesson['states'].items()}
        if lesson['own']:
            own_state = lesson['own']
            lesson['own'] = {k: own_state[k] for k in ('state', 'token', 'deadline', 'turn_due')}
    result['lesson'] = lesson
    own = next((r for r in rows if r['telegram_id'] == uid), None)
    result['my_labs'] = lab_catalog.read_numbers(own['selected_labs']) if own else None
    result['my_planned_minutes'] = own['planned_minutes'] if own else None
    if detail:
        result['entries'] = [entry_view(row, uid, admin) for row in attendance.display_entries(rows, lesson)]
        result['labs'] = list(lab_catalog.labs_for_session(session))
        result['can_pick_position'] = session['status'] != 'archived'
        by_position = {row['position']: row for row in rows}
        last = max(by_position, default=0)
        limit = 20
        result['join_slots'] = [dict(number=number, state=(
            'yours' if number in by_position and by_position[number]['telegram_id'] == uid else
            'busy' if number in by_position and by_position[number]['user_id'] is not None else 'free'))
            for number in range(1, limit + 1)]
        result['append_position'] = last + 1
    return result


def swap_views(conn, uid, *, outgoing=False):
    # Expiration remains authoritative in respond(); the view exposes only live invitations.
    actor = 'u' if outgoing else 'target'
    rows = conn.execute(f'''SELECT r.token, r.session_id, r.status, r.expires_at, u.full_name AS sender_name,
        target.full_name AS target_name, a.position AS sender_position, b.position AS target_position
        FROM swap_requests r JOIN users target ON target.id = r.target_user_id
        JOIN users u ON u.id = r.sender_user_id
        JOIN queue_entries a ON a.id = r.sender_entry_id AND a.status = 'active'
            AND a.user_id = r.sender_user_id AND a.entry_revision = r.sender_revision
        JOIN queue_entries b ON b.id = r.target_entry_id AND b.status = 'active'
            AND b.user_id = r.target_user_id AND b.entry_revision = r.target_revision
        JOIN queue_sessions s ON s.id = r.session_id
        WHERE {actor}.telegram_id = ? AND s.status IN ('open', 'closed')
        AND (r.status = 'accepted' OR (r.status = 'pending' AND r.expires_at > ?))
        ORDER BY r.id DESC LIMIT 30''', (uid, datetime.now(timezone.utc).isoformat())).fetchall()
    return [dict(row) for row in rows]
