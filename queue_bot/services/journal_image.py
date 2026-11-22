"""Render a plain grade table from one student's journal records."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_ASSETS = Path(__file__).resolve().parents[1] / "assets"
_WIDTH = 960
_ROWS_PER_PAGE = 20
_ROW_HEIGHT = 46
_COURSEWORK_ROW = "Курсова робота"


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


def _journal_rows(subject, records, coursework_records=None):
    from queue_bot.services.journal_layout import coursework_grade

    if subject.get("coursework_for"):
        return [(None, _COURSEWORK_ROW, (coursework_grade(records),))]

    by_cell = {(r["kind"], r["number"], r["part"]): r["grade"] for r in records}
    rows = []
    for work in sorted(subject["labs"], key=lambda item: (item["attestation"], item["number"])):
        values = tuple(by_cell.pop(("lab", work["number"], part), None) for part in range(1, work["parts"] + 1))
        rows.append((work["attestation"], work["title"], values))
    if coursework_records is not None:
        rows.append((2, _COURSEWORK_ROW, (coursework_grade(coursework_records),)))
    older = {}
    for (kind, number, part), grade in sorted(by_cell.items()):
        older.setdefault((kind, number), {})[part] = grade
    for (kind, number), parts in older.items():
        title = ("ЛР" if kind == "lab" else "Атестація") + f" {number} (раніше)"
        rows.append((None, title, tuple(parts.get(part) for part in range(1, max(parts) + 1))))
    return rows


def render_journal(subject, student, period, records, averages, coursework_records=None) -> list[bytes]:
    rows = _journal_rows(subject, records, coursework_records)
    pages = [rows[i:i + _ROWS_PER_PAGE] for i in range(0, len(rows), _ROWS_PER_PAGE)] or [[]]
    period_year, semester = period.split(":")
    font_file = str(_ASSETS / "fonts" / "DejaVuSans.ttf")
    title_font, label_font, body_font, grade_font, compact_grade_font = (
        ImageFont.truetype(font_file, size) for size in (31, 18, 22, 23, 14)
    )
    # Coursework has a single grade, with no attestation columns or averages of its own.
    show_summary = not subject.get("coursework_for")
    table_top = 173 if show_summary else 124
    table_bottom_padding = 42
    work_x = 116 if show_summary else 54
    columns = max((len(values) for _, _, values in rows), default=1)
    first_grade = 916 - columns * 88
    images = []

    for page_number, page in enumerate(pages, 1):
        table_bottom = table_top + 37 + _ROW_HEIGHT * max(1, len(page))
        image = Image.new("RGB", (_WIDTH, table_bottom + table_bottom_padding), "white")
        draw = ImageDraw.Draw(image)
        draw.text((44, 24), _shorten(draw, subject["name"], title_font, 872), font=title_font, fill="#18212e")
        draw.text((44, 70), _shorten(draw, student["name"], body_font, 872), font=body_font, fill="#18212e")
        draw.text((44, 101), f"{period_year} · семестр {semester}", font=label_font, fill="#4c5669")

        if show_summary:
            for index, (key, label) in enumerate((("1", "Атестація 1"), ("2", "Атестація 2"), ("all", "Семестр"))):
                x = 44 + 296 * index
                average = averages.get(key, {}).get("average")
                draw.text((x, 138), f"{label}: " + ("—" if average is None else f"{average:.2f}"),
                          font=label_font, fill="#18212e")

        grid = "#b4bcc8"
        draw.rectangle((44, table_top, 916, table_bottom), outline=grid)
        if show_summary:
            draw.line((103, table_top, 103, table_bottom), fill=grid)
            draw.text((55, table_top + 8), "Ат.", font=label_font, fill="#18212e")
        draw.text((work_x, table_top + 8), "Робота", font=label_font, fill="#18212e")
        draw.line((first_grade, table_top, first_grade, table_bottom), fill=grid)
        draw.text((905, table_top + 8), "Оцінки", font=label_font, fill="#18212e", anchor="ra")

        for index, (attestation, title, values) in enumerate(page or [(None, "Робіт поки немає", ())]):
            y = table_top + 37 + _ROW_HEIGHT * index
            draw.line((44, y, 916, y), fill=grid)
            if show_summary:
                draw.text((55, y + 11), str(attestation) if attestation else "—", font=label_font, fill="#4c5669")
            draw.text((work_x, y + 9), _shorten(draw, title, body_font, first_grade - work_x - 12),
                      font=body_font, fill="#18212e")
            for offset, value in enumerate(values):
                x = first_grade + 44 + 88 * offset
                if offset:
                    draw.line((x - 44, y, x - 44, y + _ROW_HEIGHT), fill=grid)
                label = "—" if value is None else str(value).removesuffix(".0")
                color = "#4c5669" if value is None else "#a52233" if value == 5 else "#18212e"
                point = (x, y + _ROW_HEIGHT // 2)
                if draw.textlength(label, font=grade_font) <= 76:
                    draw.text(point, label, font=grade_font, fill=color, anchor="mm")
                else:
                    text = "\n".join(label[start:start + 9] for start in range(0, len(label), 9))
                    draw.multiline_text(point, text, font=compact_grade_font, fill=color, anchor="mm", align="center", spacing=0)

        if len(pages) > 1:
            draw.text((916, table_bottom + 11), f"{page_number} / {len(pages)}", font=label_font, fill="#4c5669", anchor="ra")
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        images.append(buffer.getvalue())
    return images
