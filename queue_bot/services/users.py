import re
import sqlite3

from queue_bot.storage.database import transaction
from queue_bot.services import admins
from queue_bot.services.errors import BotError

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


def get_user_by_id(conn, user_id: int):
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def register_user(conn: sqlite3.Connection, telegram_id: int, full_name: str):
    from queue_bot.services.restrictions import require_available
    require_available(conn, telegram_id)
    existing = get_user(conn, telegram_id)
    if existing:
        if bool(existing["is_blocked"]):
            raise BotError("Вас заблоковано адміністратором.")
        raise BotError("Ви вже зареєстровані.")
    name = validate_full_name(full_name)

    # Якщо староста вже додав цього студента вручну — прив'язуємо його реальний telegram_id
    placeholder = conn.execute(
        "SELECT * FROM users WHERE LOWER(full_name) = LOWER(?) AND telegram_id < 0", (name,)
    ).fetchone()

    if placeholder:
        require_available(conn, placeholder['telegram_id'])
        placeholder = get_user_by_id(conn, placeholder['id'])
        if bool(placeholder["is_blocked"]):
            raise BotError("Вас заблоковано адміністратором.")
        conn.execute("UPDATE users SET telegram_id = ? WHERE id = ?", (telegram_id, placeholder["id"]))
        return get_user(conn, telegram_id)

    # Перевіряємо, чи це ім'я не зайняте іншим зареєстрованим користувачем
    already_taken = conn.execute(
        "SELECT 1 FROM users WHERE LOWER(full_name) = LOWER(?) AND telegram_id > 0", (name,)
    ).fetchone()
    if already_taken:
        raise BotError("Користувач з таким ПІБ уже зареєстрований. Якщо це помилка, зверніться до старости.")

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
    from queue_bot.services.restrictions import expire_due_restrictions
    expire_due_restrictions(conn)
    return conn.execute("SELECT * FROM users WHERE is_blocked ORDER BY LOWER(full_name), id").fetchall()


def get_or_create_user_by_name(conn, full_name: str):
    """Шукає або створює користувача за ПІБ (якщо додає староста вручну)."""
    name = validate_full_name(full_name)
    row = conn.execute("SELECT * FROM users WHERE LOWER(full_name) = LOWER(?)", (name,)).fetchone()
    if row:
        return row
    min_id = conn.execute("SELECT COALESCE(MIN(telegram_id), 0) FROM users WHERE telegram_id < 0").fetchone()[0]
    fake_tg_id = min_id - 1
    conn.execute("INSERT INTO users (telegram_id, full_name) VALUES (?, ?)", (fake_tg_id, name))
    return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (fake_tg_id,)).fetchone()


# ---------- CRUD розділу «Користувачі» (з перевіркою прав та аудитом) ----------

def list_users(conn, page: int = 1, per_page: int = 8) -> tuple[list, int, int]:
    """Повертає користувачів з алфавітним сортуванням та пагінацією."""
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if total == 0:
        return [], 1, 1
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    rows = conn.execute(
        "SELECT * FROM users ORDER BY LOWER(full_name), id LIMIT ? OFFSET ?",
        (per_page, offset),
    ).fetchall()
    return rows, page, total_pages


def search_users(conn, query: str) -> list:
    """Параметризований нечутливий до регістру пошук користувачів за частиною імені/прізвища."""
    clean = query.strip().lower()
    if not clean:
        return []
    return conn.execute(
        "SELECT * FROM users WHERE LOWER(full_name) LIKE ? ORDER BY LOWER(full_name), id",
        (f"%{clean}%",),
    ).fetchall()


def admin_create_placeholder(conn, actor_id: int, full_name: str):
    """Створює placeholder-користувача без реального Telegram ID."""
    admins.require_admin(conn, actor_id)
    name = validate_full_name(full_name)

    existing = conn.execute(
        "SELECT 1 FROM users WHERE LOWER(full_name) = LOWER(?)", (name,)
    ).fetchone()
    if existing:
        raise BotError("Користувач з таким ПІБ уже існує.")

    with transaction(conn):
        min_id = conn.execute(
            "SELECT COALESCE(MIN(telegram_id), 0) FROM users WHERE telegram_id < 0"
        ).fetchone()[0]
        fake_tg_id = min_id - 1
        conn.execute("INSERT INTO users (telegram_id, full_name) VALUES (?, ?)", (fake_tg_id, name))
        new_user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (fake_tg_id,)).fetchone()
        admins.log_action(conn, actor_id, "admin_create_user", f"{new_user['id']}: {name}")
        return new_user


def admin_update_full_name(conn, actor_id: int, user_id: int, new_name: str):
    """Оновлює лише full_name користувача без зміни telegram_id, черг та історії."""
    admins.require_admin(conn, actor_id)
    user = get_user_by_id(conn, user_id)
    if not user:
        raise BotError("Користувача не знайдено.")

    name = validate_full_name(new_name)
    existing = conn.execute(
        "SELECT 1 FROM users WHERE LOWER(full_name) = LOWER(?) AND id != ?", (name, user_id)
    ).fetchone()
    if existing:
        raise BotError("Користувач з таким ПІБ уже існує.")

    with transaction(conn):
        conn.execute("UPDATE users SET full_name = ? WHERE id = ?", (name, user_id))
        admins.log_action(conn, actor_id, "admin_rename_user", f"{user_id}: {user['full_name']} -> {name}")
        return get_user_by_id(conn, user_id)


def admin_delete_user(conn, actor_id: int, user_id: int) -> None:
    """Видаляє лише невикористаного користувача або placeholder без записів у чергах."""
    admins.require_admin(conn, actor_id)
    user = get_user_by_id(conn, user_id)
    if not user:
        raise BotError("Користувача не знайдено.")

    if admins.is_admin(conn, user["telegram_id"]):
        raise BotError("Неможливо видалити адміністратора. Спочатку зніміть з нього права адміністратора.")

    has_entries = conn.execute(
        "SELECT 1 FROM queue_entries WHERE user_id = ? LIMIT 1", (user_id,)
    ).fetchone()
    if has_entries:
        raise BotError(
            "Неможливо видалити користувача, який має активні або історичні записи в чергах.\n"
            "Ви можете заблокувати його замість видалення."
        )

    with transaction(conn):
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        admins.log_action(conn, actor_id, "admin_delete_user", f"{user_id}: {user['full_name']}")


def admin_toggle_block_user(conn, actor_id: int, user_id: int) -> bool:
    """Блокує / розблоковує користувача з фіксацією в аудит-лозі."""
    admins.require_admin(conn, actor_id)
    user = get_user_by_id(conn, user_id)
    if not user:
        raise BotError("Користувача не знайдено.")

    from queue_bot.services import restrictions
    if restrictions.restriction_message(conn, user['telegram_id']):
        restrictions.clear_restriction(conn, actor_id, user_id)
        return False
    restrictions.set_restriction(conn, actor_id, user_id)
    return True
