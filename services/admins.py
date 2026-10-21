import sqlite3

from db import transaction
from services.errors import BotError, PermissionDenied


def is_admin(conn: sqlite3.Connection, telegram_id: int) -> bool:
    row = conn.execute("SELECT 1 FROM admins WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return row is not None


def get_owner_ids(conn: sqlite3.Connection | None = None) -> set[int]:
    import config
    owners = {config.OWNER_ID}
    if conn is not None:
        has_configured_owner = any(
            conn.execute("SELECT 1 FROM admins WHERE telegram_id = ?", (oid,)).fetchone()
            for oid in owners
        )
        if not has_configured_owner:
            row = conn.execute("SELECT telegram_id FROM admins ORDER BY id ASC LIMIT 1").fetchone()
            if row:
                return {row["telegram_id"]}
    return owners


def is_owner(conn: sqlite3.Connection | None, telegram_id: int) -> bool:
    return telegram_id in get_owner_ids(conn)


def require_owner(conn: sqlite3.Connection | None, telegram_id: int) -> None:
    if not is_owner(conn, telegram_id):
        raise PermissionDenied("Ця функція доступна лише головному адміністратору (власнику бота).")


def require_admin(conn: sqlite3.Connection, telegram_id: int) -> None:
    """Серверна перевірка ролі: викликається в кожній адмін-операції."""
    if not is_admin(conn, telegram_id):
        raise PermissionDenied("У вас немає прав адміністратора.")


def log_action(conn: sqlite3.Connection, admin_id: int, action: str, details: str = "") -> None:
    conn.execute(
        "INSERT INTO admin_log (admin_telegram_id, action, details) VALUES (?, ?, ?)",
        (admin_id, action, details),
    )


def list_admins(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT a.telegram_id, u.full_name FROM admins a
           LEFT JOIN users u ON u.telegram_id = a.telegram_id ORDER BY a.id"""
    ).fetchall()


def init_admins(conn: sqlite3.Connection, initial_ids: list[int]) -> None:
    """Початкові адміни створюються лише якщо в базі немає жодного."""
    with transaction(conn):
        if conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] > 0:
            return
        for telegram_id in initial_ids:
            conn.execute("INSERT INTO admins (telegram_id) VALUES (?)", (telegram_id,))


def add_admin(conn: sqlite3.Connection, actor_id: int, new_id: int) -> None:
    with transaction(conn):
        require_admin(conn, actor_id)
        if is_admin(conn, new_id):
            raise BotError("Цей користувач уже є адміністратором.")
        conn.execute(
            "INSERT INTO admins (telegram_id, added_by) VALUES (?, ?)", (new_id, actor_id)
        )
        log_action(conn, actor_id, "add_admin", str(new_id))


def remove_admin(conn: sqlite3.Connection, actor_id: int, target_id: int) -> None:
    with transaction(conn):
        require_admin(conn, actor_id)
        if not is_admin(conn, target_id):
            raise BotError("Цей користувач не є адміністратором.")
        if conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] <= 1:
            raise BotError("Не можна видалити останнього адміністратора.")
        conn.execute("DELETE FROM admins WHERE telegram_id = ?", (target_id,))
        log_action(conn, actor_id, "remove_admin", str(target_id))
