import asyncio
import logging
import sqlite3
from contextlib import suppress

from aiogram import Bot

log = logging.getLogger(__name__)

# Реєстр версій та описів змін.
# Кожного разу, коли додається нова версія, додайте запис сюди:
# бот при запуску автоматично надішле це повідомлення всім зареєстрованим користувачам.
UPDATES: dict[str, str] = {
    "v1.4": (
        "🚀 <b>Оновлення бота (v1.4)</b>\n\n"
        "Ось що нового та виправленого в системі черг:\n\n"
        "1️⃣ <b>Кнопка «🔙 Назад до черги»:</b>\n"
        "— Виправлено помилку з правами адміністратора. Тепер повернення до перегляду черги працює стабільно для всіх користувачів.\n\n"
        "2️⃣ <b>Нові правила вибору номерів:</b>\n"
        "— Заборонено займати вже зайняті номери (зсувати інших студентів більше не можна).\n"
        "— Поки є вільні місця серед перших 10, можна обрати будь-який вільний номер від 1 до 10.\n"
        "— Якщо перші 10 місць уже зайняті, нові студенти записуються в кінець черги.\n\n"
        "3️⃣ <b>Виправлено вибір викладачів:</b>\n"
        "— Виправлено створення черг для предметів з декількома викладачами (зокрема, черга на КПЗ до Кондратюка Є.С. тепер успішно відкривається та доступна)."
    ),
    "v1.5": (
        "🎓 <b>Оновлення бота (v1.5) — Черги на курсові роботи!</b>\n\n"
        "Додано новий предмет та черги для консультацій і здачі:\n"
        "📚 <b>Курсова робота з КПЗ</b>\n\n"
        "Черги відкрито до всіх керівників курсових:\n"
        "— 👨‍🏫 Атаман В.О.\n"
        "— 👨‍🏫 Когут В.С.\n"
        "— 👨‍🏫 Кондратюк Є.С.\n"
        "— 👨‍🏫 Щуцький С.В.\n"
        "— 👨‍🏫 Жереб Д.В.\n\n"
        "Переглянути черги та записатися можна через кнопку <b>«📋 Усі черги»</b>."
    ),
    "v1.6": (
        "💬 <b>Оновлення бота (v1.6) — Черги на консультації та зручне групування!</b>\n\n"
        "1️⃣ <b>Черги на консультації (поза парами):</b>\n"
        "— Тепер при натисканні «➕ Відкрити чергу» можна обрати тип: «📖 Звичайна пара» або «💬 Консультація».\n"
        "— Дозволено відкривати чергу на консультацію паралельно з чергою на пару для одного й того ж викладача.\n\n"
        "2️⃣ <b>Компактне групування черг:</b>\n"
        "— Черги на курсові роботи в списку «📋 Усі черги» тепер об'єднані в одну зручну кнопку «📑 Курсова робота з КПЗ», яка відкриває список усіх керівників."
    ),
}


def ensure_updates_table(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bot_updates (
            version TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )"""
    )


def is_version_sent(conn, version: str) -> bool:
    ensure_updates_table(conn)
    row = conn.execute("SELECT 1 FROM bot_updates WHERE version = ?", (version,)).fetchone()
    return row is not None


def mark_version_sent(conn, version: str, description: str) -> None:
    ensure_updates_table(conn)
    conn.execute(
        "INSERT INTO bot_updates (version, description) VALUES (?, ?)",
        (version, description),
    )


def get_active_user_ids(conn) -> list[int]:
    rows = conn.execute(
        "SELECT telegram_id FROM users WHERE telegram_id > 0 AND is_blocked IS NOT TRUE"
    ).fetchall()
    return [r["telegram_id"] for r in rows if r["telegram_id"] and r["telegram_id"] > 0]


async def broadcast_message_to_users(bot: Bot, user_ids: list[int], text: str) -> tuple[int, int]:
    """Розсилає повідомлення списку користувачів із невеликою паузою проти rate-limit.
    Повертає (кількість_успішно, кількість_помилок)."""
    success = 0
    errors = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, text)
            success += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            log.warning(f"Не вдалося надіслати оновлення користувачу {uid}: {e}")
            errors += 1
    return success, errors


async def check_and_broadcast_updates(bot: Bot, conn) -> None:
    """Перевіряє, чи є нові невідправлені оновлення, та автоматично розсилає їх усім користувачам."""
    try:
        ensure_updates_table(conn)
        for version, text in UPDATES.items():
            if not is_version_sent(conn, version):
                log.info(f"Початок розсилки оновлення {version} для всіх користувачів...")
                uids = get_active_user_ids(conn)
                success, errors = await broadcast_message_to_users(bot, uids, text)
                mark_version_sent(conn, version, text)
                log.info(f"Розсилку оновлення {version} завершено: {success} успішно, {errors} помилок.")
    except Exception as e:
        log.exception(f"Помилка під час автоматичної розсилки оновлень: {e}")
