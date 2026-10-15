import html
from datetime import datetime
from zoneinfo import ZoneInfo
import config

UK_TZ = ZoneInfo(config.TIMEZONE)


def get_today_key() -> str:
    return datetime.now(UK_TZ).strftime("%Y-%m-%d")


def get_replacements(conn, date: str | None = None, group_name: str = "ПІ-231") -> list[dict]:
    """Отримує заміни з бази даних: на конкретну дату або всі активні від сьогодні."""
    if date:
        rows = conn.execute(
            """SELECT * FROM replacements 
               WHERE date = ? AND group_name = ? 
               ORDER BY lesson_number""",
            (date, group_name),
        ).fetchall()
    else:
        today_str = get_today_key()
        rows = conn.execute(
            """SELECT * FROM replacements 
               WHERE group_name = ? AND date >= ? 
               ORDER BY date, lesson_number""",
            (group_name, today_str),
        ).fetchall()

    return [dict(r) for r in rows]


def save_replacement(conn, item: dict) -> bool:
    """Зберігає заміну в базу даних."""
    res = conn.execute(
        """INSERT INTO replacements 
           (date, group_name, lesson_number, old_subject, new_subject, teacher, room)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (date, group_name, lesson_number, new_subject) DO UPDATE
           SET old_subject = excluded.old_subject,
               teacher = excluded.teacher,
               room = excluded.room""",
        (
            item["date"],
            item.get("group_name") or item.get("group", "ПІ-231"),
            int(item["lesson_number"]),
            item.get("old_subject"),
            item["new_subject"].strip(),
            item.get("teacher"),
            item.get("room"),
        ),
    )
    return True


def clear_replacements(conn, group_name: str = "ПІ-231") -> int:
    """Видаляє всі заміни для вказаної групи."""
    rows = conn.execute("SELECT COUNT(*) FROM replacements WHERE group_name = ?", (group_name,)).fetchone()
    count = rows[0] if rows else 0
    conn.execute("DELETE FROM replacements WHERE group_name = ?", (group_name,))
    return count


def format_replacement(item: dict) -> str:
    """Форматує картку заміни для показу в повідомленнях."""
    old_s = html.escape(item["old_subject"]) if item.get("old_subject") else "<i>не визначено</i>"
    new_s = html.escape(item["new_subject"])
    teacher = html.escape(item["teacher"]) if item.get("teacher") else "<i>не вказано</i>"
    room = html.escape(item["room"]) if item.get("room") else "<i>не вказано</i>"
    grp = html.escape(item.get("group_name") or item.get("group", "ПІ-231"))

    return (
        f"📅 <b>Дата:</b> <code>{item['date']}</code>\n"
        f"👥 <b>Група:</b> {grp}\n"
        f"⏰ <b>Пара:</b> №{item['lesson_number']}\n\n"
        f"❌ <b>Було:</b> {old_s}\n"
        f"✅ <b>Стало:</b> <b>{new_s}</b>\n"
        f"👨‍🏫 <b>Викладач:</b> {teacher}\n"
        f"🚪 <b>Аудиторія:</b> {room}"
    )
