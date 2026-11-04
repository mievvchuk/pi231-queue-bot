import sqlite3
import json

from queue_bot.storage.database import transaction, PGConnectionWrapper
from queue_bot.services.admins import log_action, require_admin
from queue_bot.services.errors import BotError
from queue_bot.services.restrictions import require_available
from queue_bot.services.lab_estimates import validate_lab_count, validate_planned_minutes
from queue_bot.services import lab_catalog

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
        """SELECT e.id, e.user_id, e.position, e.ready_to_swap, e.swap_wish, e.lab_count, e.planned_minutes, e.labs_passed,
                  e.selected_labs, e.passed_labs, e.entry_revision, s.subject_id, s.queue_type, u.full_name, u.telegram_id, u.is_blocked
           FROM queue_entries e LEFT JOIN users u ON u.id = e.user_id
           JOIN queue_sessions s ON s.id = e.session_id
           WHERE e.session_id = ? AND e.status = 'active' ORDER BY e.position""",
        (session_id,),
    ).fetchall()


def get_history(conn: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT e.status, e.completed_at, e.lab_count, e.planned_minutes, e.labs_passed,
                  e.selected_labs, e.passed_labs, u.full_name FROM queue_entries e
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
    suffix = ' FOR UPDATE' if isinstance(conn, PGConnectionWrapper) else ''
    session = conn.execute('SELECT * FROM queue_sessions WHERE id = ?' + suffix, (session_id,)).fetchone()
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
    entry = get_entry(conn, entry_id)
    if entry is None or entry['status'] != 'active':
        raise BotError('Запис уже змінився. Оновіть чергу.')
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
        "UPDATE queue_entries SET status = ?, position = NULL, ready_to_swap = FALSE, swap_wish = NULL, completed_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (new_status, entry["id"]),
    )
    from queue_bot.services.attendance import end_turn
    end_turn(conn, entry)
    if new_status == 'removed':
        conn.execute('INSERT INTO queue_entries(session_id, user_id, position) VALUES (?, NULL, ?)',
                     (entry['session_id'], entry['position']))
    else:
        _set_order(conn, entry["session_id"], _ordered_ids(conn, entry["session_id"]))


# ---------- Дії студента ----------

def join_queue(conn: sqlite3.Connection, telegram_id: int, session_id: int,
               position: int | None = None, *, lab_count: int | None = None,
               planned_minutes: int | None = None, lab_numbers: list[int] | None = None) -> tuple[int, int]:
    """Повертає (місце студента, усього в черзі).
    position=None — у кінець; інакше студент вставляється на вибраний номер."""
    validate_lab_count(lab_count)
    validate_planned_minutes(planned_minutes)
    require_available(conn, telegram_id)
    with transaction(conn):
        from queue_bot.services.maintenance import get_maintenance_end_time, is_maintenance_active
        if is_maintenance_active(conn):
            end_t = get_maintenance_end_time(conn)
            raise BotError(f"🛠 Зараз тривають технічні роботи (орієнтовно до {end_t}). Запис до черги закрито.")

        session = _require_session(conn, session_id, statuses=("open", "closed"))
        if lab_numbers is not None:
            lab_numbers = lab_catalog.validate_numbers(session, lab_numbers)
            lab_catalog.validate_selection_time(session, lab_numbers, planned_minutes)
            lab_count = len(lab_numbers)
        user = conn.execute("SELECT id, is_blocked FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if user is None:
            raise BotError("Спочатку зареєструйтесь: /start")
        if bool(user["is_blocked"]):
            raise BotError("Вас заблоковано адміністратором.")
        if get_user_position(conn, telegram_id, session_id) is not None:
            raise BotError("Ви вже записані в цю чергу.")

        if position is None:
            _place_user(conn, session_id, user["id"])
        else:
            _place_user_at(conn, session_id, user["id"], position)
        conn.execute(
            "UPDATE queue_entries SET lab_count = ?, planned_minutes = ?, selected_labs = ?, "
            "passed_labs = '[]', labs_passed = 0, entry_revision = entry_revision + 1 "
            "WHERE session_id = ? AND user_id = ? AND status = 'active'",
            (lab_count, planned_minutes, json.dumps(lab_numbers) if lab_numbers is not None else None, session_id, user['id']),
        )
    place = get_user_position(conn, telegram_id, session_id)
    return place, len(get_entries(conn, session_id))


def _require_roster_user(conn, user_id):
    lock = ' FOR UPDATE' if isinstance(conn, PGConnectionWrapper) else ''
    if not conn.execute('SELECT id FROM users WHERE id = ? AND deleted_at IS NULL' + lock, (user_id,)).fetchone():
        raise BotError('Користувача немає у списку групи. Зареєструйтесь знову через /start.')


def _place_user(conn, session_id: int, user_id: int) -> None:
    _require_roster_user(conn, user_id)
    # Завжди додаємо студента в кінець черги (навіть якщо всередині є вільні місця)
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
        "UPDATE queue_entries SET user_id = ?, lab_count = NULL, planned_minutes = NULL, labs_passed = 0, "
        "selected_labs = NULL, passed_labs = '[]', entry_revision = entry_revision + 1, "
        "ready_to_swap = FALSE, swap_wish = NULL, created_at = CURRENT_TIMESTAMP WHERE id = ?",
        (user_id, entry_id),
    )


