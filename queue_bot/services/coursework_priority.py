"""Тиждень показу курсових на звичайних парах.

У вказані дати студент, якого викладач веде по курсовій, при записі в чергу на пару
цього викладача стає перед тими, хто здає лабораторні. Дати й списки —
у data/coursework_priority.json; немає файлу або дати минули — усе працює як завжди.
"""
import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from queue_bot import config


def load(path: str | None = None) -> dict | None:
    path = path or config.COURSEWORK_PRIORITY_PATH
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def applies(conn, session, user_id: int, *, today: date | None = None, data: dict | None = None) -> bool:
    if session["queue_type"] != "lesson":
        return False
    data = load() if data is None else data
    if not data:
        return False
    if session["subject_id"] in data.get("skip_subject_ids", []):
        return False
    today = today or datetime.now(ZoneInfo(config.TIMEZONE)).date()
    if not date.fromisoformat(data["from"]) <= today <= date.fromisoformat(data["to"]):
        return False
    students = next(
        (t["students"] for t in data.get("supervisors", []) if t["teacher_id"] == session["teacher_id"]),
        [],
    )
    if not students:
        return False
    row = conn.execute("SELECT full_name FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row) and _norm(row["full_name"]) in {_norm(name) for name in students}


def promote_existing(conn, *, today: date | None = None, data: dict | None = None) -> int:
    """Ставить наперед курсовиків, які записались у відкриті черги ще до початку тижня.
    Повертає кількість змінених черг."""
    from queue_bot.services import queues
    data = load() if data is None else data
    today = today or datetime.now(ZoneInfo(config.TIMEZONE)).date()
    changed = 0
    for session in queues.list_sessions(conn, ("open", "closed")):
        if queues.promote_coursework(
            conn, session["id"], lambda user_id: applies(conn, session, user_id, today=today, data=data)
        ):
            changed += 1
    return changed


async def watch(conn, interval_sec: int = 60) -> None:
    """Раз на день у межах тижня курсових переставляє тих, хто вже стоїть у чергах."""
    import asyncio
    import logging
    log = logging.getLogger(__name__)
    while True:
        try:
            today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
            data = load()
            key = f"coursework_promoted_{today.isoformat()}"
            if data and date.fromisoformat(data["from"]) <= today <= date.fromisoformat(data["to"]) \
                    and not conn.execute("SELECT 1 FROM bot_state WHERE key = ?", (key,)).fetchone():
                changed = promote_existing(conn, today=today, data=data)
                conn.execute("INSERT INTO bot_state (key, value) VALUES (?, ?)", (key, str(changed)))
                log.info("Coursework priority applied to %s existing queues", changed)
        except Exception:
            log.exception("Coursework priority watcher failed")
        await asyncio.sleep(interval_sec)


def _norm(name: str) -> str:
    # «Дар`єв», «Дар'єв» і «Дар’єв» — одне прізвище
    for mark in "`’ʼ":
        name = name.replace(mark, "'")
    return " ".join(name.split()).lower()
