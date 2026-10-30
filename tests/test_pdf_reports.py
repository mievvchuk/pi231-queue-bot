from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfReader

from queue_bot.services import queues, reports


def test_global_pdf_contains_real_totals_and_all_students(conn, make_user):
    make_user(1, 'Шевчук Іллюша')
    subjects = [{'id': 1, 'name': 'Машинне навчання'}]
    raw = reports.global_report_pdf(queues.get_global_statistics(conn, subjects), subjects)
    reader = PdfReader(BytesIO(raw))
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert raw.startswith(b'%PDF-')
    assert 'Шевчук Іллюша' in text
    assert 'Машинне навчання' in text
    assert 'Зведений звіт' in text
    assert 'Telegram ID' not in text


def test_subject_pdf_paginates_and_repeats_headers_without_losing_rows():
    report = {'sessions_count': 5, 'total_passed': 100, 'active_session': None,
              'students': [{'name': f'Студент Український {i:03}', 'passed_count': 1,
                            'not_passed_count': 0, 'current_position': None,
                            'dates': ['2026-10-30 10:00']} for i in range(100)]}
    raw = reports.subject_report_pdf(report, {'name': 'КПЗ', 'teachers': []})
    reader = PdfReader(BytesIO(raw))
    assert len(reader.pages) >= 3
    texts = [page.extract_text() for page in reader.pages]
    assert all('Студент' in text and 'Сторінка' in text for text in texts)
    assert 'Студент Український 000' in texts[0]
    assert 'Студент Український 099' in texts[-1]


@pytest.mark.asyncio
async def test_telegram_report_sends_a_pdf_document(conn, make_user):
    from queue_bot.handlers.admin.reports import admin_export_global_pdf

    make_user(1, 'Шевчук Іллюша')
    message = SimpleNamespace(answer_document=AsyncMock())
    await admin_export_global_pdf(message, conn, [])
    file = message.answer_document.await_args.args[0]
    assert file.filename.endswith('.pdf')
    text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(file.data)).pages)
    assert 'Шевчук Іллюша' in text