def are_first_10_occupied(conn: sqlite3.Connection, session_id: int) -> bool:
    """Перевіряє, чи перші 10 місць черги повністю зайняті реальними студентами."""
    entries = get_entries(conn, session_id)
    if len(entries) < 10:
        return False
    return all(e["user_id"] is not None for e in entries[:10])


def has_free_slots(conn: sqlite3.Connection, session_id: int) -> bool:
    """Перевіряє, чи є вільні місця (user_id IS NULL) в черзі."""
    entries = get_entries(conn, session_id)
    return any(e["user_id"] is None for e in entries)


def _place_user_at(conn, session_id: int, user_id: int, position: int, allow_shift: bool = False) -> None:
    """Students may take any free position from 1 to 100; only admins may shift."""
    _require_roster_user(conn, user_id)
    if type(position) is not int or not 1 <= position <= 100:
        raise BotError('Оберіть вільний номер від 1 до 100.')

    entries = get_entries(conn, session_id)
    # Якщо вибране місце вже існує і воно пусте (зарезервоване / вільне)
    if position <= len(entries) and entries[position - 1]["user_id"] is None:
        _take_slot(conn, entries[position - 1]["id"], user_id)
        return

    # Якщо вибране місце вже зайняте студентом
    if position <= len(entries):
        if not allow_shift:
            raise BotError(f"Місце №{position} уже зайняте. Оберіть інший вільний номер.")
        ids = [e["id"] for e in entries]
        new_id = conn.execute(
            "INSERT INTO queue_entries (session_id, user_id, position) VALUES (?, ?, NULL)",
            (session_id, user_id),
        ).lastrowid
        ids.insert(position - 1, new_id)
        _set_order(conn, session_id, ids)
        return

    # Якщо вибрали номер більший, ніж зараз є в черзі
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



def _own_active_entry(conn, telegram_id: int, session_id: int):
    """Студент працює лише з власним записом (шукаємо за його Telegram ID)."""
    _require_session(conn, session_id)
    require_available(conn, telegram_id)
    entry = conn.execute(
        """SELECT e.* FROM queue_entries e JOIN users u ON u.id = e.user_id
           WHERE e.session_id = ? AND u.telegram_id = ? AND e.status = 'active'""",
        (session_id, telegram_id),
    ).fetchone()
    if entry is None:
        raise BotError("Ви не записані в цю чергу.")
    return entry


def _trim_trailing_empty_slots(conn, session_id: int) -> None:
    """Видаляє порожні місця (user_id IS NULL) у кінці черги після останнього реального студента."""
    max_student_pos = conn.execute(
        """SELECT COALESCE(MAX(position), 0) FROM queue_entries
           WHERE session_id = ? AND status = 'active' AND user_id IS NOT NULL""",
        (session_id,),
    ).fetchone()[0]
    conn.execute(
        """DELETE FROM queue_entries
           WHERE session_id = ? AND status = 'active' AND user_id IS NULL AND position > ?""",
        (session_id, max_student_pos),
    )


