"""AI artwork and exact PNG exports from the grade journal."""

import asyncio
import base64
from io import BytesIO
import logging
from pathlib import Path
from time import monotonic

import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageOps

from queue_bot import config

log = logging.getLogger(__name__)
_ASSETS = Path(__file__).resolve().parents[1] / "assets"
_AI_PROMPT = (
    "A horizontal educational report header photograph, dark navy college study desk. "
    "A graphite notebook, faint grid paper and a pen confined to the far right third, "
    "with restrained teal window light. The rest is very dark navy negative space. "
    "No text, letters, digits, faces, logos or identifiable information."
)
_AI_URL = "https://api.openai.com/v1/images/generations"
_WIDTH = 960
_HEADER_HEIGHT = 220
_ROWS_PER_PAGE = 20
_ai_lock = asyncio.Lock()
_ai_artwork = None
_ai_refresh_after = 0.0


async def get_artwork() -> bytes:
    if not config.JOURNAL_IMAGE_API_KEY:
        return (_ASSETS / "journal-banner.jpg").read_bytes()

    global _ai_artwork, _ai_refresh_after
    async with _ai_lock:
        if monotonic() >= _ai_refresh_after:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as client:
                    async with client.post(
                        _AI_URL,
                        headers={"Authorization": f"Bearer {config.JOURNAL_IMAGE_API_KEY}"},
                        json={
                            "model": "gpt-image-1", "prompt": _AI_PROMPT, "size": "1536x1024",
                            "quality": "medium", "output_format": "jpeg", "n": 1,
                        },
                    ) as response:
                        response.raise_for_status()
                        result = await response.json()
                artwork = base64.b64decode(result["data"][0]["b64_json"], validate=True)
                # Do not cache a response that can't be opened by the renderer.
                await asyncio.to_thread(_read_banner, artwork)
                _ai_artwork = artwork
                _ai_refresh_after = monotonic() + 86400
            except (aiohttp.ClientError, TimeoutError, ValueError, OSError, KeyError, IndexError, TypeError) as error:
                log.warning("Journal artwork refresh failed: %s", type(error).__name__)
                _ai_refresh_after = monotonic() + 900
    return _ai_artwork or (_ASSETS / "journal-banner.jpg").read_bytes()


def _read_banner(artwork):
    with Image.open(BytesIO(artwork)) as source:
        return ImageOps.fit(source.convert("RGB"), (_WIDTH, _HEADER_HEIGHT), Image.Resampling.LANCZOS)


def _shorten(draw, text, font, width):
    if draw.textlength(text, font=font) <= width:
        return text
    left, right = 0, len(text)
    while left < right:
        mid = (left + right + 1) // 2
        if draw.textlength(text[:mid].rstrip() + "…", font=font) <= width:
            left = mid
        else:
            right = mid - 1
    return text[:left].rstrip() + "…"


def _journal_rows(subject, records):
    by_cell = {(r["kind"], r["number"], r["part"]): r["grade"] for r in records}
    rows = []
    for work in sorted(subject["labs"], key=lambda item: (item["attestation"], item["number"])):
        values = tuple(by_cell.pop(("lab", work["number"], part), None) for part in range(1, work["parts"] + 1))
        rows.append((work["attestation"], work["title"], values))
    older = {}
    for (kind, number, part), grade in sorted(by_cell.items()):
        older.setdefault((kind, number), {})[part] = grade
    for (kind, number), parts in older.items():
        title = ("ЛР" if kind == "lab" else "Атестація") + f" {number} (раніше)"
        rows.append((0, title, tuple(parts.get(part) for part in range(1, max(parts) + 1))))
    return rows


def render_journal(subject, student, period, records, averages, artwork) -> list[bytes]:
    rows = _journal_rows(subject, records)
    pages = [rows[i:i + _ROWS_PER_PAGE] for i in range(0, len(rows), _ROWS_PER_PAGE)] or [[]]
    period_year, semester = period.split(":")
    font_file = str(_ASSETS / "fonts" / "DejaVuSans.ttf")
    title_font, subject_font, label_font, body_font, grade_font, compact_grade_font = (
        ImageFont.truetype(font_file, size) for size in (39, 29, 18, 22, 23, 14)
    )
    banner = _read_banner(artwork)
    images = []
    for page_number, page in enumerate(pages, 1):
        image = Image.new("RGB", (_WIDTH, 388 + 47 * max(1, len(page))), "#f3f6fb")
        image.paste(banner, (0, 0))
        # A translucent panel keeps actual text readable over any generated artwork.
        panel = Image.new("RGBA", (_WIDTH, _HEADER_HEIGHT), (6, 18, 36, 150))
        image.paste(panel, (0, 0), panel)
        draw = ImageDraw.Draw(image)
        draw.text((44, 34), "Електронний журнал", font=title_font, fill="#ffffff")
        draw.text((44, 92), _shorten(draw, subject["name"], subject_font, 870), font=subject_font, fill="#ffffff")
        draw.text((44, 141), _shorten(draw, student["name"], body_font, 870), font=body_font, fill="#deebfc")
        draw.text((44, 176), f"{period_year} · семестр {semester}", font=label_font, fill="#c0d2ea")

        for i, number in enumerate(("1", "2")):
            x = 44 + 450 * i
            draw.rounded_rectangle((x, 240, x + 422, 301), radius=12, fill="#e4ecfa")
            draw.text((x + 14, 259), f"Атестація {number}", font=label_font, fill="#243f6a")
            average = averages.get(number, {}).get("average")
            draw.text((x + 405, 271), "—" if average is None else f"{average:.2f}",
                      font=grade_font, fill="#102947", anchor="rm")
        draw.text((56, 319), "АТ.", font=label_font, fill="#415674")
        draw.text((92, 319), "РОБОТА", font=label_font, fill="#415674")
        draw.text((900, 319), "ОЦІНКИ", font=label_font, fill="#415674", anchor="ra")

        for index, (attestation, title, values) in enumerate(page):
            y = 350 + 47 * index
            draw.rounded_rectangle((44, y, 916, y + 42), radius=8, fill="#ffffff" if index % 2 == 0 else "#eaf0f9")
            draw.text((56, y + 10), str(attestation) if attestation else "—", font=label_font, fill="#60748f")
            draw.text((92, y + 8), _shorten(draw, title, body_font, 350), font=body_font, fill="#172c48")
            for offset, value in enumerate(reversed(values)):
                grade = "—" if value is None else str(value).removesuffix(".0")
                color = "#60748f" if value is None else "#a52233" if value == 5 else "#172c48"
                point = (900 - 88 * offset, y + 21)
                if draw.textlength(grade, font=grade_font) <= 76:
                    draw.text(point, grade, font=grade_font, fill=color, anchor="rm")
                else:
                    text = "\n".join(grade[start:start + 9] for start in range(0, len(grade), 9))
                    draw.multiline_text(point, text, font=compact_grade_font, fill=color, anchor="rm", align="right", spacing=0)
        if not page:
            draw.text((56, 355), "Робіт поки немає", font=body_font, fill="#415674")

        if len(pages) > 1:
            draw.text((916, image.height - 18), f"{page_number} / {len(pages)}", font=label_font, fill="#415674", anchor="rb")
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        images.append(buffer.getvalue())
    return images
