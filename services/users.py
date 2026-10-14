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
    if get_user(conn, telegram_id):
        raise BotError("Ви вже зареєстровані.")
    name = validate_full_name(full_name)
    conn.execute(
        "INSERT INTO users (telegram_id, full_name) VALUES (?, ?)", (telegram_id, name)
    )
    return get_user(conn, telegram_id)


def rename_user(conn: sqlite3.Connection, telegram_id: int, full_name: str):
    if not get_user(conn, telegram_id):
        raise BotError("Спочатку зареєструйтесь: /start")
    name = validate_full_name(full_name)
    conn.execute("UPDATE users SET full_name = ? WHERE telegram_id = ?", (name, telegram_id))
    return get_user(conn, telegram_id)