def set_own_lab_count(conn, telegram_id: int, session_id: int, lab_count: int | None,
                      *, planned_minutes: int | None = None) -> None:
    validate_lab_count(lab_count)
    validate_planned_minutes(planned_minutes)
    with transaction(conn):
        _require_session(conn, session_id)
        entry = _own_active_entry(conn, telegram_id, session_id)
        if entry['selected_labs'] is not None:
            raise BotError('Для цього запису змінюйте конкретні номери лабораторних.')
        if planned_minutes is None:
            conn.execute('UPDATE queue_entries SET lab_count = ? WHERE id = ?', (lab_count, entry['id']))
        else:
            conn.execute('UPDATE queue_entries SET lab_count = ?, planned_minutes = ? WHERE id = ?',
                         (lab_count, planned_minutes, entry['id']))


def set_own_planned_minutes(conn, telegram_id: int, session_id: int, minutes: int,
                            *, expected_entry_id=None, expected_revision=None) -> None:
    validate_planned_minutes(minutes, allow_unknown=False)
    with transaction(conn):
        session = _require_session(conn, session_id)
        entry = _own_active_entry(conn, telegram_id, session_id)
        _check_revision(entry, expected_entry_id, expected_revision)
        numbers = lab_catalog.read_numbers(entry['selected_labs'])
        if numbers:
            lab_catalog.validate_selection_time(session, numbers, minutes)
        conn.execute('UPDATE queue_entries SET planned_minutes = ?, entry_revision = entry_revision + 1 WHERE id = ?', (minutes, entry['id']))


def _check_revision(entry, expected_entry_id, expected_revision):
    if ((expected_entry_id is not None and entry['id'] != expected_entry_id) or
            (expected_revision is not None and entry['entry_revision'] != expected_revision)):
        raise BotError('Запис уже змінився. Відкрийте дію ще раз.')


def set_own_lab_selection(conn, telegram_id, session_id, numbers, *, planned_minutes=None,
                          expected_entry_id=None, expected_revision=None):
    with transaction(conn):
        session = _require_session(conn, session_id)
        entry = _own_active_entry(conn, telegram_id, session_id)
        _check_revision(entry, expected_entry_id, expected_revision)
        numbers = lab_catalog.validate_numbers(session, numbers)
        if set(numbers) & set(lab_catalog.read_numbers(entry['passed_labs']) or []):
            raise BotError('Ці лабораторні вже здані в цьому записі.')
        minutes = planned_minutes if planned_minutes is not None else entry['planned_minutes']
        lab_catalog.validate_selection_time(session, numbers, minutes)
        conn.execute('UPDATE queue_entries SET selected_labs = ?, lab_count = ?, planned_minutes = ?, '
                     'entry_revision = entry_revision + 1 WHERE id = ?',
                     (json.dumps(numbers), len(numbers), minutes, entry['id']))


def record_own_passed_lab_numbers(conn, telegram_id, session_id, numbers, *, entry_id, expected_revision):
    with transaction(conn):
        session = _require_session(conn, session_id)
        entry = _own_active_entry(conn, telegram_id, session_id)
        _check_revision(entry, entry_id, expected_revision)
        from queue_bot.services.attendance import require_completion
        require_completion(conn, entry)
        pending = lab_catalog.read_numbers(entry['selected_labs'])
        numbers = lab_catalog.validate_numbers(session, numbers)
        if not pending or not set(numbers).issubset(pending):
            raise BotError('Оберіть лише лабораторні з вашого поточного запису.')
        remaining = sorted(set(pending) - set(numbers))
        passed = sorted(set(lab_catalog.read_numbers(entry['passed_labs']) or []) | set(numbers))
        minutes = entry['planned_minutes']
        if remaining and minutes is not None:
            minutes = min(minutes, lab_catalog.selection_limit(session, remaining))
        conn.execute('UPDATE queue_entries SET selected_labs = ?, passed_labs = ?, lab_count = ?, '
                     'labs_passed = labs_passed + ?, planned_minutes = ?, entry_revision = entry_revision + 1 WHERE id = ?',
                     (json.dumps(remaining), json.dumps(passed), len(remaining) or None, len(numbers), minutes, entry['id']))
        if not remaining:
            _close_entry(conn, entry, 'completed')
        return remaining


