import base64
import json
import logging
import re
import aiohttp

import config
from services.errors import BotError

log = logging.getLogger(__name__)

# Словник розшифрування скорочень предметів
SUBJECT_ABBREVIATIONS = {
    "лмв": "Людино-машинна взаємодія",
    "кпз": "Конструювання програмного забезпечення",
    "мн": "Машинне навчання",
    "пі": "Програмування в Інтернет",
    "атпз": "Архітектура та проєкт ПЗ",
    "пікпс": "Проєкт інтерфейсів користувача ПС",
    "фв": "Фізичне виховання",
    "фіз": "Фізичне виховання",
    "філософія": "Філософія",
}

# Словник стандартних викладачів
TEACHER_EXPANSIONS = {
    "когут": "Когут В.С.",
    "барабаш": "Барабаш Д.С.",
    "кондратюк": "Кондратюк Є.С.",
    "щуцький": "Щуцький С.В.",
    "ковальчук": "Ковальчук С.О.",
    "атаман": "Атаман В.О.",
    "багас": "Багас Л.М.",
    "бельський": "Бельський А.В.",
    "жереб": "Жереб Д.В.",
}


def normalize_lesson_pair(raw_num, lessons_str: str = "") -> int:
    """Повертає першу пару з діапазону уроків або номер пари 1–5."""
    pairs = normalize_lesson_pairs(raw_num, lessons_str)
    return pairs[0]


