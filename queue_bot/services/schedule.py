import html
import json
import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from queue_bot import config
from queue_bot.services.replacements import get_replacements

log = logging.getLogger(__name__)

UK_TZ = ZoneInfo(config.TIMEZONE)

PAIR_TIMES = {
    1: ('08:30', '09:50'),
    2: ('10:00', '11:20'),
    3: ('11:50', '13:10'),
    4: ('13:20', '14:40'),
    5: ('14:50', '16:10'),
}


def get_day_slots(lessons: list[dict]) -> list[dict]:
    """Display slots are separate from actual lessons; gaps never invent classes."""
    numbers = sorted(set(PAIR_TIMES) | {int(item['number']) for item in lessons})
    return [{'number': number, 'startTime': PAIR_TIMES.get(number, ('', ''))[0],
             'endTime': PAIR_TIMES.get(number, ('', ''))[1],
             'lessons': [item for item in lessons if int(item['number']) == number]}
            for number in numbers]

DAY_KEYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

DAY_NAMES_UK = {
    "monday": "Понеділок",
    "tuesday": "Вівторок",
    "wednesday": "Середа",
    "thursday": "Четвер",
    "friday": "П'ятниця",
    "saturday": "Субота",
    "sunday": "Неділя",
}


def load_schedule_data(path: str | None = None) -> dict:
    path = path or config.SCHEDULE_PATH
    if not os.path.exists(path):
        return {k: [] for k in DAY_KEYS}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"Error loading schedule from {path}: {e}")
        return {k: [] for k in DAY_KEYS}


def get_current_date() -> date:
    return datetime.now(UK_TZ).date()


def get_upcoming_lesson(conn, schedule_data, *, now=None, group_name=None):
    """Next real lesson group within seven days, including an ongoing pair."""
    now = (now or datetime.now(UK_TZ)).astimezone(UK_TZ)
    for offset in range(7):
        target = now.date() + timedelta(days=offset)
        if target.weekday() == 5:
            from queue_bot.services import working_days
            if (working_days.get_working_saturday(conn, target.isoformat()) and
                    working_days.is_saturday_archived(target.isoformat(), now=now)):
                continue
        lessons, _ = get_actual_schedule(conn, schedule_data, target, group_name or config.STUDENT_GROUP)
        for number in sorted({item['number'] for item in lessons}):
            start, end = PAIR_TIMES.get(number, ('', ''))
            if not start or not end:
                continue
            if target == now.date() and end <= now.strftime('%H:%M'):
                continue
            selected = [item for item in lessons if item['number'] == number and
                        str(item.get('subject', '')).strip().casefold() not in
                        ('', 'немає пари', 'нема пари', 'пари немає', 'скасовано', 'немає заняття')]
            if selected:
                return dict(date=target.isoformat(), number=number, startTime=start, endTime=end,
                            ongoing=target == now.date() and start <= now.strftime('%H:%M') < end,
                            lessons=selected)
    return None


def get_tomorrow_date() -> date:
    return get_current_date() + timedelta(days=1)


def get_week_type(dt: date) -> str:
    """Визначає чисельник чи знаменник за парністю номера тижня року (ISO)."""
    week_num = dt.isocalendar()[1]
    return "denominator" if week_num % 2 == 0 else "numerator"


def get_base_schedule_for_day(schedule_data: dict, day_key: str, w_type: str) -> list[dict]:
    day_content = schedule_data.get(day_key, [])

    if isinstance(day_content, dict):
        raw_list = day_content.get(w_type) or day_content.get("regular") or []
    elif isinstance(day_content, list):
        raw_list = day_content
    else:
        raw_list = []

    filtered = []
    for item in raw_list:
        wt = item.get("week_type", "both")
        if wt not in ("both", w_type):
            continue

        pair_num = int(item.get("pair") or item.get("number") or 0)
        start_time, end_time = PAIR_TIMES.get(pair_num, (item.get('startTime', ''), item.get('endTime', '')))
        teachers_list = item.get("teachers")

        if isinstance(teachers_list, list) and teachers_list:
            teacher_parts = []
            for t in teachers_list:
                name = t.get("name", "")
                room = t.get("room")
                if room:
                    teacher_parts.append(f"{name} ({room})")
                else:
                    teacher_parts.append(name)
            teachers_display = ", ".join(teacher_parts)
            room_display = ""
        else:
            teachers_display = item.get("teacher", "")
            room_display = item.get("room", "")

        filtered.append({
            "number": pair_num,
            "subject": item.get("subject", ""),
            "teacher": teachers_display,
            "teachers_raw": teachers_list if isinstance(teachers_list, list) else None,
            "room": room_display,
            "lessons": item.get("lessons", ""),
            "startTime": start_time,
            "endTime": end_time,
            "note": str(item.get("note") or "").strip(),  # free text from schedule.json, e.g. «Остання пара»
        })

    return filtered


def get_base_schedule(schedule_data: dict, dt: date) -> list[dict]:
    day_key = dt.strftime("%A").lower()
    w_type = get_week_type(dt)
    return get_base_schedule_for_day(schedule_data, day_key, w_type)