def record_own_passed_labs(conn, telegram_id: int, session_id: int, passed_count: int | None,
                           *, entry_id: int, expected_count: int | None) -> int:
    """Return remaining labs; zero means finished. Partial completion never moves the entry."""
    validate_lab_count(passed_count)
    with transaction(conn):
        _require_session(conn, session_id)
        entry = _own_active_entry(conn, telegram_id, session_id)
        from queue_bot.services.attendance import require_completion
        require_completion(conn, entry)
        if entry['selected_labs'] is not None:
            raise BotError('Для цього запису оберіть конкретні здані лабораторні.')
        remaining = entry['lab_count']
        if entry['id'] != entry_id or remaining != expected_count:
            raise BotError('Запис або кількість уже змінилися. Натисніть «✅ Здав» ще раз.')
        if remaining is not None and (passed_count is None or passed_count > remaining):
            raise BotError(f'Можна вказати від 1 до {remaining} зданих лабораторних.')
        if passed_count is not None:
            conn.execute('UPDATE queue_entries SET labs_passed = labs_passed + ? WHERE id = ?',
                         (passed_count, entry['id']))
        if remaining is not None and passed_count < remaining:
            remaining -= passed_count
            conn.execute('UPDATE queue_entries SET lab_count = ? WHERE id = ?', (remaining, entry['id']))
            return remaining
        _close_entry(conn, entry, 'completed')
        return 0


def leave_queue(conn: sqlite3.Connection, telegram_id: int, session_id: int) -> None:
    """Leave an empty numbered slot, including when the last student leaves."""
    with transaction(conn):
        entry = _own_active_entry(conn, telegram_id, session_id)
        entry_session_id = entry["session_id"]

        # Фіксуємо вихід в історії
        conn.execute(
            """INSERT INTO queue_entries (session_id, user_id, position, status, completed_at, lab_count, planned_minutes, labs_passed,
                                          selected_labs, passed_labs)
               VALUES (?, ?, NULL, 'left', CURRENT_TIMESTAMP, ?, ?, ?, ?, ?)""",
            (entry_session_id, entry["user_id"], entry['lab_count'], entry['planned_minutes'], entry['labs_passed'],
             entry['selected_labs'], entry['passed_labs']),
        )

        from queue_bot.services.attendance import end_turn
        end_turn(conn, entry)
        conn.execute(
            """UPDATE queue_entries
               SET user_id = NULL, lab_count = NULL, planned_minutes = NULL, labs_passed = 0,
                   selected_labs = NULL, passed_labs = '[]', entry_revision = entry_revision + 1,
                   ready_to_swap = FALSE, swap_wish = NULL
               WHERE id = ?""", (entry['id'],))


def create_queue(conn: sqlite3.Connection, telegram_id: int, subject_id: int,
                 teacher_id: int, lesson_title: str = "", queue_type: str = "lesson") -> int:
    """Будь-який зареєстрований студент може відкрити чергу."""
    require_available(conn, telegram_id)
    with transaction(conn):
        from queue_bot.services.maintenance import get_maintenance_end_time, is_maintenance_active
        if is_maintenance_active(conn):
            end_t = get_maintenance_end_time(conn)
            raise BotError(f"🛠 Зараз тривають технічні роботи (орієнтовно до {end_t}). Відкриття нових черг тимчасово заблоковано.")

        if conn.execute("SELECT 1 FROM users WHERE telegram_id = ? AND deleted_at IS NULL", (telegram_id,)).fetchone() is None:
            raise BotError("Спочатку зареєструйтесь: /start")
        return _insert_session(conn, subject_id, teacher_id, lesson_title.strip(), queue_type=queue_type)