def normalize_lesson_pairs(raw_num, lessons_str: str = "") -> list[int]:
    """Розгортає діапазони уроків, зокрема 7–10 → [4, 5]."""
    text = str(lessons_str or raw_num or "").strip()
    ranges = re.findall(r"(?<!\d)(\d{1,2})\s*[-–—−]\s*(\d{1,2})(?!\d)", text)
    pairs = set()
    for start, end in ranges:
        start, end = int(start), int(end)
        if 1 <= start <= end <= 10:
            pairs.update((lesson + 1) // 2 for lesson in range(start, end + 1))
    if pairs:
        return sorted(pairs)
    return [_normalize_single_pair(raw_num)]


def _normalize_single_pair(raw_num) -> int:
    try:
        val = int(raw_num)
        if val in (1, 2, 3, 4, 5):
            return val
        if val == 6:
            return 3
        if val in (7, 8):
            return 4
        if val in (9, 10):
            return 5
    except (ValueError, TypeError):
        pass
    return 1


def expand_subject(name: str) -> str:
    cleaned = name.strip()
    norm = cleaned.lower().replace(".", "").replace(" ", "")
    if norm in SUBJECT_ABBREVIATIONS:
        full = SUBJECT_ABBREVIATIONS[norm]
        return f"{full} ({cleaned})" if cleaned.upper() == cleaned else full
    return cleaned


def expand_teacher(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = name.strip()
    surname = cleaned.split()[0].lower()
    for key, full in TEACHER_EXPANSIONS.items():
        if key in surname:
            return full
    return cleaned


def group_matches(raw_group: str, target_group: str) -> bool:
    """Розрізняє ПІ-231 та КІ-231, підтримує спільні рядки ПІ-231,232."""
    def groups(text):
        text = str(text).lower().translate(str.maketrans({
            "п": "p", "і": "i", "к": "k", "м": "m", "т": "t", "е": "e", "а": "a",
        }))
        text = re.sub(r"\s+", "", text).replace("–", "-").replace("—", "-")
        prefix = ""
        found = set()
        for name, number in re.findall(r"([a-zа-яіїєґ]+)?-?(\d{3,4})(?!\d)", text):
            prefix = name or prefix
            found.add((prefix, number))
        return found
    return bool(groups(raw_group) & groups(target_group))


async def recognize_replacements_from_image(image_bytes: bytes, group_name: str = "ПІ-231", conn=None, *, allow_empty: bool = False) -> dict:
    """Розпізнає таблицю замін з фотографії через Groq або OpenAI API."""
    api_key = config.RECOGNITION_API_KEY
    if not api_key or config.RECOGNITION_PROVIDER == "disabled":
        raise BotError(
            "⚠️ Автоматичне розпізнавання фото наразі вимкнено (не налаштовано RECOGNITION_API_KEY в .env).\n"
            "Зверніться до адміністратора для активації ключа Groq або OpenAI."
        )

    try:
        from services.working_days import get_next_working_day_key
        next_work_date = get_next_working_day_key(conn)
    except Exception:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        now_dt = datetime.now(ZoneInfo(config.TIMEZONE)).date()
        next_work_date = (now_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    images = image_bytes if isinstance(image_bytes, list) else [image_bytes]
    prompt = (
        f"Проаналізуй фотографію таблиці замін занять для групи '{group_name}'.\n"
        "Знайди рядок, де в стовпчику 'Група' вказано саме цю групу (або схоже написання: ПІ-231, ПI-231, PI-231).\n\n"
        "Враховуй спільні рядки «ПІ-231,232»: вони стосуються обох груп. КІ-231 та інші спеціальності — інші групи.\n"
        "Об'єднана комірка групи поширюється на всі рядки до її нижньої межі: порожня комірка під ПІ-231 може бути продовженням цієї групи.\n"
        "Копіюй значення за стовпчиками: після № може бути прізвище відсутнього викладача, далі предмет заміни, викладач заміни, аудиторія.\n"
        "Прізвище відсутнього викладача (наприклад Щуцький) НЕ є oldSubject і НЕ є викладачем заміни.\n"
        "oldSubject заповнюй лише якщо на фото явно вказано попередній ПРЕДМЕТ; інакше null — його підставить бот із базового розкладу.\n"
        "ДУЖЕ ВАЖЛИВО щодо номерів пар:\n"
        "У стовпчику '№' зазначено номери уроків:\n"
        "- 1-2 це 1 пара (lessonNumber = 1)\n"
        "- 3-4 це 2 пара (lessonNumber = 2)\n"
        "- 5-6 це 3 пара (lessonNumber = 3)\n"
        "- 7-8 це 4 пара (lessonNumber = 4)\n"
        "- 9-10 це 5 пара (lessonNumber = 5)\n\n"
        "Збери ВСІ заміни групи до 5 пари включно: перевір усі рядки, продовження та об'єднані комірки.\n"
        "Якщо предмет займає кілька пар (наприклад 7-10), створи окремі записи для 4 та 5 пари з тими самими предметом, викладачем і аудиторією.\n"
        "Не пропускай 9-10 уроки, навіть якщо 5 пари немає в базовому розкладі.\n"
        "У полі 'lessonNumber' записуй саме НОМЕР ПАРИ (1, 2, 3, 4, 5).\n"
        "У полі 'lessons' записуй текст зі стовпчика № (наприклад '5-6').\n"
        "У полі 'newSubject' записуй назву предмета (стовпчик 'Предмет'). Скорочення розшифруй (наприклад ЛМВ -> Людино-машинна взаємодія).\n"
        f"Визнач дату з шапки таблиці у форматі YYYY-MM-DD (наприклад '16 жовтня 2026' -> '2026-10-16').\n"
        f"Якщо дату важко розібрати або вона відсутня, за замовчуванням це наступний робочий день: {next_work_date}.\n\n"
        "Поверни ТІЛЬКИ валідний JSON у форматі:\n"
        "{\n"
        '  "date": "YYYY-MM-DD",\n'
        '  "confidence": 0.95,\n'
        '  "replacements": [\n'
        '    {\n'
        f'      "group": "{group_name}",\n'
        '      "lessonNumber": 3,\n'
        '      "lessons": "5-6",\n'
        '      "newSubject": "Людино-машинна взаємодія (ЛМВ)",\n'
        '      "oldSubject": null,\n'
        '      "teacher": "Кондратюк",\n'
        '      "room": "231"\n'
        '    }\n'
        '  ]\n'
        '}\n'
        'Якщо для групи замін немає або рядок не знайдено, поверни replacements: [].'
    )

    prompt += (
        "\nУсі зображення належать одному допису: перевір кожну сторінку. "
        "Додай tableReadable: true лише якщо це чітка таблиця замін, яку можна перевірити повністю; "
        "інакше false. Додай noReplacements: true лише коли таблиці прочитані повністю "
        "і для цільової групи справді немає замін. Нечітке фото не означає відсутність замін."
    )

    if api_key.startswith("gsk_"):
        url = "https://api.groq.com/openai/v1/chat/completions"
        model_name = "qwen/qwen3.8-27b"
        provider_name = "Groq"
    else:
        url = "https://api.openai.com/v1/chat/completions"
        model_name = "gpt-4o-mini"
        provider_name = "OpenAI"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "radiant-hertz/1.0",
    }
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    *[{
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(img).decode("ascii")},
                    } for img in images],
                ],
            }
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=45)) as resp:
                if resp.status != 200:
                    err_body = await resp.text()
                    log.error(f"{provider_name} API error {resp.status}: {err_body}")
                    raise BotError(f"Помилка {provider_name} API ({resp.status}). Спробуйте пізніше або надішліть чіткіше фото.")
                data = await resp.json()
                content = data["choices"][0]["message"]["content"]
                result = json.loads(content)
    except BotError:
        raise
    except Exception as e:
        log.exception(f"Error calling {provider_name} API for recognition: {e}")
        raise BotError(f"Не вдалося обробити зображення: {e}")

    date_str = result.get("date")
    if allow_empty:
        from datetime import date
        try:
            date.fromisoformat(str(date_str))
        except ValueError:
            raise BotError("Не вдалося визначити дату таблиці. Потрібна ручна перевірка фото.")
    if not date_str or not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date_str).strip()):
        date_str = next_work_date

    confidence = float(result.get("confidence", 0.0))
    if confidence < 0.5:
        raise BotError("Низька впевненість розпізнавання. Будь ласка, надішліть більш чітке або рівне фото.")

    raw_replacements = result.get("replacements", [])
    from services import replacements, schedule
    schedule_data = schedule.load_schedule_data(config.SCHEDULE_PATH)
    current_group = group_name
    filtered = []
    for r in raw_replacements:
        grp = str(r.get("group") or "").strip()
        if grp:
            current_group = grp
        if group_matches(current_group, group_name):
            subj_raw = str(r.get("newSubject", "")).strip()
            if subj_raw:
                for pair_num in normalize_lesson_pairs(r.get("lessonNumber"), r.get("lessons", "")):
                    item = {
                        "date": date_str,
                        "group_name": group_name,
                        "lesson_number": pair_num,
                        "new_subject": expand_subject(subj_raw),
                        "old_subject": expand_subject(r["oldSubject"]) if r.get("oldSubject") else None,
                        "teacher": expand_teacher(r.get("teacher")),
                        "room": str(r.get("room")) if r.get("room") is not None else None,
                    }
                    replacements.fill_old_subject(conn, item, schedule_data)
                    if item not in filtered:
                        filtered.append(item)

    if not filtered and not (allow_empty and result.get("tableReadable") is True and result.get("noReplacements") is True and confidence >= 0.8 and not raw_replacements):
        raise BotError(f"Для групи {group_name} замін на фото не знайдено.")

    return {
        "date": date_str,
        "confidence": confidence,
        "replacements": filtered,
    }

