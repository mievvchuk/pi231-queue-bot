import json


def load_subjects(path: str) -> list[dict]:
    """Завантажує subjects.json і перевіряє його структуру."""
    with open(path, encoding="utf-8") as file:
        subjects = json.load(file)
    validate_subjects(subjects)
    return subjects


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