def finish_own_entry(conn: sqlite3.Connection, telegram_id: int, session_id: int,
                     passed: bool) -> int | None:
    """«Здав» — запис стає completed і зникає з черги.
    «Не здав» — студент переходить в кінець черги (повертає нове місце)."""
    with transaction(conn):
        entry = _own_active_entry(conn, telegram_id, session_id)
        _require_session(conn, session_id)
        from queue_bot.services.attendance import require_completion, end_turn
        require_completion(conn, entry)
        if passed:
            _close_entry(conn, entry, "completed")
            return None
        end_turn(conn, entry)
        conn.execute('UPDATE queue_entries SET entry_revision = entry_revision + 1 WHERE id = ?', (entry['id'],))
        ids = _ordered_ids(conn, session_id)
        ids.remove(entry["id"])
        ids.append(entry["id"])
        _set_order(conn, session_id, ids)
        conn.execute("UPDATE queue_entries SET ready_to_swap = FALSE, swap_wish = NULL WHERE id = ?", (entry["id"],))
        return len(ids)


def swap_entries(conn, entry_id_1: int, entry_id_2: int) -> tuple[int, int]:
    """Міняє двох студентів місцями в черзі. Повертає (нове_місце_1, нове_місце_2)."""
    with transaction(conn):
        return _swap_entries_in_transaction(conn, entry_id_1, entry_id_2)


def _swap_entries_in_transaction(conn, entry_id_1, entry_id_2):
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
    conn.execute("UPDATE queue_entries SET ready_to_swap = FALSE, swap_wish = NULL WHERE id IN (?, ?)", (entry_1["id"], entry_2["id"]))

    return pos_2, pos_1


def set_swap_wish(conn: sqlite3.Connection, telegram_id: int, session_id: int, wish: str | None) -> str | None:
    """Встановлює або перемикає бажання обміну: 'higher', 'lower' або None.
    Якщо поточне бажання вже дорівнює wish, воно скидається (None).
    Повертає новий статус бажання ('higher', 'lower' або None)."""
    with transaction(conn):
        entry = _own_active_entry(conn, telegram_id, session_id)
        current = entry["swap_wish"] if "swap_wish" in entry.keys() else None
        new_val = None if current == wish else wish
        conn.execute(
            "UPDATE queue_entries SET swap_wish = ?, ready_to_swap = ? WHERE id = ?",
            (new_val, bool(new_val), entry["id"]),
        )
        return new_val


def get_swap_wish(conn: sqlite3.Connection, telegram_id: int, session_id: int) -> str | None:
    """Повертає поточне бажання обміну студента ('higher', 'lower' або None)."""
    row = conn.execute(
        """SELECT e.swap_wish FROM queue_entries e JOIN users u ON u.id = e.user_id
           WHERE e.session_id = ? AND u.telegram_id = ? AND e.status = 'active'""",
        (session_id, telegram_id),
    ).fetchone()
    if not row:
        return None
    return row["swap_wish"]


def toggle_ready_to_swap(conn: sqlite3.Connection, telegram_id: int, session_id: int) -> bool:
    """Перемикає статус готовності студента до обміну місцями.
    Повертає новий статус (True, якщо готовий)."""
    with transaction(conn):
        entry = _own_active_entry(conn, telegram_id, session_id)
        current = bool(entry["ready_to_swap"]) if "ready_to_swap" in entry.keys() and entry["ready_to_swap"] is not None else False
        new_val = not current
        wish_val = entry["swap_wish"] if "swap_wish" in entry.keys() else None
        conn.execute(
            "UPDATE queue_entries SET ready_to_swap = ?, swap_wish = ? WHERE id = ?",
            (new_val, None if not new_val else wish_val, entry["id"]),
        )
        return new_val


def is_ready_to_swap(conn: sqlite3.Connection, telegram_id: int, session_id: int) -> bool:
    """Перевіряє, чи активний статус готовності до обміну в користувача."""
    wish = get_swap_wish(conn, telegram_id, session_id)
    if wish:
        return True
    row = conn.execute(
        """SELECT e.ready_to_swap FROM queue_entries e JOIN users u ON u.id = e.user_id
           WHERE e.session_id = ? AND u.telegram_id = ? AND e.status = 'active'""",
        (session_id, telegram_id),
    ).fetchone()
    if not row:
        return False
    return bool(row["ready_to_swap"]) if row["ready_to_swap"] is not None else False


