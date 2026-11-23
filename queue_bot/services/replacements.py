import html
from datetime import date, datetime
from zoneinfo import ZoneInfo
from queue_bot import config
from queue_bot.services.errors import BotError
UK_TZ = ZoneInfo(config.TIMEZONE)


def get_today_key() -> str:
    return datetime.now(UK_TZ).strftime("%Y-%m-%d")


def validate_publication_date(value: str) -> None:
    try:
        day = date.fromisoformat(value)
        if day.isoformat() != value:
            raise ValueError
    except (TypeError, ValueError):
        raise BotError("Вкажіть коректну дату замін у форматі ДД.ММ.РРРР.") from None
    if value < get_today_key():
        raise BotError(f"Дата замін {day:%d.%m.%Y} вже минула. Вкажіть сьогоднішню або майбутню дату.")


def validate_review(day: str, items: list[dict]) -> None:
    validate_publication_date(day)
    seen = set()
    for item in items:
        number = item.get("lesson_number")
        subject = (item.get("new_subject") or "").strip()
        if type(number) is not int or not 1 <= number <= 10 or not subject:
            raise BotError("Для кожної заміни вкажіть номер пари від 1 до 10 і предмет.")
        key = (number, subject.casefold())
        if key in seen:
            raise BotError(f"{number} пара повторюється з тим самим предметом. Перевірте список замін.")
        seen.add(key)


def format_review(day: str, items: list[dict]) -> str:
    try:
        display_date = f"{date.fromisoformat(day):%d.%m.%Y}"
    except (TypeError, ValueError):
        display_date = "уточніть дату"
    lines = [f"<b>Перевірка замін · {html.escape(display_date)}</b>"]
    try:
        validate_review(day, items)
    except BotError as err:
        lines.append(f"⚠️ {html.escape(str(err))}")
    lines.append("")
    for item in items:
        subject = html.escape((item.get("new_subject") or "Без предмета")[:120])
        number = html.escape(str(item.get("lesson_number") or "—"))
        line = f"<b>{number} пара</b> · {subject}"
        details = [item.get("teacher"), f"ауд. {item['room']}" if item.get("room") else None,
                   f"замість {item['old_subject']}" if item.get("old_subject") else None]
        lines.append(line + "".join(f" · {html.escape(str(value)[:65])}" for value in details if value))
    if not items:
        lines.append("Замін немає.")
    notes = dict.fromkeys(item["source_note"] for item in items if item.get("source_note"))
    if notes:
        lines.append(f"📝 {html.escape(' · '.join(str(note) for note in notes)[:260])}")
    return "\n".join(lines)


def get_replacements(conn, date: str | None = None, group_name: str = "ПІ-231", include_archived: bool = False, *, from_date: str | None = None) -> list[dict]:
    """Повертає заміни на конкретну дату або починаючи з from_date (типово сьогодні)."""
    arch_sql = "" if include_archived else "AND (is_archived = FALSE OR is_archived IS NULL)"
    if date:
        rows = conn.execute(
            f"""SELECT * FROM replacements 
               WHERE date = ? AND group_name = ? {arch_sql}
               ORDER BY lesson_number""",
            (date, group_name),
        ).fetchall()
    else:
        first_day = from_date or get_today_key()
        rows = conn.execute(
            f"""SELECT * FROM replacements 
               WHERE group_name = ? AND date >= ? {arch_sql}
               ORDER BY date, lesson_number""",
            (group_name, first_day),
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
           (date, group_name, lesson_number, old_subject, new_subject, teacher, room, source_note, is_archived)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, FALSE)
           ON CONFLICT (date, group_name, lesson_number, new_subject) DO UPDATE
           SET old_subject = excluded.old_subject,
               teacher = excluded.teacher,
               room = excluded.room,
               source_note = excluded.source_note,
               is_archived = FALSE""",
        (
            date_val,
            grp,
            int(item["lesson_number"]),
            item.get("old_subject"),
            item["new_subject"].strip(),
            item.get("teacher"),
            item.get("room"),
            item.get("source_note"),
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
        + (f"\n📝 <b>Примітка з фото:</b> {html.escape(str(item['source_note']))}" if item.get('source_note') else '')
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

