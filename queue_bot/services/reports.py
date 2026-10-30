"""Read-only PDF rendering shared by Telegram and the Mini App."""
from datetime import datetime
from html import escape
from io import BytesIO
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import CondPageBreak, LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

from queue_bot import config

_FONT = 'QueueDejaVu'
_LOCK = Lock()
_INK = colors.HexColor('#17283C')
_BLUE = colors.HexColor('#325879')
_WIDTH = A4[0] - 36 * mm


def _font():
    with _LOCK:
        if _FONT not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(_FONT, str(Path(__file__).resolve().parents[1] / 'assets/fonts/DejaVuSans.ttf')))


def _paragraph(value, *, heading=False, header=False):
    style = ParagraphStyle(
        'cell', fontName=_FONT, fontSize=16 if heading else 8.5,
        leading=21 if heading else 12, textColor=colors.white if header else _INK,
        spaceAfter=10 if heading else 0, wordWrap='LTR',
    )
    return Paragraph(escape(str(value)).replace('\n', '<br/>'), style)


def _table(headers, rows, widths):
    table = LongTable(
        [[_paragraph(h, header=True) for h in headers]] +
        [[_paragraph(v) for v in row] for row in rows],
        colWidths=[_WIDTH * w for w in widths], repeatRows=1,
        hAlign='LEFT', splitInRow=1,
    )
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), _BLUE),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F1F5F8')]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LINEBELOW', (0, 0), (-1, 0), .5, _BLUE),
    ]))
    return table


def _section(story, title, headers, rows, widths):
    heading = _paragraph(title, heading=True)
    story.extend([CondPageBreak(40 * mm), Spacer(1, 7 * mm), heading])
    story.append(_table(headers, rows, widths) if rows else _paragraph('Записів поки немає.'))


def _build(title, make_story):
    _font()
    stream = BytesIO()
    stamp = datetime.now(ZoneInfo(config.TIMEZONE)).strftime('%d.%m.%Y о %H:%M')
    group = config.STUDENT_GROUP
    story = [_paragraph(title, heading=True), _paragraph(f'Група {group} | Сформовано {stamp} ({config.TIMEZONE})'), Spacer(1, 5 * mm)]
    make_story(story)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#D5DEE6'))
        canvas.line(18 * mm, 15 * mm, A4[0] - 18 * mm, 15 * mm)
        canvas.setFont(_FONT, 8)
        canvas.setFillColor(_BLUE)
        canvas.drawString(18 * mm, 10 * mm, f'{group} | Електронні черги')
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f'Сторінка {doc.page}')
        canvas.restoreState()

    doc = SimpleDocTemplate(stream, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                            topMargin=18 * mm, bottomMargin=23 * mm,
                            title=f'{group}: {title}', author=f'Бот {group}')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()


def global_report_pdf(stats, subjects):
    """All students and per-subject totals; never includes Telegram identifiers."""
    def story(items):
        items.append(_table(['Показник', 'Значення'], [
            ['Сесій усього / активних / архівних', f"{stats['total_sessions']} / {stats['active_sessions']} / {stats['archived_sessions']}"],
            ['Звичайні пари / консультації / курсова', f"{stats['lessons_count']} / {stats['consults_count']} / {stats['cw_count']}"],
            ['Завершених здач / не здано або перенесено', f"{stats['total_passed']} / {stats['total_removed']}"],
            ['Виходів із черг / активних записів', f"{stats['total_left']} / {stats['total_active']}"],
            ['Частка успішних завершень', f"{stats['pass_rate']}%"],
            ['Студентів у базі / з Telegram', f"{stats['registered_users'] + stats['placeholder_users']} / {stats['registered_users']}"],
        ], [.73, .27]))
        items.append(Spacer(1, 4 * mm))
        items.append(_paragraph('Статистика враховує завершені записи в чергах. Це не кількість окремих лабораторних і не оцінки.'))
        _section(items, 'Активність за предметами', ['Предмет', 'Сесій', 'Здач'],
                 [[s['name'], s['sessions'], s['passed']] for s in stats['subjects']], [.70, .15, .15])
        students = sorted(stats['top_students'], key=lambda s: s['name'].casefold())
        _section(items, 'Підсумки студентів', ['Студент', 'Здач', 'Не здано / перенесено', 'Виходів'],
                 [[s['name'], s['passed'], s['removed'], s['left']] for s in students], [.46, .13, .26, .15])
        # Narrow batches keep long subject names readable on A4, regardless of catalog size.
        for start in range(0, len(subjects), 3):
            batch = subjects[start:start + 3]
            _section(items, 'Завершені здачі за предметами', ['Студент'] + [s['name'] for s in batch],
                     [[s['name']] + [s['by_subject'].get(sub['id'], 0) for sub in batch] for s in students],
                     [.40] + [.60 / len(batch)] * len(batch))
    return _build('Зведений звіт', story)


def subject_report_pdf(report, subject):
    def story(items):
        items.append(_paragraph(subject['name'], heading=True))
        teachers = ', '.join(t['name'] for t in subject.get('teachers', [])) or 'Не вказано'
        items.extend([_paragraph(f'Викладачі: {teachers}'), Spacer(1, 4 * mm)])
        items.append(_table(['Показник', 'Значення'], [
            ['Проведено сесій', report['sessions_count']],
            ['Завершених здач', report['total_passed']],
            ['Поточна черга', 'Є активна сесія' if report['active_session'] else 'Немає активної сесії'],
        ], [.73, .27]))
        items.extend([Spacer(1, 5 * mm), _paragraph('Здача означає завершений запис у черзі, а не кількість окремих лабораторних. Місце вказано в поточній черзі; дати наведено як у журналі бота.')])
        _section(items, 'Результати студентів', ['Студент', 'Здач', 'Не здано', 'Місце', 'Дати здач'],
                 [[s['name'], s['passed_count'], s['not_passed_count'], s['current_position'] or '-',
                   '\n'.join(s['dates']) or '-'] for s in report['students']], [.35, .10, .13, .10, .32])
    return _build('Звіт за предметом', story)