# ---------- Адмін: керування чергами ----------

def create_session(conn, actor_id: int, subject_id: int, teacher_id: int, lesson_title: str = "", queue_type: str = "lesson"):
    with transaction(conn):
        require_admin(conn, actor_id)
        session_id = _insert_session(conn, subject_id, teacher_id, lesson_title.strip(), queue_type=queue_type)
        log_action(conn, actor_id, "create_session", str(session_id))
    return session_id


def add_student_by_admin(conn, actor_id: int, session_id: int, user_id: int, position: int | None = None) -> int:
    """Додає студента в чергу від імені старости."""
    with transaction(conn):
        require_admin(conn, actor_id)
        session = _require_session(conn, session_id)
        user = conn.execute("SELECT telegram_id FROM users WHERE id = ?", (user_id,)).fetchone()
        if user:
            require_available(conn, user['telegram_id'])
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
            _place_user_at(conn, session_id, user_id, position, allow_shift=True)
        log_action(conn, actor_id, "admin_add_student", f"user={user_id} session={session_id}")
    
    row = conn.execute(
        "SELECT position FROM queue_entries WHERE session_id = ? AND user_id = ? AND status = 'active'",
        (session_id, user_id)
    ).fetchone()
    return row["position"] if row else len(get_entries(conn, session_id))



def add_students_by_admin(conn, actor_id: int, session_id: int, names: list[str]) -> int:
    """Add a validated batch atomically, including newly created placeholders."""
    from queue_bot.services import users
    if not isinstance(names, list) or not 1 <= len(names) <= 100 or any(not isinstance(name, str) for name in names):
        raise BotError('Вкажіть від 1 до 100 імен.')
    clean = [users.validate_full_name(name) for name in names]
    if len({name.casefold() for name in clean}) != len(clean):
        raise BotError('Імена не повинні повторюватися.')
    with transaction(conn):
        require_admin(conn, actor_id)
        _require_session(conn, session_id)
        for name in clean:
            user = users.get_or_create_user_by_name(conn, name)
            require_available(conn, user['telegram_id'])
            if conn.execute("SELECT 1 FROM queue_entries WHERE session_id = ? AND user_id = ? AND status = 'active'", (session_id, user['id'])).fetchone():
                raise BotError(f'{name} вже в цій черзі.')
            _place_user(conn, session_id, user['id'])
            log_action(conn, actor_id, 'admin_add_student', f"user={user['id']} session={session_id}")
    return len(clean)


def find_active_session_by_subject_teacher(conn, subject_id: int, teacher_id: int, queue_type: str | None = None):
    """Повертає активну сесію черги (open/closed) для вказаного предмета, викладача і типу або None."""
    if queue_type is not None:
        return conn.execute(
            "SELECT * FROM queue_sessions WHERE subject_id = ? AND teacher_id = ? AND queue_type = ? AND status IN ('open', 'closed')",
            (subject_id, teacher_id, queue_type),
        ).fetchone()
    return conn.execute(
        "SELECT * FROM queue_sessions WHERE subject_id = ? AND teacher_id = ? AND status IN ('open', 'closed')",
        (subject_id, teacher_id),
    ).fetchone()


def _insert_session(conn, subject_id: int, teacher_id: int, lesson_title: str, queue_type: str = "lesson") -> int:
    existing = find_active_session_by_subject_teacher(conn, subject_id, teacher_id, queue_type)
    if existing:
        type_label = "на консультацію" if queue_type == "consultation" else "на пару"
        status_text = "відкрита для запису" if existing["status"] == "open" else "закрита для запису"
        raise BotError(
            f"⚠️ Черга {type_label} для цього предмета й викладача вже створена ({status_text})!\n"
            "Ви можете переглянути її або записатися в меню «📋 Усі черги»."
        )

    try:
        cursor = conn.execute(
            "INSERT INTO queue_sessions (subject_id, teacher_id, lesson_title, queue_type) VALUES (?, ?, ?, ?)",
            (subject_id, teacher_id, lesson_title, queue_type),
        )
    except Exception as e:
        err_name = type(e).__name__
        err_msg = str(e).lower()
        if "integrityerror" in err_name.lower() or "uniqueviolation" in err_name.lower() or "unique" in err_msg:
            type_label = "на консультацію" if queue_type == "consultation" else "на пару"
            raise BotError(
                f"⚠️ Черга {type_label} для цього предмета й викладача вже створена!\n"
                "Ви можете знайти її в меню «📋 Усі черги»."
            )
        raise
    return cursor.lastrowid


