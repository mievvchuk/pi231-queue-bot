import html
from datetime import datetime
from zoneinfo import ZoneInfo
from queue_bot import config
UK_TZ = ZoneInfo(config.TIMEZONE)


def get_today_key() -> str:
    return datetime.now(UK_TZ).strftime("%Y-%m-%d")


def get_replacements(conn, date: str | None = None, group_name: str = "ПІ-231", include_archived: bool = False) -> list[dict]:
    """Отримує заміни з бази даних: на конкретну дату або всі активні від сьогодні."""
    arch_sql = "" if include_archived else "AND (is_archived = FALSE OR is_archived IS NULL)"
    if date:
        rows = conn.execute(
            f"""SELECT * FROM replacements 
               WHERE date = ? AND group_name = ? {arch_sql}
               ORDER BY lesson_number""",
            (date, group_name),
        ).fetchall()
    else:
        today_str = get_today_key()
        rows = conn.execute(
            f"""SELECT * FROM replacements 
               WHERE group_name = ? AND date >= ? {arch_sql}
               ORDER BY date, lesson_number""",
            (group_name, today_str),
        ).fetchall()

    return [dict(r) for r in rows]


def get_archived_replacements(conn, group_name: str = "ПІ-231", limit: int = 30) -> list[dict]:
    """Повертає заархівовані заміни для групи (останні збережені)."""
    rows = conn.execute(
        """SELECT * FROM replacements 
           WHERE group_name = ? AND is_archived = TRUE 
           ORDER BY date DESC, lesson_number ASC 
           LIMIT ?""",
        (group_name, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def archive_previous_replacements(conn, before_date: str, group_name: str = "ПІ-231") -> int:
    """Автоматично архівує всі активні заміни з датою, меншою за before_date."""
    cur = conn.execute(
        """UPDATE replacements 
           SET is_archived = TRUE 
           WHERE group_name = ? AND date < ? AND (is_archived = FALSE OR is_archived IS NULL)""",
        (group_name, before_date),
    )
    return cur.rowcount if hasattr(cur, "rowcount") and cur.rowcount is not None and cur.rowcount >= 0 else 0


def save_replacement(conn, item: dict, auto_archive_previous: bool = True) -> bool:
    """Зберігає заміну в базу даних. За замовчуванням архівує заміни за попередні дати."""
    date_val = item["date"]
    grp = item.get("group_name") or item.get("group", "ПІ-231")
    if auto_archive_previous:
        archive_previous_replacements(conn, date_val, group_name=grp)

    conn.execute(
        """INSERT INTO replacements 
           (date, group_name, lesson_number, old_subject, new_subject, teacher, room, is_archived)
           VALUES (?, ?, ?, ?, ?, ?, ?, FALSE)
           ON CONFLICT (date, group_name, lesson_number, new_subject) DO UPDATE
           SET old_subject = excluded.old_subject,
               teacher = excluded.teacher,
               room = excluded.room,
               is_archived = FALSE""",
        (
            date_val,
            grp,
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


def format_replacements_broadcast(items: list[dict], date_str: str) -> str:
    """Форматує гарне спільне повідомлення для автоматичної розсилки всій групі."""
    lines = [
        "📢 <b>Увага! Опубліковано заміни пар</b>",
        f"📅 <b>Дата:</b> <code>{date_str}</code>\n",
    ]
    sorted_items = sorted(items, key=lambda x: int(x.get("lesson_number") or 0))
    for it in sorted_items:
        old_s = html.escape(it["old_subject"]) if it.get("old_subject") else "—"
        new_s = html.escape(it["new_subject"])
        teacher = html.escape(it["teacher"]) if it.get("teacher") else ""
        room = f" (ауд. {html.escape(it['room'])})" if it.get("room") else ""
        teacher_part = f" — {teacher}{room}" if (teacher or room) else ""
        lines.append(f"⏰ <b>{it.get('lesson_number')} пара:</b> <b>{new_s}</b>{teacher_part}")
        if old_s and old_s != "—":
            lines.append(f"   <i>(замість: {old_s})</i>")
        lines.append("")

    lines.append("Актуальний розклад оновлено в меню <b>«📅 Розклад»</b> та <b>«🔄 Заміни»</b>.")
    return "\n".join(lines)

