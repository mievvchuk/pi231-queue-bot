import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import config

log = logging.getLogger(__name__)
UK_TZ = ZoneInfo(config.TIMEZONE)

DAY_KEYS_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday"]
DAY_NAMES_UK = {
    "monday": "Понеділок",
    "tuesday": "Вівторок",
    "wednesday": "Середа",
    "thursday": "Четвер",
    "friday": "П'ятниця",
    "saturday": "Субота",
    "sunday": "Неділя",
}


def ensure_working_saturdays_table(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS working_saturdays (
            date TEXT PRIMARY KEY,
            replaces_day TEXT NOT NULL,
            week_type TEXT,
            description TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )"""
    )


def get_working_saturday(conn, date_str: str) -> dict | None:
    ensure_working_saturdays_table(conn)
    row = conn.execute(
        "SELECT * FROM working_saturdays WHERE date = ?", (date_str,)
    ).fetchone()
    return dict(row) if row else None


def set_working_saturday(
    conn,
    date_str: str,
    replaces_day: str,
    week_type: str | None = None,
    description: str = "",
) -> None:
    ensure_working_saturdays_table(conn)
    conn.execute(
        """INSERT INTO working_saturdays (date, replaces_day, week_type, description)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (date) DO UPDATE
           SET replaces_day = excluded.replaces_day,
               week_type = excluded.week_type,
               description = excluded.description""",
        (date_str, replaces_day.lower(), week_type, description),
    )


def remove_working_saturday(conn, date_str: str) -> bool:
    ensure_working_saturdays_table(conn)
    cur = conn.execute("DELETE FROM working_saturdays WHERE date = ?", (date_str,))
    return bool(cur.rowcount > 0)


def list_working_saturdays(conn, from_date: str | None = None) -> list[dict]:
    ensure_working_saturdays_table(conn)
    if from_date:
        rows = conn.execute(
            "SELECT * FROM working_saturdays WHERE date >= ? ORDER BY date",
            (from_date,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM working_saturdays ORDER BY date"
        ).fetchall()
    return [dict(r) for r in rows]


def get_next_saturday(ref_date: date | None = None) -> date:
    if ref_date is None:
        ref_date = datetime.now(UK_TZ).date()
    days_ahead = 5 - ref_date.weekday()
    if days_ahead < 0:  # If today is Sunday (weekday 6)
        days_ahead += 7
    return ref_date + timedelta(days=days_ahead)


def get_upcoming_saturdays(count: int = 4, ref_date: date | None = None) -> list[date]:
    first = get_next_saturday(ref_date)
    return [first + timedelta(days=7 * i) for i in range(count)]


def get_next_working_day(conn, ref_date: date | None = None) -> date:
    """Визначає наступний робочий день з урахуванням вихідних та зареєстрованих робочих субот.
    Якщо сьогодні п'ятниця і субота не робоча — повертає понеділок.
    Якщо субота робоча — повертає суботу."""
    if ref_date is None:
        ref_date = datetime.now(UK_TZ).date()

    candidate = ref_date + timedelta(days=1)
    for _ in range(14):  # Запобіжник зациклення
        weekday = candidate.weekday()  # 0=Пн .. 4=Пт, 5=Сб, 6=Нд
        if weekday < 5:
            return candidate
        if weekday == 5:
            if conn is not None:
                sat = get_working_saturday(conn, candidate.strftime("%Y-%m-%d"))
                if sat:
                    return candidate
        candidate += timedelta(days=1)

    return candidate


def get_next_working_day_key(conn, ref_date: date | None = None) -> str:
    return get_next_working_day(conn, ref_date).strftime("%Y-%m-%d")

