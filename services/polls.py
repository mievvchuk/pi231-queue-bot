import asyncio
import html
import json
import logging
import os
from contextlib import suppress
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot

import config
from db import transaction
from services.admins import log_action, require_admin
from services.errors import BotError
from services.updates import broadcast_message_to_users, get_active_user_ids

log = logging.getLogger(__name__)
UK_TZ = ZoneInfo(config.TIMEZONE)


def get_group_chat_id(conn) -> int | None:
    """Повертає збережений ID групового чату ПІ-231 з bot_state або змінної середовища."""
    try:
        row = conn.execute("SELECT value FROM bot_state WHERE key = 'group_chat_id'").fetchone()
        if row and row["value"]:
            return int(row["value"])
    except Exception:
        pass
    env_val = os.getenv("GROUP_CHAT_ID", "").strip()
    if env_val and (env_val.lstrip("-").isdigit()):
        return int(env_val)
    return None


def set_group_chat_id(conn, chat_id: int) -> None:
    """Зберігає ID групового чату в таблицю bot_state."""
    conn.execute(
        """INSERT INTO bot_state (key, value, updated_at)
           VALUES ('group_chat_id', ?, CURRENT_TIMESTAMP)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
        (str(chat_id),),
    )


def create_poll(
    conn,
    actor_id: int,
    question: str,
    options: list[str],
    is_anonymous: bool = True,
    allows_multiple: bool = False,
    target: str = "private",
    closes_at: str | None = None,
) -> int:
    """Створює нове опитування в базі даних і повертає його ID."""
    with transaction(conn):
        require_admin(conn, actor_id)
        q_clean = question.strip()
        if not q_clean:
            raise BotError("Запитання опитування не може бути порожнім.")
        if len(q_clean) > 300:
            raise BotError("Запитання занадто довге (максимум 300 символів).")

        cleaned_opts = [o.strip() for o in options if o and o.strip()]
        if len(cleaned_opts) < 2:
            raise BotError("Опитування повинно містити щонайменше 2 варіанти відповіді.")
        if len(cleaned_opts) > 10:
            raise BotError("Максимальна кількість варіантів відповіді в Telegram — 10.")
        for idx, opt in enumerate(cleaned_opts, 1):
            if len(opt) > 100:
                raise BotError(f"Варіант №{idx} перевищує ліміт у 100 символів.")

        if target not in ("private", "group"):
            raise BotError("Некоректне призначення опитування.")

        cur = conn.execute(
            """INSERT INTO polls (question, options_json, is_anonymous, allows_multiple, target, status, created_by, closes_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                q_clean,
                json.dumps(cleaned_opts, ensure_ascii=False),
                bool(is_anonymous),
                bool(allows_multiple),
                target,
                actor_id,
                closes_at,
            ),
        )
        poll_id = cur.lastrowid
        log_action(conn, actor_id, "create_poll", f"poll_id={poll_id} target={target}")
        return poll_id


def get_poll(conn, poll_id: int):
    return conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()