def get_actual_schedule(conn, schedule_data: dict, dt: date, group_name: str = "ПІ-231") -> tuple[list[dict], list[dict]]:
    date_str = dt.isoformat()
    replacements = get_replacements(conn, date=date_str, group_name=group_name)

    from queue_bot.services.working_days import get_working_saturday
    ws = get_working_saturday(conn, date_str) if conn else None

    if ws:
        effective_day_key = ws["replaces_day"]
        effective_w_type = ws.get("week_type") or get_week_type(dt)
        base = get_base_schedule_for_day(schedule_data, effective_day_key, effective_w_type)
        if ws.get("pairs_count"):
            kept = sorted({int(l.get("number", 0)) for l in base})[:int(ws["pairs_count"])]
            base = [l for l in base if int(l.get("number", 0)) in kept]
    else:
        base = get_base_schedule(schedule_data, dt)

    rep_map = {int(r["lesson_number"]): r for r in replacements}
    actual_lessons = []

    for lesson in base:
        num = int(lesson.get("number", 0))
        if num in rep_map:
            rep = rep_map[num]
            actual_lessons.append({
                "number": num,
                "subject": rep["new_subject"],
                "teacher": rep.get("teacher") or lesson.get("teacher"),
                "teachers_raw": None,
                "room": rep.get("room") or lesson.get("room"),
                "lessons": lesson.get("lessons", ""),
                "startTime": lesson.get("startTime", ""),
                "endTime": lesson.get("endTime", ""),
                "is_replacement": True,
                "note": rep.get("source_note") or "",
            })
        else:
            actual_lessons.append({**lesson, "is_replacement": False})

    base_numbers = {int(l.get("number", 0)) for l in base}
    for num, rep in sorted(rep_map.items()):
        if num not in base_numbers:
            actual_lessons.append({
                "number": num,
                "subject": rep["new_subject"],
                "teacher": rep.get("teacher"),
                "teachers_raw": None,
                "room": rep.get("room"),
                "lessons": "",
                "startTime": PAIR_TIMES.get(num, ('', ''))[0],
                "endTime": PAIR_TIMES.get(num, ('', ''))[1],
                "is_replacement": True,
                "note": rep.get("source_note") or "",
            })

    actual_lessons.sort(key=lambda x: x.get("number", 0))
    return actual_lessons, replacements


def format_schedule_for_date(conn, schedule_data: dict, dt: date, group_name: str = "ПІ-231") -> str:
    day_key = dt.strftime("%A").lower()
    day_uk = DAY_NAMES_UK.get(day_key, day_key.capitalize())
    w_type = get_week_type(dt)
    w_type_uk = "Знаменник" if w_type == "denominator" else "Чисельник"

    date_formatted = dt.strftime("%d.%m.%Y")
    from queue_bot.services.working_days import get_working_saturday, DAY_NAMES_UK as WS_DAY_NAMES
    date_str = dt.isoformat()
    ws = get_working_saturday(conn, date_str) if conn else None

    if ws:
        rep_day_uk = WS_DAY_NAMES.get(ws["replaces_day"], ws["replaces_day"].capitalize())
        ws_wtype_uk = "Знаменник" if (ws.get("week_type") or w_type) == "denominator" else "Чисельник"
        header = (
            f"📅 <b>{day_uk}, {date_formatted}</b> ({ws_wtype_uk})\n"
            f"🏢 <b>Робоча субота за розкладом: {rep_day_uk}</b>"
        )
        if ws.get("pairs_count"):
            header += f"\n🔢 Пар у цю суботу: <b>{ws['pairs_count']}</b>"
    else:
        header = f"📅 <b>{day_uk}, {date_formatted}</b> ({w_type_uk})"

    lessons, replacements = get_actual_schedule(conn, schedule_data, dt, group_name)
    from queue_bot.services.channel_import import confirmed_results, format_no_replacements
    confirmed = confirmed_results(conn, dt.isoformat())
    if confirmed and not replacements and not confirmed[0]['replacements']:
        header += '\n✅ ' + format_no_replacements(dt.isoformat())
    if not lessons:
        return f"{header}\n\n🏖 <i>Занять немає (вихідний або вільний день).</i>"

    lines = [header, ""]
    present_numbers = {int(item['number']) for item in lessons}
    missing = iter(sorted(set(range(1, max(present_numbers) + 1)) - present_numbers))
    next_missing = next(missing, None)
    for l in lessons:
        while next_missing is not None and next_missing < int(l['number']):
            lines.append(f"{next_missing} пара — немає заняття")
            lines.append("")
            next_missing = next(missing, None)
        time_part = f" ({l['startTime']}–{l['endTime']})" if l.get("startTime") and l.get("endTime") else ""
        badge = " ⚠️ <b>[ЗАМІНА]</b>" if l.get("is_replacement") else ""
        lines.append(f"⏰ <b>{l['number']} пара</b>{time_part}{badge}")
        lines.append(f"📚 <b>{html.escape(l.get('subject', ''))}</b>")

        if l.get("teachers_raw"):
            for t in l["teachers_raw"]:
                t_name = html.escape(t.get("name", ""))
                t_room = f" (ауд. {html.escape(t['room'])})" if t.get("room") else ""
                lines.append(f"👨‍🏫 {t_name}{t_room}")
        else:
            if l.get("teacher"):
                lines.append(f"👨‍🏫 {html.escape(l['teacher'])}")
            if l.get("room"):
                lines.append(f"🚪 Ауд. {html.escape(l['room'])}")
        if l.get("note"):
            lines.append(f"📝 <i>{html.escape(l['note'])}</i>")
        lines.append("")

    return "\n".join(lines).strip()


def format_week_schedule(conn, schedule_data: dict, ref_date: date, group_name: str = "ПІ-231") -> list[str]:
    """Повертає список відформатованих днів для поточного тижня (з Пн по Пт/Нд)."""
    monday = ref_date - timedelta(days=ref_date.weekday())
    result = []
    for i in range(7):
        day_date = monday + timedelta(days=i)
        result.append(format_schedule_for_date(conn, schedule_data, day_date, group_name))
    return result