# Legacy storage compatibility for archived-data imports and historical fixtures.
# Live Telegram/HTTP routes never expose signup toggles or session finalization.
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
                conn, session["subject_id"], session["teacher_id"], session["lesson_title"],
                queue_type=session['queue_type'],
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
            "INSERT INTO queue_entries (session_id, user_id, position, lab_count, planned_minutes, selected_labs) VALUES (?, ?, ?, ?, ?, ?)",
            (new_id, old["user_id"] if old else None, position, old['lab_count'] if old else None,
             old['planned_minutes'] if old else None, old['selected_labs'] if old else None),
        )
    # Старі записи залишаємо в історії як перенесені
    for entry_id in carry:
        conn.execute(
            "UPDATE queue_entries SET status = 'transferred' WHERE id = ?", (entry_id,)
        )


def get_subject_report(conn, subject_id: int) -> dict:
    """Формує детальну аналітику та звіт по конкретному предмету."""
    sessions = conn.execute(
        "SELECT * FROM queue_sessions WHERE subject_id = ? ORDER BY id ASC",
        (subject_id,)
    ).fetchall()

    session_ids = [s["id"] for s in sessions]
    if not session_ids:
        return {
            "sessions_count": 0,
            "active_session": None,
            "total_passed": 0,
            "students": [],
        }

    active_session = next((s for s in reversed(sessions) if s["status"] in ("open", "closed")), None)

    marks = ",".join("?" * len(session_ids))
    entries = conn.execute(
        f"""SELECT e.session_id, e.user_id, e.position, e.status, e.completed_at, u.full_name
            FROM queue_entries e
            JOIN users u ON u.id = e.user_id
            WHERE e.session_id IN ({marks})
            ORDER BY u.full_name ASC, e.id ASC""",
        session_ids
    ).fetchall()

    student_stats = {}
    total_passed = 0

    for e in entries:
        uid = e["user_id"]
        name = e["full_name"]
        if uid not in student_stats:
            student_stats[uid] = {
                "name": name,
                "passed_count": 0,
                "not_passed_count": 0,
                "current_position": None,
                "dates": [],
            }

        if e["status"] == "completed":
            student_stats[uid]["passed_count"] += 1
            total_passed += 1
            if e["completed_at"]:
                student_stats[uid]["dates"].append(str(e["completed_at"])[:16])
        elif e["status"] == "removed":
            student_stats[uid]["not_passed_count"] += 1

        if active_session and e["session_id"] == active_session["id"] and e["status"] == "active":
            student_stats[uid]["current_position"] = e["position"]

    sorted_students = sorted(
        student_stats.values(),
        key=lambda s: (-s["passed_count"], s["name"])
    )

    return {
        "sessions_count": len(sessions),
        "active_session": active_session,
        "total_passed": total_passed,
        "students": sorted_students,
    }


