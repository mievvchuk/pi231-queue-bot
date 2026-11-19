import html
import json
import logging
import os
from collections import Counter
from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from queue_bot import config
from queue_bot.services.replacements import get_replacements
from queue_bot.storage.database import transaction

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


WEEK_NAMES_UK = {"numerator": "Чисельник", "denominator": "Знаменник"}
EDIT_WEEK_NAMES_UK = {**WEEK_NAMES_UK, "both": "Обидва тижні"}
_SCHEDULE_IMPORT_KEY = "base_schedule_imported"


def _insert_lesson(conn, day_key, week_type, position, lesson):
    cur = conn.execute(
        """INSERT INTO schedule_lessons
           (day_key, week_type, pair, position, subject, teacher, room, lessons, start_time, end_time, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            day_key, week_type, lesson["number"], position, lesson["subject"],
            "" if lesson.get("teachers_raw") else lesson.get("teacher") or "",
            lesson.get("room") or "",
            lesson.get("lessons") or "", lesson.get("startTime") or "", lesson.get("endTime") or "",
            lesson.get("note") or "",
        ),
    )
    for index, teacher in enumerate(lesson.get("teachers_raw") or []):
        conn.execute(
            "INSERT INTO schedule_teachers (lesson_id, position, name, room) VALUES (?, ?, ?, ?)",
            (cur.lastrowid, index, teacher.get("name") or "", teacher.get("room")),
        )


def import_legacy_schedule(conn, path: str) -> None:
    """Одноразово переносить чинний файл у базу разом із позначкою про імпорт."""
    with transaction(conn):
        claimed = conn.execute(
            "INSERT INTO bot_state (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING RETURNING key",
            (_SCHEDULE_IMPORT_KEY, "1"),
        ).fetchone()
        if claimed is None:
            return

        source = Path(path)
        data = json.loads(source.read_text(encoding="utf-8-sig")) if source.exists() else {}
        _import_lessons(conn, data)


def _import_lessons(conn, data):
    for day_key in DAY_KEYS:
        for week_type in WEEK_NAMES_UK:
            for position, lesson in enumerate(get_base_schedule_for_day(data, day_key, week_type)):
                if lesson["number"] > 0 and (lesson["subject"] or "").strip():
                    _insert_lesson(conn, day_key, week_type, position, lesson)


def read_schedule(conn) -> dict:
    if conn is None:
        return load_schedule_data()
    if conn.execute("SELECT 1 FROM bot_state WHERE key = ?", (_SCHEDULE_IMPORT_KEY,)).fetchone() is None:
        import_legacy_schedule(conn, config.SCHEDULE_PATH)

    data = {day: {week: [] for week in WEEK_NAMES_UK} for day in DAY_KEYS}
    lessons = {}
    rows = conn.execute(
        """SELECT l.*, t.position AS teacher_position, t.name AS teacher_name, t.room AS teacher_room
           FROM schedule_lessons l LEFT JOIN schedule_teachers t ON t.lesson_id = l.id
           ORDER BY l.pair, l.position, t.position"""
    )
    for row in rows:
        lesson_id = row["id"]
        if lesson_id not in lessons:
            lessons[lesson_id] = {
                "pair": row["pair"], "subject": row["subject"], "teacher": row["teacher"],
                "room": row["room"], "lessons": row["lessons"], "startTime": row["start_time"],
                "endTime": row["end_time"], "note": row["note"], "teachers": [],
            }
            data[row["day_key"]][row["week_type"]].append(lessons[lesson_id])
        if row["teacher_position"] is not None:
            lessons[lesson_id]["teachers"].append({"name": row["teacher_name"], "room": row["teacher_room"]})
    return data


def set_schedule_pair(conn, day_key: str, week_type: str, number: int, lesson: dict | None) -> None:
    if day_key not in DAY_KEYS or week_type not in EDIT_WEEK_NAMES_UK or number not in PAIR_TIMES:
        raise ValueError("Невірний день, тип тижня або номер пари.")

    import_legacy_schedule(conn, config.SCHEDULE_PATH)
    with transaction(conn):
        # PostgreSQL тримає блокування цього рядка до кінця транзакції, навіть коли пара порожня.
        conn.execute("UPDATE bot_state SET value = value WHERE key = ?", (_SCHEDULE_IMPORT_KEY,))
        weeks = WEEK_NAMES_UK if week_type == "both" else (week_type,)
        for week in weeks:
            params = (day_key, week, number)
            current = conn.execute(
                """SELECT lessons, start_time, end_time, note FROM schedule_lessons
                   WHERE day_key = ? AND week_type = ? AND pair = ?
                   ORDER BY position LIMIT 1""",
                params,
            ).fetchone()
            conn.execute("DELETE FROM schedule_lessons WHERE day_key = ? AND week_type = ? AND pair = ?", params)
            if lesson is not None:
                start, end = PAIR_TIMES[number]
                _insert_lesson(conn, day_key, week, 0, {
                    **lesson,
                    "number": number,
                    "lessons": current["lessons"] if current and current["lessons"] else f"{number * 2 - 1}-{number * 2}",
                    "startTime": current["start_time"] if current else start,
                    "endTime": current["end_time"] if current else end,
                    "note": lesson.get("note", current["note"] if current else ""),
                })


def replace_schedule_from_backup(conn, data: dict) -> None:
    """Restore a legacy backup atomically into the same database used by the editor."""
    import_legacy_schedule(conn, config.SCHEDULE_PATH)
    with transaction(conn):
        conn.execute("UPDATE bot_state SET value = value WHERE key = ?", (_SCHEDULE_IMPORT_KEY,))
        conn.execute("DELETE FROM schedule_lessons")
        _import_lessons(conn, data)


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
            "note": str(item.get("note") or "").strip(),
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
    if not lessons and dt.weekday() >= 5 and not ws:
        return f"{header}\n\n🏖 <i>Занять немає (вихідний або вільний день).</i>"
    return f"{header}\n\n{format_lesson_slots(lessons)}"


def _lesson_details(lesson: dict) -> tuple:
    people = lesson["teachers_raw"] or [{"name": lesson["teacher"], "room": lesson["room"]}]
    teachers = tuple(sorted((person.get("name") or "", person.get("room") or "") for person in people))
    return lesson["subject"], teachers, lesson["startTime"], lesson["endTime"], lesson["note"]


def get_base_week_slots(data: dict, day: str) -> dict[int, dict[str, list[dict]]]:
    """Group each pair by week only if its lessons differ."""
    weeks = {
        week: {slot["number"]: slot["lessons"] for slot in get_day_slots(get_base_schedule_for_day(data, day, week))}
        for week in WEEK_NAMES_UK
    }
    slots = {}
    for pair in sorted(set(weeks["numerator"]) | set(weeks["denominator"])):
        numerator = weeks["numerator"].get(pair, [])
        denominator = weeks["denominator"].get(pair, [])
        same = Counter(map(_lesson_details, numerator)) == Counter(map(_lesson_details, denominator))
        slots[pair] = ({"both": numerator} if same else
                       {"numerator": numerator, "denominator": denominator})
    return slots


def format_base_week_slots(slots: dict[int, dict[str, list[dict]]]) -> str:
    def summary(lessons, *, details=False):
        labels = []
        for lesson in lessons:
            label = lesson["subject"]
            if details:
                extra = " · ".join(value for value in (lesson["teacher"], lesson["room"], lesson["note"]) if value)
                if extra:
                    label += f" · {extra[:139]}…" if len(extra) > 140 else f" · {extra}"
            labels.append(html.escape(label))
        return " / ".join(labels) if labels else "немає заняття"

    lines = []
    for pair, variants in slots.items():
        if "both" in variants:
            lines.append(f"{pair} пара — {summary(variants['both'])}")
        else:
            same_subjects = summary(variants["numerator"]) == summary(variants["denominator"])
            lines.append(f"<b>{pair} пара</b>")
            for week, lessons in variants.items():
                lines.append(f"{WEEK_NAMES_UK[week]} — {summary(lessons, details=same_subjects)}")
    return "\n".join(lines)


def format_lesson_slots(lessons: list[dict]) -> str:
    blocks = []
    for slot in get_day_slots(lessons):
        if slot["lessons"]:
            blocks.extend(_format_lesson(lesson) for lesson in slot["lessons"])
        else:
            blocks.append(f"{slot['number']} пара — немає заняття")
    return "\n\n".join(blocks)


def _format_lesson(l: dict) -> str:
    lines = []
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

    return "\n".join(lines).strip()


def format_week_schedule(conn, schedule_data: dict, ref_date: date, group_name: str = "ПІ-231") -> list[str]:
    """Повертає список відформатованих днів для поточного тижня (з Пн по Пт/Нд)."""
    monday = ref_date - timedelta(days=ref_date.weekday())
    result = []
    for i in range(7):
        day_date = monday + timedelta(days=i)
        result.append(format_schedule_for_date(conn, schedule_data, day_date, group_name))
    return result

