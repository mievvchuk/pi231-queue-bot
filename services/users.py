import re
import sqlite3

from services.errors import BotError

NAME_PATTERN = re.compile(r"^[^\W\d_]+(?:['’\-][^\W\d_]+)*$")


def validate_full_name(text: str) -> str:
    """Очікуємо рівно два слова: прізвище та ім'я."""
    words = text.split()
    if len(words) != 2:
        raise BotError("Введіть прізвище та ім'я двома словами, наприклад: Петренко Іван")
    for word in words:
        if not NAME_PATTERN.match(word) or not 2 <= len(word) <= 30:
            raise BotError("Допустимі лише літери (2–30 символів у слові), без цифр і символів.")
    return " ".join(word[0].upper() + word[1:] for word in words)


def get_user(conn: sqlite3.Connection, telegram_id: int):
    return conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()


def register_user(conn: sqlite3.Connection, telegram_id: int, full_name: str):
    existing = get_user(conn, telegram_id)
    if existing:
        if existing.get("is_blocked"):
            raise BotError("Вас заблоковано адміністратором.")
        raise BotError("Ви вже зареєстровані.")
    name = validate_full_name(full_name)
    conn.execute(
        "INSERT INTO users (telegram_id, full_name) VALUES (?, ?)", (telegram_id, name)
    )
    return get_user(conn, telegram_id)


def rename_user(conn: sqlite3.Connection, telegram_id: int, full_name: str):
    name = validate_full_name(full_name)
    conn.execute("UPDATE users SET full_name = ? WHERE telegram_id = ?", (name, telegram_id))
    return get_user(conn, telegram_id)


def admin_rename_user(conn, user_id: int, new_name: str):
    """Староста змінює ПІБ студенту."""
    name = validate_full_name(new_name)
    conn.execute("UPDATE users SET full_name = ? WHERE id = ?", (name, user_id))
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def toggle_block_user(conn, user_id: int) -> bool:
    """Блокує / розблоковує студента."""
    row = conn.execute("SELECT is_blocked FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise BotError("Користувача не знайдено.")
    new_state = not bool(row["is_blocked"])
    conn.execute("UPDATE users SET is_blocked = ? WHERE id = ?", (new_state, user_id))
    return new_state


def list_blocked_users(conn):
    """Повертає список заблокованих студентів."""
    return conn.execute("SELECT * FROM users WHERE is_blocked ORDER BY full_name").fetchall()



def get_or_create_user_by_name(conn, full_name: str):
    """Шукає або створює користувача за ПІБ (якщо додає староста вручну)."""
    name = validate_full_name(full_name)
    row = conn.execute("SELECT * FROM users WHERE full_name = ?", (name,)).fetchone()
    if row:
        return row
    # Для студентів, яких вписав адмін без їхнього telegram_id, створюємо унікальний від'ємний telegram_id
    min_id = conn.execute("SELECT COALESCE(MIN(telegram_id), 0) FROM users WHERE telegram_id < 0").fetchone()[0]
    fake_tg_id = min_id - 1
    conn.execute("INSERT INTO users (telegram_id, full_name) VALUES (?, ?)", (fake_tg_id, name))
    return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (fake_tg_id,)).fetchone()

