"""Reads the replacement table without AI: OpenCV finds the grid, RapidOCR reads each cell.

Returns the same dict the AI path returns, so group filtering, merged group cells and
header notes stay in recognition.py.
"""
import difflib
import re
from datetime import date, timedelta
from functools import lru_cache

from queue_bot.services.errors import BotError

# shortcut: the college's group prefixes; add a new one here when a new specialty appears.
GROUP_PREFIXES = {'ПІ', 'ПМ', 'МЕ', 'МЕБ', 'АТ', 'КІ', 'ЕК', 'МГ', 'ОЕ'}
LATIN_CHARS = 'ABEKMHOPCTXYIJΓΠaeopcyxikmthjπ'
LATIN = str.maketrans(LATIN_CHARS, 'АВЕКМНОРСТХУІЛГПаеорсухікмтнлп')
SHAPE = str.maketrans('авекмнорстухіїйгbrvjл', 'abekmhopctyxiiitptyll')
P_LOOKALIKES = {'', 'І', 'ІІ', 'Т', 'Н', 'Л'}
# The model often prints Russian «и» for Ukrainian «і»; compare words with these letters folded.
FOLD = str.maketrans('іїйєґ’ʼ`', "иииег'''")
DIGITS = str.maketrans({'З': '3', 'з': '3', 'О': '0', 'о': '0', 'O': '0', 'o': '0', 'I': '1', 'l': '1', 'І': '1',
                        'і': '1', '–': '-', '—': '-', ' ': ''})
MONTHS = ('січ', 'лют', 'берез', 'квіт', 'трав', 'черв', 'лип', 'серп', 'верес', 'жовт', 'листоп', 'груд')
WORDS = ("немає пари по розкладу здача заліків перездача заборгованостей фізичне виховання фізика "
         "математика українська мова іноземна історія україни інформатика хімія біологія екологія географія "
         "черговий викладач заміни розкладі четверті треті другі перші п'яті проводяться розкладом понеділка "
         "вівторка середи четверга п'ятниці суботи понеділок вівторок середа четвер п'ятниця субота "
         "знаменник чисельник навчання група виходить січень лютий березень квітень травень червень липень "
         "серпень вересень жовтень листопад грудень січня лютого березня квітня травня червня липня серпня "
         "вересня жовтня листопада грудня")
WORD = re.compile(r"[^\W\d_]+(?:['’ʼ`][^\W\d_]+)*")


def fold(text: str) -> str:
    return str(text).lower().translate(FOLD)


