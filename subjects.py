import json
import uuid
from services.errors import BotError


def load_subjects(path: str, conn=None) -> list[dict]:
    """Завантажує subjects.json (та синхронізує з БД при наявності conn) і перевіряє його структуру."""
    with open(path, encoding="utf-8") as file:
        subjects = json.load(file)
    validate_subjects(subjects)

    if conn is not None:
        try:
            row = conn.execute("SELECT value FROM bot_state WHERE key = 'subjects_data'").fetchone()
            if row and row["value"]:
                db_subjects = json.loads(row["value"])
                if isinstance(db_subjects, list):
                    _merge_subjects_in_place(subjects, db_subjects)
                    validate_subjects(subjects)
            save_subjects(subjects, path=path, conn=conn)
        except Exception:
            pass

    return subjects


def _merge_subjects_in_place(target: list[dict], source: list[dict]) -> None:
    """Об'єднує предмети та викладачів із source у target без дублікатів."""
    for src_sub in source:
        existing_sub = next(
            (s for s in target if s["id"] == src_sub.get("id") or s["name"].lower() == src_sub.get("name", "").lower()),
            None,
        )
        if existing_sub is None:
            new_id = src_sub.get("id")
            if not new_id or any(s["id"] == new_id for s in target):
                new_id = max((s["id"] for s in target), default=0) + 1
            resolved_teachers = []
            for t in src_sub.get("teachers", []):
                rt = resolve_or_create_teacher(target, t.get("name", ""))
                if not any(x["id"] == rt["id"] for x in resolved_teachers):
                    resolved_teachers.append(rt)
            if resolved_teachers:
                target.append({"id": new_id, "name": src_sub["name"], "teachers": resolved_teachers})
        else:
            for t in src_sub.get("teachers", []):
                t_name = (t.get("name") or "").strip()
                if not t_name:
                    continue
                rt = resolve_or_create_teacher(target, t_name)
                if not any(x["id"] == rt["id"] for x in existing_sub["teachers"]):
                    existing_sub["teachers"].append(rt)


