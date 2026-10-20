import html
import logging
from contextlib import suppress

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db import transaction
from services import admins, users
from services.errors import BotError

log = logging.getLogger(__name__)


def create_bug_report(
    conn,
    telegram_id: int,
    text: str,
    username: str | None = None,
    photo_file_id: str | None = None,
    fallback_name: str = "Студент",
) -> int:
    """Зберігає новий баг-репорт у базу даних і повертає його ID."""
    clean_text = (text or "").strip()
    if not clean_text and not photo_file_id:
        raise BotError("Опишіть проблему текстом або прикріпіть скріншот.")
    if not clean_text and photo_file_id:
        clean_text = "(Скріншот без текстового опису)"
    if len(clean_text) > 2000:
        raise BotError("Опис занадто довгий (максимум 2000 символів).")

    user = users.get_user(conn, telegram_id)
    user_name = user["full_name"] if user else fallback_name

    with transaction(conn):
        cur = conn.execute(
            """INSERT INTO bug_reports (user_telegram_id, user_name, username, text, photo_file_id, status)
               VALUES (?, ?, ?, ?, ?, 'open')""",
            (telegram_id, user_name, username, clean_text, photo_file_id),
        )
        return cur.lastrowid


def get_bug_report(conn, report_id: int):
    return conn.execute("SELECT * FROM bug_reports WHERE id = ?", (report_id,)).fetchone()


def list_bug_reports(conn, status: str | None = "open", limit: int = 20) -> list:
    if status:
        return conn.execute(
            "SELECT * FROM bug_reports WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM bug_reports ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def resolve_bug_report(conn, report_id: int, admin_reply: str | None = None) -> None:
    with transaction(conn):
        if admin_reply is not None:
            conn.execute(
                "UPDATE bug_reports SET status = 'resolved', admin_reply = ? WHERE id = ?",
                (admin_reply.strip(), report_id),
            )
        else:
            conn.execute(
                "UPDATE bug_reports SET status = 'resolved' WHERE id = ?",
                (report_id,),
            )


def format_bug_report_card(report) -> str:
    status_badge = "🟢 Відкритий" if report["status"] == "open" else "✅ Вирішено"
    uname = f" (@{html.escape(report['username'])})" if report.get("username") else ""
    lines = [
        f"🐞 <b>Баг-репорт #{report['id']}</b> [{status_badge}]",
        f"👤 <b>Від:</b> {html.escape(report['user_name'])}{uname} (<code>{report['user_telegram_id']}</code>)",
        f"🕒 <b>Час:</b> {str(report['created_at'])[:16]}",
        "",
        f"📝 <b>Опис проблеми:</b>\n{html.escape(report['text'])}",
    ]
    if report.get("admin_reply"):
        lines.append("")
        lines.append(f"💬 <b>Відповідь адміна:</b>\n<i>{html.escape(report['admin_reply'])}</i>")
    return "\n".join(lines)


async def notify_admins_new_bug(bot: Bot, conn, report_id: int) -> None:
    """Миттєво сповіщає всіх адміністраторів про новий баг-репорт."""
    if bot is None:
        return
    report = get_bug_report(conn, report_id)
    if not report:
        return

    text = format_bug_report_card(report)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💬 Відповісти студенту", callback_data=f"bug_reply:{report_id}"),
                InlineKeyboardButton(text="✅ Вирішено", callback_data=f"bug_resolve:{report_id}"),
            ]
        ]
    )

    admin_rows = admins.list_admins(conn)
    for adm in admin_rows:
        adm_id = adm["telegram_id"]
        if not adm_id or adm_id <= 0:
            continue
        with suppress(Exception):
            if report.get("photo_file_id"):
                await bot.send_photo(
                    chat_id=adm_id,
                    photo=report["photo_file_id"],
                    caption=text,
                    reply_markup=kb,
                )
            else:
                await bot.send_message(
                    chat_id=adm_id,
                    text=text,
                    reply_markup=kb,
                )