def list_polls(conn, status: str | None = None, limit: int = 20) -> list:
    if status:
        return conn.execute(
            "SELECT * FROM polls WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM polls ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def register_poll_instance(conn, poll_id: int, chat_id: int, message_id: int, tg_poll_id: str) -> int:
    cur = conn.execute(
        """INSERT INTO poll_instances (poll_id, chat_id, message_id, tg_poll_id)
           VALUES (?, ?, ?, ?)""",
        (poll_id, chat_id, message_id, str(tg_poll_id)),
    )
    return cur.lastrowid


def get_poll_instances(conn, poll_id: int) -> list:
    return conn.execute(
        "SELECT * FROM poll_instances WHERE poll_id = ? ORDER BY id ASC",
        (poll_id,),
    ).fetchall()


def record_poll_answer(
    conn,
    tg_poll_id: str,
    voter_telegram_id: int,
    voter_name: str,
    option_ids: list[int],
) -> int | None:
    """Обробляє подію PollAnswer (голосування користувача у неанонімному опитуванні)."""
    inst = conn.execute(
        "SELECT * FROM poll_instances WHERE tg_poll_id = ?",
        (str(tg_poll_id),),
    ).fetchone()
    if not inst:
        return None

    poll_id = inst["poll_id"]
    poll = get_poll(conn, poll_id)
    if not poll or poll["status"] != "active":
        return None

    with transaction(conn):
        conn.execute(
            "DELETE FROM poll_votes WHERE poll_id = ? AND voter_telegram_id = ?",
            (poll_id, voter_telegram_id),
        )
        for opt_idx in option_ids:
            conn.execute(
                """INSERT INTO poll_votes (poll_id, voter_telegram_id, voter_name, option_index)
                   VALUES (?, ?, ?, ?)""",
                (poll_id, voter_telegram_id, voter_name or "", int(opt_idx)),
            )
    return poll_id


def record_poll_state_update(
    conn,
    tg_poll_id: str,
    option_voter_counts: list[int],
) -> int | None:
    """Обробляє подію Poll (оновлення лічильників варіантів від Telegram)."""
    inst = conn.execute(
        "SELECT * FROM poll_instances WHERE tg_poll_id = ?",
        (str(tg_poll_id),),
    ).fetchone()
    if not inst:
        return None

    poll_id = inst["poll_id"]
    poll = get_poll(conn, poll_id)
    if not poll:
        return None

    with transaction(conn):
        conn.execute(
            "UPDATE poll_instances SET options_counts_json = ? WHERE id = ?",
            (json.dumps(option_voter_counts), inst["id"]),
        )
        # Якщо це приватна розсилка і опитування анонімне, PollAnswer не приходить,
        # проте інстанс належить конкретному chat_id, тому ми можемо точно оновити підрахунок голосів:
        if poll["target"] == "private" and bool(poll["is_anonymous"]):
            voter_id = inst["chat_id"]
            conn.execute(
                "DELETE FROM poll_votes WHERE poll_id = ? AND voter_telegram_id = ?",
                (poll_id, voter_id),
            )
            for opt_idx, cnt in enumerate(option_voter_counts):
                if cnt > 0:
                    conn.execute(
                        """INSERT INTO poll_votes (poll_id, voter_telegram_id, voter_name, option_index)
                           VALUES (?, ?, '', ?)""",
                        (poll_id, voter_id, opt_idx),
                    )
    return poll_id


def get_poll_results(conn, poll_id: int) -> dict:
    """Формує повну статистику голосування по опитуванню."""
    poll = get_poll(conn, poll_id)
    if not poll:
        raise BotError("Опитування не знайдено.")

    options = json.loads(poll["options_json"])
    votes = conn.execute(
        """SELECT pv.voter_telegram_id, pv.voter_name, pv.option_index, u.full_name
           FROM poll_votes pv
           LEFT JOIN users u ON u.telegram_id = pv.voter_telegram_id
           WHERE pv.poll_id = ?
           ORDER BY pv.id ASC""",
        (poll_id,),
    ).fetchall()

    option_Counts = [0] * len(options)
    option_voters: list[list[str]] = [[] for _ in options]
    unique_voters = set()

    for v in votes:
        idx = v["option_index"]
        if 0 <= idx < len(options):
            option_Counts[idx] += 1
            unique_voters.add(v["voter_telegram_id"])
            display_name = v["full_name"] or v["voter_name"] or f"ID:{v['voter_telegram_id']}"
            option_voters[idx].append(display_name)

    total_voters = len(unique_voters)

    # Якщо опитування в групі та є дані з Poll update (особливо для анонімних у групі)
    if poll["target"] == "group":
        instances = get_poll_instances(conn, poll_id)
        for inst in instances:
            if inst["options_counts_json"]:
                try:
                    inst_counts = json.loads(inst["options_counts_json"])
                    if len(inst_counts) == len(options):
                        if sum(inst_counts) >= sum(option_Counts):
                            option_Counts = inst_counts
                            if total_voters == 0:
                                total_voters = max(inst_counts) if bool(poll["allows_multiple"]) else sum(inst_counts)
                except Exception:
                    pass

    total_votes_cast = sum(option_Counts)
    denom = total_voters if total_voters > 0 else total_votes_cast

    results_per_option = []
    for i, opt_text in enumerate(options):
        cnt = option_Counts[i]
        pct = round((cnt / denom) * 100) if denom > 0 else 0
        pct = min(100, pct)
        results_per_option.append({
            "index": i,
            "text": opt_text,
            "count": cnt,
            "percent": pct,
            "voters": option_voters[i] if not bool(poll["is_anonymous"]) else [],
        })

    return {
        "poll": poll,
        "options": results_per_option,
        "total_voters": total_voters,
        "total_votes_cast": total_votes_cast,
    }


def _make_progress_bar(percent: int, width: int = 8) -> str:
    filled = round((percent / 100) * width)
    filled = max(0, min(width, filled))
    return "🟩" * filled + "⬜" * (width - filled)


def format_poll_results(conn, poll_id: int, is_final: bool = False) -> str:
    """Повертає красиво відформатований HTML-текст результатів опитування."""
    data = get_poll_results(conn, poll_id)
    poll = data["poll"]

    if is_final or poll["status"] == "closed":
        header = f"🏁 <b>Підсумки опитування #{poll['id']} (завершено)</b>"
    else:
        header = f"📊 <b>Поточні результати опитування #{poll['id']}</b>"

    anon_str = "🕵️ Анонімне" if bool(poll["is_anonymous"]) else "👀 Публічне"
    mult_str = "🔢 Кілька варіантів" if bool(poll["allows_multiple"]) else "1️⃣ Один варіант"
    target_str = "👥 У групі" if poll["target"] == "group" else "📩 У приватних"

    lines = [
        header,
        f"<i>{anon_str} • {mult_str} • {target_str}</i>",
    ]

    if poll["status"] == "active" and poll["closes_at"]:
        try:
            dt = datetime.fromisoformat(str(poll["closes_at"]))
            lines.append(f"⏱ <i>Закриється автоматично о: {dt.strftime('%d.%m.%Y %H:%M')}</i>")
        except Exception:
            pass

    lines.append("")
    lines.append(f"❓ <b>{html.escape(poll['question'])}</b>")
    lines.append("")

    for opt in data["options"]:
        bar = _make_progress_bar(opt["percent"])
        lines.append(f"<b>{opt['index'] + 1}. {html.escape(opt['text'])}</b>")
        lines.append(f"{bar} <b>{opt['percent']}%</b> ({opt['count']} гол.)")
        if opt["voters"]:
            voters_escaped = ", ".join(html.escape(v) for v in opt["voters"][:25])
            if len(opt["voters"]) > 25:
                voters_escaped += f" та ще {len(opt['voters']) - 25}"
            lines.append(f"   └ 👤 <i>{voters_escaped}</i>")
        lines.append("")

    lines.append(f"👥 Усього проголосувало: <b>{data['total_voters']}</b>")
    return "\n".join(lines)


async def publish_poll(bot: Bot, conn, poll_id: int) -> tuple[int, int]:
    """Надсилає опитування адресатам (у групу або кожному студенту в приватні).
    Повертає (успішно, помилок)."""
    poll = get_poll(conn, poll_id)
    if not poll:
        raise BotError("Опитування не знайдено.")

    options = json.loads(poll["options_json"])
    is_anon = bool(poll["is_anonymous"])
    allows_mult = bool(poll["allows_multiple"])

    timer_note = ""
    if poll["closes_at"]:
        with suppress(Exception):
            dt = datetime.fromisoformat(str(poll["closes_at"]))
            timer_note = f"\n⏱ <i>Голосування триває до {dt.strftime('%H:%M (%d.%m)')}</i>"

    intro_text = (
        f"🗳 <b>Нове опитування для групи ПІ-231!</b>"
        f"{timer_note}\n"
        f"Оберіть варіант нижче 👇"
    )

    if poll["target"] == "group":
        group_id = get_group_chat_id(conn)
        if not group_id:
            raise BotError(
                "ID групового чату ще не налаштовано! "
                "Додайте бота в групу або вкажіть ID чату в меню «🔗 Груповий чат ПІ-231»."
            )
        try:
            await bot.send_message(group_id, intro_text)
            msg = await bot.send_poll(
                chat_id=group_id,
                question=poll["question"],
                options=options,
                is_anonymous=is_anon,
                allows_multiple_answers=allows_mult,
            )
            register_poll_instance(conn, poll_id, group_id, msg.message_id, msg.poll.id)
            return 1, 0
        except Exception as e:
            log.warning(f"Failed to send poll to group {group_id}: {e}")
            raise BotError(f"Не вдалося надіслати опитування в групу ({group_id}): {e}")

    # Приватна розсилка студентам
    uids = get_active_user_ids(conn)
    success = 0
    errors = 0
    for uid in uids:
        try:
            await bot.send_message(uid, intro_text)
            msg = await bot.send_poll(
                chat_id=uid,
                question=poll["question"],
                options=options,
                is_anonymous=is_anon,
                allows_multiple_answers=allows_mult,
            )
            register_poll_instance(conn, poll_id, uid, msg.message_id, msg.poll.id)
            success += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            log.warning(f"Failed to send poll {poll_id} to user {uid}: {e}")
            errors += 1
    return success, errors


async def send_poll_to_single_user(bot: Bot, conn, poll_id: int, telegram_id: int) -> bool:
    """Надсилає активне опитування конкретному студенту (якщо він ще не отримував або просить повторно)."""
    poll = get_poll(conn, poll_id)
    if not poll or poll["status"] != "active":
        raise BotError("Це опитування вже завершено.")
    options = json.loads(poll["options_json"])
    try:
        msg = await bot.send_poll(
            chat_id=telegram_id,
            question=poll["question"],
            options=options,
            is_anonymous=bool(poll["is_anonymous"]),
            allows_multiple_answers=bool(poll["allows_multiple"]),
        )
        register_poll_instance(conn, poll_id, telegram_id, msg.message_id, msg.poll.id)
        return True
    except Exception as e:
        log.warning(f"Failed to send single poll {poll_id} to {telegram_id}: {e}")
        return False


async def close_poll_and_summarize(bot: Bot, conn, poll_id: int) -> str:
    """Закриває опитування, зупиняє Telegram-бюлетені та розсилає фінальні підсумки."""
    poll = get_poll(conn, poll_id)
    if not poll:
        raise BotError("Опитування не знайдено.")

    if poll["status"] == "closed":
        return format_poll_results(conn, poll_id, is_final=True)

    with transaction(conn):
        conn.execute(
            "UPDATE polls SET status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (poll_id,),
        )

    instances = get_poll_instances(conn, poll_id)
    for inst in instances:
        with suppress(Exception):
            stopped_poll = await bot.stop_poll(chat_id=inst["chat_id"], message_id=inst["message_id"])
            if stopped_poll and stopped_poll.options:
                counts = [o.voter_count for o in stopped_poll.options]
                record_poll_state_update(conn, inst["tg_poll_id"], counts)
        await asyncio.sleep(0.03)

    summary_text = format_poll_results(conn, poll_id, is_final=True)

    if poll["target"] == "group":
        group_id = get_group_chat_id(conn)
        if group_id:
            with suppress(Exception):
                await bot.send_message(group_id, summary_text)
    else:
        uids = get_active_user_ids(conn)
        await broadcast_message_to_users(bot, uids, summary_text)

    return summary_text


async def check_expired_polls(bot: Bot, conn) -> int:
    """Перевіряє активні опитування з вичерпаним таймером і автоматично закриває їх."""
    now_kyiv = datetime.now(UK_TZ)
    active_polls = list_polls(conn, status="active", limit=50)
    closed_count = 0

    for p in active_polls:
        closes_at_str = p["closes_at"]
        if not closes_at_str:
            continue
        try:
            closes_dt = datetime.fromisoformat(str(closes_at_str))
            if closes_dt.tzinfo is None:
                closes_dt = closes_dt.replace(tzinfo=UK_TZ)
            if now_kyiv >= closes_dt:
                log.info(f"Auto-closing expired poll #{p['id']} ({p['question']})")
                await close_poll_and_summarize(bot, conn, p["id"])
                closed_count += 1
        except Exception as e:
            log.warning(f"Error checking expiry for poll #{p['id']}: {e}")

    return closed_count


async def start_polls_watcher(bot: Bot, conn, interval_sec: int = 15) -> None:
    """Фоновий процес для автоматичного закриття опитувань за таймером."""
    log.info("Polls timer watcher started")
    while True:
        try:
            await check_expired_polls(bot, conn)
        except Exception as e:
            log.exception(f"Unexpected error in polls watcher: {e}")
        await asyncio.sleep(interval_sec)
