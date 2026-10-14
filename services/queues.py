import sqlite3

from db import transaction
from services.admins import log_action, require_admin
from services.errors import BotError

UNFINISHED = ("open", "closed")


# ---------- Читання ----------

def get_session(conn: sqlite3.Connection, session_id: int):
    return conn.execute("SELECT * FROM queue_sessions WHERE id = ?", (session_id,)).fetchone()


def list_sessions(conn: sqlite3.Connection, statuses: tuple[str, ...]) -> list[sqlite3.Row]:
    marks = ",".join("?" * len(statuses))
    return conn.execute(
        f"SELECT * FROM queue_sessions WHERE status IN ({marks}) ORDER BY id DESC", statuses
    ).fetchall()


def get_entry(conn: sqlite3.Connection, entry_id: int):
    return conn.execute("SELECT * FROM queue_entries WHERE id = ?", (entry_id,)).fetchone()


def get_entries(conn: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    """Активні записи по порядку. full_name = NULL означає вільне місце."""
    return conn.execute(
        """SELECT e.id, e.user_id, e.position, u.full_name, u.telegram_id, u.is_blocked
           FROM queue_entries e LEFT JOIN users u ON u.id = e.user_id
           WHERE e.session_id = ? AND e.status = 'active' ORDER BY e.position""",
        (session_id,),
    ).fetchall()


def get_history(conn: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT e.status, e.completed_at, u.full_name FROM queue_entries e
           JOIN users u ON u.id = e.user_id
           WHERE e.session_id = ? AND e.status != 'active' ORDER BY e.id""",
        (session_id,),
    ).fetchall()


def get_user_position(conn: sqlite3.Connection, telegram_id: int, session_id: int) -> int | None:
    row = conn.execute(
        """SELECT e.position FROM queue_entries e JOIN users u ON u.id = e.user_id
           WHERE e.session_id = ? AND u.telegram_id = ? AND e.status = 'active'""",
        (session_id, telegram_id),
    ).fetchone()
    return row["position"] if row else None


def list_user_sessions(conn: sqlite3.Connection, telegram_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT s.* FROM queue_sessions s
           JOIN queue_entries e ON e.session_id = s.id AND e.status = 'active'
           JOIN users u ON u.id = e.user_id
           WHERE u.telegram_id = ? AND s.status IN ('open', 'closed') ORDER BY s.id""",
        (telegram_id,),
    ).fetchall()


# ---------- Внутрішні помічники (без власних транзакцій) ----------

def _require_session(conn, session_id: int, statuses=UNFINISHED):
    session = get_session(conn, session_id)
    if session is None:
        raise BotError("Чергу не знайдено.")
    if session["status"] not in statuses:
        raise BotError("Ця черга вже недоступна для цієї дії.")
    return session


def _require_active_entry(conn, entry_id: int):
    entry = get_entry(conn, entry_id)
    if entry is None or entry["status"] != "active":
        raise BotError("Запис не знайдено або він уже неактивний.")
    _require_session(conn, entry["session_id"])
    return entry


def _set_order(conn, session_id: int, entry_ids: list[int]) -> None:
    """Єдине місце, де розставляються позиції: 1..N без пропусків.
    Спочатку знімаємо позиції, щоб не порушити унікальний індекс."""
    conn.execute(
        "UPDATE queue_entries SET position = NULL WHERE session_id = ? AND status = 'active'",
        (session_id,),
    )
    for position, entry_id in enumerate(entry_ids, start=1):
        conn.execute("UPDATE queue_entries SET position = ? WHERE id = ?", (position, entry_id))


def _ordered_ids(conn, session_id: int) -> list[int]:
    return [row["id"] for row in get_entries(conn, session_id)]


def _close_entry(conn, entry, new_status: str) -> None:
    """Прибирає запис з активної черги, зберігаючи його в історії."""
    conn.execute(
        "UPDATE queue_entries SET status = ?, position = NULL, completed_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (new_status, entry["id"]),
    )
    _set_order(conn, entry["session_id"], _ordered_ids(conn, entry["session_id"]))


# ---------- Дії студента ----------

def join_queue(conn: sqlite3.Connection, telegram_id: int, session_id: int,
               position: int | None = None) -> tuple[int, int]:
    """Повертає (місце студента, усього в черзі).
    position=None — у кінець; інакше студент вставляється на вибраний номер."""
    with transaction(conn):
        session = _require_session(conn, session_id, statuses=("open", "closed"))
        if session["status"] != "open":
            raise BotError("Запис до цієї черги закрито.")
        user = conn.execute("SELECT id, is_blocked FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if user is None:
            raise BotError("Спочатку зареєструйтесь: /start")
        if user.get("is_blocked"):
            raise BotError("Вас заблоковано адміністратором.")
        if get_user_position(conn, telegram_id, session_id) is not None:
            raise BotError("Ви вже записані в цю чергу.")

        if position is None:
            _place_user(conn, session_id, user["id"])
        else:
            _place_user_at(conn, session_id, user["id"], position)
    place = get_user_position(conn, telegram_id, session_id)
    return place, len(get_entries(conn, session_id))


def _place_user(conn, session_id: int, user_id: int) -> None:
    # Спочатку займаємо вільне зарезервоване місце, інакше — кінець черги
    slot = conn.execute(
        """SELECT id FROM queue_entries WHERE session_id = ? AND status = 'active'
           AND user_id IS NULL ORDER BY position LIMIT 1""",
        (session_id,),
    ).fetchone()
    if slot:
        _take_slot(conn, slot["id"], user_id)
        return
    last = conn.execute(
        "SELECT COALESCE(MAX(position), 0) FROM queue_entries "
        "WHERE session_id = ? AND status = 'active'",
        (session_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO queue_entries (session_id, user_id, position) VALUES (?, ?, ?)",
        (session_id, user_id, last + 1),
    )


def _take_slot(conn, entry_id: int, user_id: int) -> None:
    conn.execute(
        "UPDATE queue_entries SET user_id = ?, created_at = CURRENT_TIMESTAMP WHERE id = ?",
        (user_id, entry_id),
    )


def _place_user_at(conn, session_id: int, user_id: int, position: int) -> None:
    """Вставляє студента на вибране місце.
    Якщо вказано номер більше поточної довжини черги — проміжні місця стають вільними слотами.
    Якщо номер всередині зайнятого списку — наступні зсуваються вниз."""
    if not 1 <= position <= 100:
        raise BotError("Оберіть номер від 1 до 100.")

    entries = get_entries(conn, session_id)
    # Якщо вибране місце вже існує і воно пусте (зарезервоване)
    if position <= len(entries) and entries[position - 1]["user_id"] is None:
        _take_slot(conn, entries[position - 1]["id"], user_id)
        return

    # Якщо вибрали номер більший, ніж зараз є в черзі
    if position > len(entries):
        # Доповнюємо чергу пустими слотами до потрібного номера
        for p in range(len(entries) + 1, position):
            conn.execute(
                "INSERT INTO queue_entries (session_id, user_id, position) VALUES (?, NULL, ?)",
                (session_id, p),
            )
        # Вставляємо користувача на бажану позицію
        conn.execute(
            "INSERT INTO queue_entries (session_id, user_id, position) VALUES (?, ?, ?)",
            (session_id, user_id, position),
        )
        return

    # Якщо вклинюємося перед кимось всередині списку
    ids = [e["id"] for e in entries]
    new_id = conn.execute(
        "INSERT INTO queue_entries (session_id, user_id, position) VALUES (?, ?, NULL)",
        (session_id, user_id),
    ).lastrowid
    ids.insert(position - 1, new_id)
    _set_order(conn, session_id, ids)



def _own_active_entry(conn, telegram_id: int, session_id: int):
    """Студент працює лише з власним записом (шукаємо за його Telegram ID)."""
    entry = conn.execute(
        """SELECT e.* FROM queue_entries e JOIN users u ON u.id = e.user_id
           WHERE e.session_id = ? AND u.telegram_id = ? AND e.status = 'active'""",
        (session_id, telegram_id),
    ).fetchone()
    if entry is None:
        raise BotError("Ви не записані в цю чергу.")
    return entry


def leave_queue(conn: sqlite3.Connection, telegram_id: int, session_id: int) -> None:
    with transaction(conn):
        _close_entry(conn, _own_active_entry(conn, telegram_id, session_id), "left")


def create_queue(conn: sqlite3.Connection, telegram_id: int, subject_id: int,
                 teacher_id: int, lesson_title: str = "") -> int:
    """Будь-який зареєстрований студент може відкрити чергу."""
    with transaction(conn):
        if conn.execute("SELECT 1 FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone() is None:
            raise BotError("Спочатку зареєструйтесь: /start")
        return _insert_session(conn, subject_id, teacher_id, lesson_title.strip())


def finish_own_entry(conn: sqlite3.Connection, telegram_id: int, session_id: int,
                     passed: bool) -> int | None:
    """«Здав» — запис стає completed і зникає з черги.
    «Не здав» — студент переходить в кінець черги (повертає нове місце)."""
    with transaction(conn):
        entry = _own_active_entry(conn, telegram_id, session_id)
        _require_session(conn, session_id)
        if passed:
            _close_entry(conn, entry, "completed")
            return None
        ids = _ordered_ids(conn, session_id)
        ids.remove(entry["id"])
        ids.append(entry["id"])
        _set_order(conn, session_id, ids)
        return len(ids)


def swap_entries(conn, entry_id_1: int, entry_id_2: int) -> tuple[int, int]:
    """Міняє двох студентів місцями в черзі. Повертає (нове_місце_1, нове_місце_2)."""
    with transaction(conn):
        entry_1 = _require_active_entry(conn, entry_id_1)
        entry_2 = _require_active_entry(conn, entry_id_2)

        if entry_1["session_id"] != entry_2["session_id"]:
            raise BotError("Студенти знаходяться в різних чергах.")
        if entry_1["id"] == entry_2["id"]:
            raise BotError("Неможливо обмінятися з самим собою.")

        pos_1, pos_2 = entry_1["position"], entry_2["position"]

        # Тимчасово знімаємо позицію для уникнення порушення унікального індексу
        conn.execute("UPDATE queue_entries SET position = NULL WHERE id IN (?, ?)", (entry_1["id"], entry_2["id"]))
        conn.execute("UPDATE queue_entries SET position = ? WHERE id = ?", (pos_2, entry_1["id"]))
        conn.execute("UPDATE queue_entries SET position = ? WHERE id = ?", (pos_1, entry_2["id"]))

        return pos_2, pos_1


# ---------- Адмін: керування чергами ----------

def create_session(conn, actor_id: int, subject_id: int, teacher_id: int, lesson_title: str = ""):
    with transaction(conn):
        require_admin(conn, actor_id)
        session_id = _insert_session(conn, subject_id, teacher_id, lesson_title.strip())
        log_action(conn, actor_id, "create_session", str(session_id))
    return session_id


def add_student_by_admin(conn, actor_id: int, session_id: int, user_id: int, position: int | None = None) -> int:
    """Додає студента в чергу від імені старости."""
    with transaction(conn):
        require_admin(conn, actor_id)
        session = _require_session(conn, session_id)
        user = conn.execute("SELECT is_blocked FROM users WHERE id = ?", (user_id,)).fetchone()
        if user and user.get("is_blocked"):
            raise BotError("Цього студента заблоковано адміністратором.")
        # Перевіряємо чи студент уже є в черзі
        exists = conn.execute(
            "SELECT 1 FROM queue_entries WHERE session_id = ? AND user_id = ? AND status = 'active'",
            (session_id, user_id),
        ).fetchone()
        if exists:
            raise BotError("Цей студент уже є в цій черзі.")

        if position is None:
            _place_user(conn, session_id, user_id)
        else:
            _place_user_at(conn, session_id, user_id, position)
        log_action(conn, actor_id, "admin_add_student", f"user={user_id} session={session_id}")
    
    row = conn.execute(
        "SELECT position FROM queue_entries WHERE session_id = ? AND user_id = ? AND status = 'active'",
        (session_id, user_id)
    ).fetchone()
    return row["position"] if row else len(get_entries(conn, session_id))



def _insert_session(conn, subject_id: int, teacher_id: int, lesson_title: str) -> int:
    try:
        cursor = conn.execute(
            "INSERT INTO queue_sessions (subject_id, teacher_id, lesson_title) VALUES (?, ?, ?)",
            (subject_id, teacher_id, lesson_title),
        )
    except sqlite3.IntegrityError:
        raise BotError("Для цього предмета й викладача вже є незавершена черга.")
    return cursor.lastrowid


def set_signup_open(conn, actor_id: int, session_id: int, is_open: bool) -> None:
    with transaction(conn):
        require_admin(conn, actor_id)
        _require_session(conn, session_id)
        conn.execute(
            "UPDATE queue_sessions SET status = ? WHERE id = ?",
            ("open" if is_open else "closed", session_id),
        )
        log_action(conn, actor_id, "open_signup" if is_open else "close_signup", str(session_id))


# ---------- Адмін: зміна порядку ----------

def move_entry(conn, actor_id: int, entry_id: int, new_position: int) -> None:
    with transaction(conn):
        require_admin(conn, actor_id)
        entry = _require_active_entry(conn, entry_id)
        session_id = entry["session_id"]
        if not 1 <= new_position <= 100:
            raise BotError("Позиція має бути від 1 до 100.")
        
        entries = get_entries(conn, session_id)
        # Якщо нова позиція більша за поточну довжину черги, доповнюємо пустими слотами
        if new_position > len(entries):
            for p in range(len(entries) + 1, new_position):
                conn.execute(
                    "INSERT INTO queue_entries (session_id, user_id, position) VALUES (?, NULL, ?)",
                    (session_id, p),
                )
            ids = _ordered_ids(conn, session_id)
            ids.remove(entry_id)
            ids.append(entry_id)
        else:
            ids = _ordered_ids(conn, session_id)
            ids.remove(entry_id)
            ids.insert(new_position - 1, entry_id)

        _set_order(conn, session_id, ids)
        log_action(conn, actor_id, "move_entry", f"{entry_id} -> {new_position}")



def move_up(conn, actor_id: int, entry_id: int) -> None:
    entry = _current_entry(conn, entry_id)
    move_entry(conn, actor_id, entry_id, max(1, entry["position"] - 1))


def move_down(conn, actor_id: int, entry_id: int) -> None:
    entry = _current_entry(conn, entry_id)
    total = len(get_entries(conn, entry["session_id"]))
    move_entry(conn, actor_id, entry_id, min(total, entry["position"] + 1))


def _current_entry(conn, entry_id: int):
    entry = get_entry(conn, entry_id)
    if entry is None or entry["status"] != "active":
        raise BotError("Запис не знайдено або він уже неактивний.")
    return entry


def mark_done(conn, actor_id: int, entry_id: int) -> None:
    with transaction(conn):
        require_admin(conn, actor_id)
        entry = _require_active_entry(conn, entry_id)
        if entry["user_id"] is None:
            raise BotError("Це вільне місце, а не студент.")
        _close_entry(conn, entry, "completed")
        log_action(conn, actor_id, "mark_done", str(entry_id))


def remove_entry(conn, actor_id: int, entry_id: int) -> None:
    with transaction(conn):
        require_admin(conn, actor_id)
        entry = _require_active_entry(conn, entry_id)
        _close_entry(conn, entry, "removed")
        log_action(conn, actor_id, "remove_entry", str(entry_id))


def remove_user_from_all_queues(conn, actor_id: int, user_id: int) -> None:
    """Видаляє заблокованого студента з усіх активних черг."""
    with transaction(conn):
        require_admin(conn, actor_id)
        rows = conn.execute(
            "SELECT id FROM queue_entries WHERE user_id = ? AND status = 'active'",
            (user_id,),
        ).fetchall()
        for r in rows:
            entry = get_entry(conn, r["id"])
            if entry and entry["status"] == "active":
                _close_entry(conn, entry, "removed")
                log_action(conn, actor_id, "admin_block_remove_entry", str(r["id"]))


# ---------- Адмін: завершення заняття ----------

def finish_session(conn, actor_id: int, session_id: int, create_new: bool = False,
                   carry: dict[int, int] | None = None) -> int | None:
    """Архівує чергу. Усе в одній транзакції: або все виконається, або нічого.

    carry: {entry_id: позиція в новій черзі}. Перенесені студенти ставляться
    саме на ці позиції, проміжні місця резервуються для нових студентів.
    Повертає id нової черги (або None, якщо створювати її не треба)."""
    carry = carry or {}
    with transaction(conn):
        require_admin(conn, actor_id)
        session = _require_session(conn, session_id)
        if carry and not create_new:
            raise BotError("Перенесення можливе лише разом зі створенням нової черги.")
        entries = _validate_carry(conn, session_id, carry)
        _archive(conn, session_id)
        new_id = None
        if create_new:
            new_id = _insert_session(
                conn, session["subject_id"], session["teacher_id"], session["lesson_title"]
            )
            _fill_new_queue(conn, new_id, entries, carry)
        log_action(conn, actor_id, "finish_session", f"{session_id} -> {new_id}")
    return new_id


def _validate_carry(conn, session_id: int, carry: dict[int, int]) -> dict[int, sqlite3.Row]:
    entries = {}
    for entry_id in carry:
        entry = get_entry(conn, entry_id)
        if (entry is None or entry["session_id"] != session_id
                or entry["status"] != "active" or entry["user_id"] is None):
            raise BotError("Некоректний студент для перенесення.")
        entries[entry_id] = entry
    positions = list(carry.values())
    if len(set(positions)) != len(positions) or any(p < 1 for p in positions):
        raise BotError("Позиції мають бути унікальними й починатися з 1.")
    return entries


def _archive(conn, session_id: int) -> None:
    # Невиконані записи лишаються в історії зі статусом removed
    conn.execute(
        "UPDATE queue_entries SET status = 'removed', position = NULL, "
        "completed_at = CURRENT_TIMESTAMP WHERE session_id = ? AND status = 'active'",
        (session_id,),
    )
    conn.execute(
        "UPDATE queue_sessions SET status = 'archived', closed_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (session_id,),
    )


def _fill_new_queue(conn, new_id: int, entries: dict, carry: dict[int, int]) -> None:
    by_position = {position: entries[entry_id] for entry_id, position in carry.items()}
    for position in range(1, max(by_position, default=0) + 1):
        old = by_position.get(position)
        conn.execute(
            "INSERT INTO queue_entries (session_id, user_id, position) VALUES (?, ?, ?)",
            (new_id, old["user_id"] if old else None, position),
        )
    # Старі записи залишаємо в історії як перенесені
    for entry_id in carry:
        conn.execute(
            "UPDATE queue_entries SET status = 'transferred' WHERE id = ?", (entry_id,)
        )