def get_global_statistics(conn, subjects: list[dict]) -> dict:
    """Формує комплексну статистику активності черг та успішності всієї групи."""
    all_sessions = conn.execute("SELECT * FROM queue_sessions ORDER BY id ASC").fetchall()
    total_sessions = len(all_sessions)
    active_sessions = sum(1 for s in all_sessions if s["status"] in ("open", "closed"))
    archived_sessions = sum(1 for s in all_sessions if s["status"] == "archived")

    sub_map = {s["id"]: s["name"] for s in subjects}
    lessons_count = 0
    consults_count = 0
    cw_count = 0
    for s in all_sessions:
        s_name = sub_map.get(s["subject_id"], "")
        q_type = s["queue_type"] if "queue_type" in s.keys() else "lesson"
        if "курсов" in s_name.lower():
            cw_count += 1
        elif q_type == "consultation":
            consults_count += 1
        else:
            lessons_count += 1

    entries = conn.execute(
        """SELECT e.session_id, e.user_id, e.status, s.subject_id, u.full_name
           FROM queue_entries e
           JOIN queue_sessions s ON s.id = e.session_id
           JOIN users u ON u.id = e.user_id
           WHERE e.user_id IS NOT NULL"""
    ).fetchall()

    total_passed = 0
    total_removed = 0
    total_left = 0
    total_active = 0

    student_totals: dict[int, dict] = {}
    subject_stats: dict[int, dict] = {
        s["id"]: {
            "id": s["id"],
            "name": s["name"],
            "sessions": sum(1 for sess in all_sessions if sess["subject_id"] == s["id"]),
            "passed": 0,
            "entries": 0,
        }
        for s in subjects
    }

    for e in entries:
        uid = e["user_id"]
        sub_id = e["subject_id"]
        st = e["status"]

        if uid not in student_totals:
            student_totals[uid] = {
                "user_id": uid,
                "name": e["full_name"],
                "passed": 0,
                "removed": 0,
                "left": 0,
                "by_subject": {},
            }

        if sub_id in subject_stats:
            subject_stats[sub_id]["entries"] += 1

        if st == "completed":
            total_passed += 1
            student_totals[uid]["passed"] += 1
            student_totals[uid]["by_subject"][sub_id] = student_totals[uid]["by_subject"].get(sub_id, 0) + 1
            if sub_id in subject_stats:
                subject_stats[sub_id]["passed"] += 1
        elif st == "removed":
            total_removed += 1
            student_totals[uid]["removed"] += 1
        elif st == "left":
            total_left += 1
            student_totals[uid]["left"] += 1
        elif st == "active":
            total_active += 1

    all_users = conn.execute("SELECT id, telegram_id, full_name FROM users ORDER BY lower(full_name) ASC").fetchall()
    registered_count = sum(1 for u in all_users if u["telegram_id"] and u["telegram_id"] > 0)
    placeholder_count = sum(1 for u in all_users if not u["telegram_id"] or u["telegram_id"] < 0)

    for u in all_users:
        if u["id"] not in student_totals:
            student_totals[u["id"]] = {
                "user_id": u["id"],
                "name": u["full_name"],
                "passed": 0,
                "removed": 0,
                "left": 0,
                "by_subject": {},
            }

    top_students = sorted(
        student_totals.values(),
        key=lambda x: (-x["passed"], x["name"].lower()),
    )

    sorted_subjects = sorted(
        subject_stats.values(),
        key=lambda x: (-x["passed"], -x["sessions"], x["name"]),
    )

    finished_attempts = total_passed + total_removed
    pass_rate = round((total_passed / finished_attempts) * 100) if finished_attempts > 0 else 0

    return {
        "total_sessions": total_sessions,
        "active_sessions": active_sessions,
        "archived_sessions": archived_sessions,
        "lessons_count": lessons_count,
        "consults_count": consults_count,
        "cw_count": cw_count,
        "total_passed": total_passed,
        "total_removed": total_removed,
        "total_left": total_left,
        "total_active": total_active,
        "pass_rate": pass_rate,
        "registered_users": registered_count,
        "placeholder_users": placeholder_count,
        "top_students": top_students,
        "subjects": sorted_subjects,
    }


def export_global_statistics_csv(conn, subjects: list[dict]) -> bytes:
    """Формує зведену CSV-таблицю успішності всіх студентів по всіх предметах."""
    stats = get_global_statistics(conn, subjects)
    sub_headers = [s["name"] for s in subjects]
    header_row = ["Прізвище та ім'я", "Усього здано", "Не здано / перенесено", "Виходів із черги"] + sub_headers

    rows = [";".join(header_row)]
    students_alpha = sorted(stats["top_students"], key=lambda x: x["name"].lower())

    for st in students_alpha:
        row_cells = [
            st["name"],
            str(st["passed"]),
            str(st["removed"]),
            str(st["left"]),
        ]
        for s in subjects:
            row_cells.append(str(st["by_subject"].get(s["id"], 0)))
        rows.append(";".join(row_cells))

    return "\r\n".join(rows).encode("utf-8-sig")

