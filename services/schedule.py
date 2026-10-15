import html
import json
import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config
from services.replacements import get_replacements

log = logging.getLogger(__name__)

UK_TZ = ZoneInfo(config.TIMEZONE)

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


def load_schedule_data(path: str = "schedule.json") -> dict:
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


def get_tomorrow_date() -> date:
    return get_current_date() + timedelta(days=1)


def get_week_type(dt: date) -> str:
    """Визначає чисельник чи знаменник за парністю номера тижня року (ISO)."""
    week_num = dt.isocalendar()[1]
    return "denominator" if week_num % 2 == 0 else "numerator"


def get_base_schedule(schedule_data: dict, dt: date) -> list[dict]:
    day_key = dt.strftime("%A").lower()
    day_content = schedule_data.get(day_key, [])
    if isinstance(day_content, list):
        return day_content
    w_type = get_week_type(dt)
    return day_content.get(w_type) or day_content.get("regular") or []


def get_actual_schedule(conn, schedule_data: dict, dt: date, group_name: str = "ПІ-231") -> tuple[list[dict], list[dict]]:
    date_str = dt.isoformat()
    replacements = get_replacements(conn, date=date_str, group_name=group_name)
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
                "room": rep.get("room") or lesson.get("room"),
                "startTime": lesson.get("startTime", ""),
                "endTime": lesson.get("endTime", ""),
                "is_replacement": True,
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
                "room": rep.get("room"),
                "startTime": "",
                "endTime": "",
                "is_replacement": True,
            })

    actual_lessons.sort(key=lambda x: x.get("number", 0))
    return actual_lessons, replacements


def format_schedule_for_date(conn, schedule_data: dict, dt: date, group_name: str = "ПІ-231") -> str:
    day_key = dt.strftime("%A").lower()
    day_uk = DAY_NAMES_UK.get(day_key, day_key.capitalize())
    w_type = get_week_type(dt)
    w_type_uk = "Знаменник" if w_type == "denominator" else "Чисельник"

    date_formatted = dt.strftime("%d.%m.%Y")
    header = f"📅 <b>{day_uk}, {date_formatted}</b> ({w_type_uk})"

    lessons, replacements = get_actual_schedule(conn, schedule_data, dt, group_name)
    if not lessons:
        return f"{header}\n\n🏖 <i>Занять немає (вихідний або вільний день).</i>"

    lines = [header, ""]
    for l in lessons:
        time_part = f" ({l['startTime']}–{l['endTime']})" if l.get("startTime") and l.get("endTime") else ""
        badge = " ⚠️ <b>[ЗАМІНА]</b>" if l.get("is_replacement") else ""
        lines.append(f"⏰ <b>{l['number']} пара</b>{time_part}{badge}")
        lines.append(f"📚 <b>{html.escape(l.get('subject', ''))}</b>")
        if l.get("teacher"):
            lines.append(f"👨‍🏫 {html.escape(l['teacher'])}")
        if l.get("room"):
            lines.append(f"🚪 Ауд. {html.escape(l['room'])}")
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
