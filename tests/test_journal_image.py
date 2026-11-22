from base64 import b64encode
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

from PIL import Image
import pytest

from queue_bot import config
from queue_bot.services import journal_image
from queue_bot.services.journal_layout import layout


def test_export_preserves_zero_missing_cells_history_and_pagination():
    title = "Проєкт інтерфейсів користувача"
    subject = {"name": title, **layout(title)}
    records = [
        {"kind": "lab", "number": 1, "part": 1, "grade": 0.0},
        {"kind": "lab", "number": 51, "part": 1, "grade": 4.375},
        {"kind": "lab", "number": 99, "part": 2, "grade": 5},
    ]
    rows = journal_image._journal_rows(subject, records)
    assert (1, "ЛР 1", (0.0,)) in rows
    assert (1, "Тест 1", (4.375,)) in rows
    assert (0, "ЛР 99 (раніше)", (None, 5)) in rows
    assert (1, "ЛР 2", (None,)) in rows
    artwork = (journal_image._ASSETS / "journal-banner.jpg").read_bytes()
    images = journal_image.render_journal(subject, {"name": "Тестовий Студент"}, "2026-2027:1", records, {}, artwork)
    assert len(images) == 2
    for content in images:
        with Image.open(BytesIO(content)) as image:
            assert image.format == "PNG" and image.width == 960
            image.verify()


@pytest.mark.asyncio
async def test_ai_artwork_uses_only_the_generic_prompt_and_is_cached(monkeypatch):
    monkeypatch.setattr(config, "JOURNAL_IMAGE_API_KEY", "test-token")
    monkeypatch.setattr(journal_image, "_ai_artwork", None)
    monkeypatch.setattr(journal_image, "_ai_refresh_after", 0.0)
    sample = BytesIO()
    Image.new("RGB", (960, 220), "#13233a").save(sample, format="JPEG")
    response = MagicMock()
    response.json = AsyncMock(return_value={"data": [{"b64_json": b64encode(sample.getvalue()).decode()}]})
    request = MagicMock()
    request.__aenter__.return_value = response
    client = MagicMock()
    client.post.return_value = request
    session = MagicMock()
    session.__aenter__.return_value = client
    monkeypatch.setattr(journal_image.aiohttp, "ClientSession", lambda **kwargs: session)

    assert await journal_image.get_artwork() == sample.getvalue()
    assert await journal_image.get_artwork() == sample.getvalue()
    client.post.assert_called_once()
    request_body = client.post.call_args.kwargs["json"]
    assert set(request_body) == {"model", "prompt", "size", "quality", "output_format", "n"}
    assert request_body["prompt"] == journal_image._AI_PROMPT
