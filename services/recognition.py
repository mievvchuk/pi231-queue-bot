import base64
import json
import logging
import aiohttp

import config
from services.errors import BotError

log = logging.getLogger(__name__)


async def recognize_replacements_from_image(image_bytes: bytes, group_name: str = "ПІ-231") -> dict:
    """Розпізнає таблицю замін з фотографії через OpenAI API."""
    api_key = config.RECOGNITION_API_KEY
    if not api_key or config.RECOGNITION_PROVIDER == "disabled":
        raise BotError(
            "⚠️ Автоматичне розпізнавання фото наразі вимкнено (не налаштовано RECOGNITION_API_KEY в .env).\n"
            "Зверніться до адміністратора для активації ключа OpenAI."
        )

    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    prompt = (
        f"Розпізнай розклад або таблицю замін пар для групи '{group_name}'.\n"
        "Поверни ТІЛЬКИ валідний JSON у такому форматі (без markdown-обгорток):\n"
        "{\n"
        '  "date": "YYYY-MM-DD",\n'
        '  "confidence": 0.95,\n'
        '  "replacements": [\n'
        '    {\n'
        f'      "group": "{group_name}",\n'
        '      "lessonNumber": 1,\n'
        '      "newSubject": "Назва предмета",\n'
        '      "teacher": "Прізвище І.О. або null",\n'
        '      "room": "Номер аудиторії або null"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Якщо групу не знайдено або це не таблиця замін, поверни replacements як порожній масив []."
    )

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
                    },
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
                    log.error(f"OpenAI API error {resp.status}: {err_body}")
                    raise BotError(f"Помилка OpenAI API ({resp.status}). Спробуйте пізніше або надішліть чіткіше фото.")
                data = await resp.json()
                content = data["choices"][0]["message"]["content"]
                result = json.loads(content)
    except BotError:
        raise
    except Exception as e:
        log.exception(f"Error calling OpenAI API for recognition: {e}")
        raise BotError(f"Не вдалося обробити зображення: {e}")

    date_str = result.get("date")
    if not date_str:
        raise BotError("Не вдалося визначити дату замін на фото. Переконайтеся, що на фото видно дату.")

    confidence = float(result.get("confidence", 0.0))
    if confidence < 0.6:
        raise BotError("Низька впевненість розпізнавання. Будь ласка, надішліть більш чітке або рівне фото.")

    raw_replacements = result.get("replacements", [])
    target_norm = group_name.strip().lower().replace(" ", "").replace("-", "")
    filtered = []
    for r in raw_replacements:
        grp = str(r.get("group", "")).strip().lower().replace(" ", "").replace("-", "")
        if grp == target_norm or not grp:
            if r.get("newSubject") and str(r.get("newSubject")).strip():
                filtered.append({
                    "date": date_str,
                    "group_name": group_name,
                    "lesson_number": int(r.get("lessonNumber", 1)),
                    "new_subject": str(r["newSubject"]).strip(),
                    "teacher": r.get("teacher"),
                    "room": str(r.get("room")) if r.get("room") is not None else None,
                })

    if not filtered:
        raise BotError(f"Для групи {group_name} замін на фото не знайдено.")

    return {
        "date": date_str,
        "confidence": confidence,
        "replacements": filtered,
    }