def save_subjects(subjects: list[dict], path: str | None = None, conn=None) -> None:
    """Валідує та зберігає предмети у файл subjects.json і таблицю bot_state."""
    validate_subjects(subjects)
    raw_json = json.dumps(subjects, ensure_ascii=False, indent=2)
    if path:
        try:
            with open(path, "w", encoding="utf-8") as file:
                file.write(raw_json + "\n")
        except Exception:
            pass
    if conn is not None:
        conn.execute(
            """INSERT INTO bot_state (key, value, updated_at)
               VALUES ('subjects_data', ?, CURRENT_TIMESTAMP)
               ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            (raw_json,),
        )


def validate_subjects(subjects: list[dict]) -> None:
    subject_ids = set()
    teacher_names = {}  # id викладача -> ім'я (один id = одна людина)
    for subject in subjects:
        if subject["id"] in subject_ids:
            raise ValueError(f"Duplicate subject id {subject['id']}")
        subject_ids.add(subject["id"])
        if not subject["teachers"]:
            raise ValueError(f"Subject {subject['name']} has no teachers")
        for teacher in subject["teachers"]:
            known = teacher_names.setdefault(teacher["id"], teacher["name"])
            if known != teacher["name"]:
                raise ValueError(f"Teacher id {teacher['id']} has two names")


def find_subject(subjects: list[dict], subject_id: int) -> dict | None:
    return next((s for s in subjects if s["id"] == subject_id), None)


def find_teacher(subject: dict, teacher_id: int) -> dict | None:
    return next((t for t in subject["teachers"] if t["id"] == teacher_id), None)


def list_all_teachers(subjects: list[dict]) -> list[dict]:
    """Повертає список усіх унікальних викладачів групи."""
    seen = {}
    for s in subjects:
        for t in s.get("teachers", []):
            if t["id"] not in seen:
                seen[t["id"]] = {"id": t["id"], "name": t["name"]}
    return [seen[k] for k in sorted(seen.keys())]


def resolve_or_create_teacher(subjects: list[dict], teacher_name: str) -> dict:
    """Знаходить існуючого викладача за ПІБ (щоб зберегти єдиний ID) або створює новий ID."""
    clean = (teacher_name or "").strip()
    if len(clean) < 3 or len(clean) > 60:
        raise BotError("ПІБ викладача має містити від 3 до 60 символів (наприклад: Барабаш Д.С.).")
    for s in subjects:
        for t in s.get("teachers", []):
            if t["name"].lower() == clean.lower():
                return {"id": t["id"], "name": t["name"]}
    max_id = max((t["id"] for s in subjects for t in s.get("teachers", [])), default=0)
    return {"id": max_id + 1, "name": clean}


def add_teacher_to_subject(
    conn,
    subjects: list[dict],
    subject_id: int,
    teacher_name: str,
    path: str | None = None,
) -> dict:
    """Прикріплює викладача до предмету (безпечно використовує існуючий ID або створює новий)."""
    sub = find_subject(subjects, subject_id)
    if not sub:
        raise BotError("Предмет не знайдено.")
    teacher = resolve_or_create_teacher(subjects, teacher_name)
    if any(t["id"] == teacher["id"] or t["name"].lower() == teacher["name"].lower() for t in sub["teachers"]):
        raise BotError(f"Викладач «{teacher['name']}» вже прикріплений до предмета «{sub['name']}».")
    sub["teachers"].append(teacher)
    save_subjects(subjects, path=path, conn=conn)
    return teacher


def add_new_subject(
    conn,
    subjects: list[dict],
    subject_name: str,
    teacher_name: str,
    path: str | None = None,
) -> dict:
    """Додає новий предмет із першим викладачем."""
    clean_sub = (subject_name or "").strip()
    if len(clean_sub) < 2 or len(clean_sub) > 80:
        raise BotError("Назва предмета має містити від 2 до 80 символів.")
    if any(s["name"].lower() == clean_sub.lower() for s in subjects):
        raise BotError(f"Предмет «{clean_sub}» вже існує.")
    teacher = resolve_or_create_teacher(subjects, teacher_name)
    new_id = max((s["id"] for s in subjects), default=0) + 1
    new_sub = {"id": new_id, "name": clean_sub, "teachers": [teacher]}
    subjects.append(new_sub)
    save_subjects(subjects, path=path, conn=conn)
    return new_sub


def rename_subject(
    conn,
    subjects: list[dict],
    subject_id: int,
    new_name: str,
    path: str | None = None,
) -> dict:
    """Перейменовує предмет (тільки для власника бота)."""
    clean_name = (new_name or "").strip()
    if len(clean_name) < 2 or len(clean_name) > 80:
        raise BotError("Назва предмета має містити від 2 до 80 символів.")
    sub = find_subject(subjects, subject_id)
    if not sub:
        raise BotError("Предмет не знайдено.")
    if any(s["id"] != subject_id and s["name"].lower() == clean_name.lower() for s in subjects):
        raise BotError(f"Предмет з назвою «{clean_name}» вже існує.")
    sub["name"] = clean_name
    save_subjects(subjects, path=path, conn=conn)
    return sub


def remove_teacher_from_subject(
    conn,
    subjects: list[dict],
    subject_id: int,
    teacher_id: int,
    path: str | None = None,
) -> dict:
    """Відкріплює викладача від предмету (тільки для власника бота)."""
    sub = find_subject(subjects, subject_id)
    if not sub:
        raise BotError("Предмет не знайдено.")
    if len(sub["teachers"]) <= 1:
        raise BotError("У предмета має залишитися щонайменше один викладач. Спочатку додайте іншого або видаліть увесь предмет.")
    teacher = find_teacher(sub, teacher_id)
    if not teacher:
        raise BotError("Викладача не знайдено у цьому предметі.")
    sub["teachers"] = [t for t in sub["teachers"] if t["id"] != teacher_id]
    save_subjects(subjects, path=path, conn=conn)
    return teacher


def delete_subject(
    conn,
    subjects: list[dict],
    subject_id: int,
    path: str | None = None,
) -> dict:
    """Видаляє предмет зі списку (тільки для власника бота)."""
    if len(subjects) <= 1:
        raise BotError("Не можна видалити останній предмет у боті.")
    sub = find_subject(subjects, subject_id)
    if not sub:
        raise BotError("Предмет не знайдено.")
    subjects.remove(sub)
    save_subjects(subjects, path=path, conn=conn)
    return sub


def _load_requests(conn) -> dict:
    try:
        row = conn.execute("SELECT value FROM bot_state WHERE key = 'subject_requests'").fetchone()
        if row and row["value"]:
            data = json.loads(row["value"])
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_requests(conn, data: dict) -> None:
    raw = json.dumps(data, ensure_ascii=False)
    conn.execute(
        """INSERT INTO bot_state (key, value, updated_at)
           VALUES ('subject_requests', ?, CURRENT_TIMESTAMP)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
        (raw,),
    )


def create_subject_request(
    conn,
    user_telegram_id: int,
    user_name: str,
    req_type: str,
    subject_id: int | None,
    subject_name: str,
    teacher_name: str,
) -> dict:
    """Створює заявку від студента на додавання викладача або предмета."""
    requests = _load_requests(conn)
    req_id = str(uuid.uuid4())[:8]
    item = {
        "id": req_id,
        "user_telegram_id": user_telegram_id,
        "user_name": user_name,
        "type": req_type,  # 'add_teacher' або 'add_subject'
        "subject_id": subject_id,
        "subject_name": subject_name.strip(),
        "teacher_name": teacher_name.strip(),
        "status": "pending",
    }
    requests[req_id] = item
    _save_requests(conn, requests)
    return item


def get_subject_request(conn, req_id: str) -> dict | None:
    return _load_requests(conn).get(req_id)


def update_subject_request_status(conn, req_id: str, status: str) -> dict | None:
    requests = _load_requests(conn)
    item = requests.get(req_id)
    if not item:
        return None
    item["status"] = status
    _save_requests(conn, requests)
    return item

