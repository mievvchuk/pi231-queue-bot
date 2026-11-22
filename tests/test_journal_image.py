from io import BytesIO

from PIL import Image

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
    assert (None, "ЛР 99 (раніше)", (None, 5)) in rows
    assert (1, "ЛР 2", (None,)) in rows
    images = journal_image.render_journal(subject, {"name": "Тестовий Студент"}, "2026-2027:1", records, {})
    assert len(images) == 2
    for content in images:
        with Image.open(BytesIO(content)) as image:
            assert image.format == "PNG" and image.width == 960
            image.verify()


def test_coursework_image_has_only_one_grade_row_and_kpz_can_show_it_in_second_attestation():
    title = "Курсова робота з КПЗ"
    course = {"name": title, **layout(title)}
    records = [
        {"kind": "lab", "number": 1, "part": 1, "grade": 4.5},
        {"kind": "attestation", "number": 1, "part": 1, "grade": 5},
    ]
    assert journal_image._journal_rows(course, records) == [(None, "Курсова робота", (4.5,))]
    kpz = {"name": "КПЗ", **layout("КПЗ")}
    assert journal_image._journal_rows(kpz, [], records)[-1] == (2, "Курсова робота", (4.5,))
    content, = journal_image.render_journal(course, {"name": "Студент"}, "2026-2027:1", records, {})
    with Image.open(BytesIO(content)) as image:
        assert image.width == 960 and image.height < 300
        assert image.getpixel((0, 0)) == (255, 255, 255)