def vocabulary(schedule_data=None) -> dict:
    from queue_bot.services import recognition, schedule
    texts = [WORDS, *recognition.SUBJECT_ABBREVIATIONS.values(), *recognition.TEACHER_EXPANSIONS.values()]
    stack = [schedule.load_schedule_data() if schedule_data is None else schedule_data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            texts += [str(v) for k, v in item.items() if k in ('subject', 'teacher', 'name') and v]
            stack += list(item.values())
        elif isinstance(item, list):
            stack += item
    vocab = {}
    for text in texts:
        for word in WORD.findall(text):
            vocab.setdefault(fold(word), word.lower())
    return vocab


def shape(word: str) -> str:
    """Cyrillic and Latin letters folded to what the model confuses them with: р/b, т/r, у/v."""
    return word.lower().translate(SHAPE)


def shapes(words) -> dict:
    return {shape(word): word for word in words.values() if len(word) >= 5}


def fix_text(text: str, cutoff: float = 0.85, *, words, word_shapes) -> str:
    """Latin look-alikes inside Cyrillic words and «и»/«і» mix-ups, restored from known words.

    cutoff repairs a dropped or wrong letter; notes use a looser one, their words are all known."""
    def word(m):
        token = m[0]
        # The tables are Ukrainian: a word made only of look-alikes ('JMB') is Cyrillic too.
        if re.search('[а-яіїєґА-ЯІЇЄҐ]', token) or set(token) <= set(LATIN_CHARS):
            token = token.translate(LATIN)
        known = words.get(fold(token))
        if not known and not re.search('[а-яіїєґА-ЯІЇЄҐ]', token) and len(token) >= 5:
            # A smeared cell comes out in Latin ('Abxirekrvoa'): compare letter shapes instead.
            close = difflib.get_close_matches(shape(token), word_shapes, n=1, cutoff=0.8)
            known = word_shapes[close[0]] if close else None
        if not known and len(token) >= 4:
            close = difflib.get_close_matches(fold(token), words, n=1, cutoff=cutoff)
            known = words[close[0]] if close else None
        if not known:
            return token
        return known.upper() if token.isupper() and len(token) > 1 else known[0].upper() + known[1:] if token[0].isupper() else known
    return WORD.sub(word, ' '.join(str(text).split()))


def fix_note(text: str, *, words, word_shapes) -> str:
    """'З-Ї ПАВН ПОНЕДІЛКА' -> '3-ї пари ПОНЕДІЛКА': after an ordinal number only «пари» can follow."""
    text = re.sub(r'(?<!\w)[Зз](?=\s*-\s*[ЇїІіИи](?!\w))', '3', fix_text(text, cutoff=0.75, words=words, word_shapes=word_shapes))
    return re.sub(r'(\d)\s*-\s*[ЇїІіИи]\s+[^\s(]+', r'\1-ї пари', text)


def parse_group(text: str) -> str | None:
    """'III-232' -> 'ПІ-232', 'KI-231,232' -> 'КІ-231,232'; None when it is not a readable group."""
    t = re.sub(r'\s+', '', str(text).upper().translate(LATIN)).strip('.,;:')
    t = re.sub(r'^(\D+)1(?=[-–—])', r'\1І', t)  # 'K1-253'
    t = re.sub(r'^(\D+)[56](?=[-–—])', r'\1Б', t)  # 'МЕ5-241'
    m = re.fullmatch(r'([^\d\W]+)[-–—.]*(\d{3}(?:[,.]\d{3})*)', t)
    if not m:
        return None
    letters, digits = m[1], m[2].replace('.', ',')
    if letters not in GROUP_PREFIXES:
        # «П» is the one glyph the model loses: 'ІІ', 'І', 'Т', 'Н' or nothing ('І-231' is ПІ-231).
        # «К» was never misread on real tables, so this does not steal КІ rows.
        hits = {p for p in GROUP_PREFIXES if p[0] == 'П' and letters.endswith(p[1:])
                and letters[:len(letters) - len(p) + 1] in P_LOOKALIKES}
        if not hits:
            hits = {letters.replace('Т', 'Г')} & GROUP_PREFIXES  # «Г» looks like «Т»: 'МТ' is МГ
        if not hits:
            # Otherwise one wrong letter, only when a single prefix fits.
            hits = {p for p in GROUP_PREFIXES
                    if len(p) == len(letters) and sum(a != b for a, b in zip(p, letters)) == 1}
        if len(hits) != 1:
            return None
        letters = hits.pop()
    return f'{letters}-{digits}'


def parse_lessons(text: str) -> str:
    """The thin dash is often lost or read as a dot: '1.2', '34', '14' -> '1-2', '3-4', '1-4'."""
    t = re.sub(r'(?<=\d)[.,:]', '-', str(text).translate(DIGITS))
    if re.fullmatch(r'\d\d', t) and t[0] < t[1]:
        t = f'{t[0]}-{t[1]}'
    return t if re.fullmatch(r'\d{1,2}(-\d{1,2})?', t) else str(text).strip()


def parse_date(lines: list[str]) -> str | None:
    """Date from the title, checked against the weekday line: a dropped digit ('4' for '14')
    is recovered only when exactly one day of that month fits both."""
    from queue_bot.services.recognition import DAY_STEMS
    text = fold(' '.join(lines))
    # A schedule announcement names the SOURCE weekday, not the photo's date.
    # Only title/weekday lines may validate the target date.
    weekday_text = ' '.join(fold(line) for line in lines
                            if not re.search(r'пар|чергов|викладач', fold(line)))
    weekday = next((i for i, (stem, _) in enumerate(DAY_STEMS) if fold(stem) in weekday_text), None)
    m = re.search(r'(\d{1,2})\s*([^\W\d_]+)\s*(\d\d[\dбзо]{2})', text)  # '202бр.'
    month = next((i for i, stem in enumerate(MONTHS, 1) if m and m[2].startswith(fold(stem))), None)
    if not month:
        return None
    first = date(int(m[3].translate(str.maketrans('бзо', '630'))), month, 1)
    days = [first + timedelta(n) for n in range(31) if (first + timedelta(n)).month == month]
    exact = [d for d in days if d.day == int(m[1])]
    if weekday is None:
        return exact[0].isoformat() if exact else None
    if exact and exact[0].weekday() == weekday:
        return exact[0].isoformat()
    fits = [d for d in days if d.weekday() == weekday and m[1] in str(d.day)]
    return fits[0].isoformat() if len(fits) == 1 else None


@lru_cache(maxsize=1)
def engine():
    try:
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
    except ImportError:
        raise BotError('Розпізнавання без ШІ не встановлено на сервері (пакет rapidocr).')
    return RapidOCR(params={'Rec.lang_type': LangRec.ESLAV, 'Rec.ocr_version': OCRVersion.PPOCRV5,
                            'Rec.model_type': ModelType.MOBILE})


def _runs(profile, gap: int) -> list[list[int]]:
    import numpy as np
    runs = []
    for v in np.flatnonzero(profile):
        if runs and v - runs[-1][1] <= gap:
            runs[-1][1] = v
        else:
            runs.append([v, v])
    return runs


def _read(gray, scores: list) -> str:
    import cv2
    crop = cv2.copyMakeBorder(gray, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
    result = engine()(cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR), use_det=False, use_cls=False)
    scores += list(result.scores or ())
    return ''.join(result.txts or ()).strip()


def read_lines(gray, scores: list) -> list[str]:
    """Each text line of a crop separately: the recognizer reads only single lines."""
    ink = gray < 150
    bands = _runs(ink.sum(axis=1), 1)
    # Dots of «ї», «й» are a thin band of their own: glue them to the line below.
    for i in range(len(bands) - 1, -1, -1):
        if bands[i][1] - bands[i][0] < 6 and len(bands) > 1:
            j = i + 1 if i + 1 < len(bands) else i - 1
            bands[j] = [min(bands[i][0], bands[j][0]), max(bands[i][1], bands[j][1])]
            del bands[i]
    lines = []
    for top, bottom in bands:
        if bottom - top < 8 or ink[top:bottom + 1].sum() < 40:
            continue
        band = ink[top:bottom + 1]
        # Far-apart pieces (the table icon next to the title) are read separately.
        parts = [_read(gray[max(top - 3, 0):bottom + 4, max(a - 3, 0):b + 4], scores)
                 for a, b in _runs(band.sum(axis=0), 4 * (bottom - top))]
        text = ' '.join(p for p in parts if p)
        if text:
            lines.append(text)
    return lines


def _positions(mask_sum, threshold, gap=3) -> list[int]:
    import numpy as np
    groups = []
    for v in np.flatnonzero(mask_sum > threshold):
        if groups and v - groups[-1][-1] <= gap:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [int(sum(g) / len(g)) for g in groups]


def read_table(image: bytes) -> dict:
    import cv2
    import numpy as np
    gray = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise BotError('Не вдалося відкрити фото таблиці.')
    # Channel screenshots have ~8 px letters; the recognizer reads them far better upscaled.
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    h, w = gray.shape
    bw = cv2.adaptiveThreshold(~gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2)
    hor = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (w // 8, 1)))
    ys = _positions(hor.sum(axis=1) / 255, w * 0.3)
    xs = []
    if len(ys) > 1:
        # A column line spans most of a row; letter strokes are shorter.
        row = int(np.median(np.diff(ys)))
        ver = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(8, row * 3 // 4))))
        # gap: a doubled outer border is one line, not an extra column.
        xs = _positions(ver.sum(axis=0) / 255, (ys[-1] - ys[0]) * 0.5, gap=20)
    if len(xs) != 7:
        raise BotError('Не вдалося знайти на фото таблицю замін із 6 стовпцями. Внесіть заміни вручну.')
    clean = gray.copy()
    clean[(hor | ver) > 0] = 255
    scores, rows = [], []
    for y0, y1 in zip(ys, ys[1:]):
        # A merged cell has no vertical line between its columns.
        present = [x for x in xs[1:-1] if (ver[y0 + 5:y1 - 4, x - 4:x + 5].max(axis=1) > 0).mean() > 0.6]
        bounds = [xs[0], *present, xs[-1]]
        # Stay inside the borders (their remnants read as «b», «]»); text starts right at the left one.
        texts = [read_lines(clean[y0 + 4:y1 - 3, a + 3:b - 5], scores) for a, b in zip(bounds, bounds[1:])]
        if len(bounds) == 2:
            rows += [{'note': line} for line in texts[0]]
            continue
        cells = [''] * 6
        for a, lines in zip(bounds, texts):
            cells[xs.index(a)] = ' '.join(lines)
        rows.append({'cells': cells})
    # Full-width rows before the column titles are the title itself (a photo may start at a line).
    lead = next((i for i, row in enumerate(rows) if 'cells' in row), len(rows))
    header = read_lines(clean[:max(ys[0] - 1, 0)], scores) + [row['note'] for row in rows[:lead]]
    return {'header': header, 'rows': rows[lead:], 'scores': scores}


def recognize(images: list[bytes], group_name: str, schedule_data=None) -> dict:
    from queue_bot.services.recognition import expand_groups, parse_header_note
    from queue_bot.services.schedule_notes import normalize_group
    tables = sorted((read_table(image) for image in images), key=lambda t: parse_date(t['header']) is None)
    words = vocabulary(schedule_data)
    spelling = {"words": words, "word_shapes": shapes(words)}
    header = [fix_note(line, **spelling) for t in tables for line in t['header']]
    week = next((key for word, key in (('знаменник', 'denominator'), ('чисельник', 'numerator'))
                 if fold(word) in fold(' '.join(header))), None)
    notes, rows, readable = [], [], True
    target_digits = set(re.findall(r'\d{3}', group_name))
    for table in tables:
        for row in table['rows']:
            if 'note' in row:
                notes.append(fix_note(row['note'], **spelling))
                continue
            group_cell, lessons, replaced, subject, teacher, room = row['cells']
            if not re.search(r'\d', group_cell + lessons):
                continue  # column titles
            group = parse_group(group_cell) if group_cell else ''
            lessons = parse_lessons(lessons)
            # An unreadable group may hide ours only when its number could be ours.
            digits = set(re.findall(r'\d{3}', group_cell))
            readable &= group is not None or bool(digits and not digits & target_digits)
            rows.append({'group': group if group is not None else group_cell, 'lessons': lessons,
                         'replaced': fix_text(replaced, **spelling), 'subject': fix_text(subject, **spelling),
                         'teacher': fix_text(teacher, **spelling), 'room': room.translate(LATIN).strip()})
    notes += [line for line in header if fold('розклад') in fold(line) and fold('замін') not in fold(line)]
    notes = [n for n in notes if not re.search(fold('черговий|виходить на навчання'), fold(n))]
    target = normalize_group(group_name)
    scores = [s for t in tables for s in t['scores']]
    return {
        'date': parse_date(header),
        'confidence': round(sum(scores) / len(scores), 3) if scores else 0.0,
        'headerWeekType': week,
        'notes': notes,
        'unparsedNotes': [n for n in notes if 'проводят' in fold(n) and not parse_header_note(n, week)],
        'rows': rows,
        'tableReadable': readable,
        'noReplacements': not any(target in expand_groups(r['group']) for r in rows if r['group']),
    }
