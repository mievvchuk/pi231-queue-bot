"""Single place for journal structure: columns per work and the attestation split."""
from queue_bot.services.lesson_scheduler import subject_name

# Tests are stored as lab numbers TEST_BASE + n, so no schema change is needed.
TEST_BASE = 50
KPZ = "Конструювання програмного забезпечення"
COURSEWORK_KPZ = "Курсова робота з КПЗ"
_COURSEWORK_KEY = subject_name(COURSEWORK_KPZ)

# parts: grade columns per lab; first: labs in attestation 1 (the rest go to 2);
# tests/first_tests: same for tests; title: column label instead of «ЛР».
STRUCTURE = {
    KPZ: {'parts': [5] + [1] * 10, 'first': 7},
    'Людино-машинна взаємодія': {'parts': [1] * 10, 'first': 4},
    'Машинне навчання': {'parts': [3] * 9, 'first': 3, 'optional': True},
    'Програмування в Інтернет': {'parts': [3] * 7, 'first': 4},
    'Архітектура та проєкт ПЗ': {'parts': [5] * 3, 'first': 1},
    'Проєкт інтерфейсів користувача': {'parts': [1] * 14, 'first': 7, 'tests': 9, 'first_tests': 4},
    'Філософія': {'parts': [1] * 6, 'first': 3, 'title': 'Оцінка'},
    'Фізичне виховання': {'parts': [1] * 4, 'first': 2, 'title': 'Норматив'},
}
DEFAULT = {'parts': [1], 'first': 1, 'title': 'Оцінка'}
_BY_KEY = {subject_name(name): value for name, value in STRUCTURE.items()}


def layout(name):
    key = subject_name(name)
    if key == _COURSEWORK_KEY:
        return {"coursework_for": subject_name(KPZ), "labs": [
            {"number": 1, "parts": 1, "optional_last": False, "title": "Курсова робота", "attestation": None},
        ]}
    conf = _BY_KEY.get(key, DEFAULT)
    title = conf.get('title', 'ЛР')
    single = conf['parts'] == [1] and 'tests' not in conf
    labs = [{'number': n, 'parts': parts, 'optional_last': conf.get('optional', False),
             'title': title if single else f'{title} {n}', 'attestation': 1 if n <= conf['first'] else 2}
            for n, parts in enumerate(conf['parts'], 1)]
    labs += [{'number': TEST_BASE + n, 'parts': 1, 'optional_last': False, 'title': f'Тест {n}',
              'attestation': 1 if n <= conf['first_tests'] else 2}
             for n in range(1, conf.get('tests', 0) + 1)]
    return {'labs': labs}


def coursework_grade(cells):
    return next((cell["grade"] for cell in cells
                 if (cell["kind"], cell["number"], cell["part"]) == ("lab", 1, 1)), None)


def summary(subject, cells, *, coursework_cells=None):
    """Averages per attestation and semester. An empty cell is separate from zero."""
    def stats(values, required):
        return {'average': round(sum(values) / len(values), 2) if values else None,
                'filled': len(values), 'total': max(required, len(values))}

    if subject.get("coursework_for"):
        grade = coursework_grade(cells)
        return {"all": stats([] if grade is None else [grade], 1)}

    works = {w['number']: w for w in subject['labs']}
    grades = {1: [], 2: []}
    totals = {1: 0, 2: 0}
    for work in works.values():
        totals[work['attestation']] += work['parts'] - work['optional_last']
    for cell in cells:
        work = works.get(cell['number'])
        if cell['kind'] == 'lab' and work:
            grades[work['attestation']].append(cell['grade'])

    if coursework_cells is not None:
        totals[2] += 1
        grade = coursework_grade(coursework_cells)
        if grade is not None:
            grades[2].append(grade)

    return {
        '1': stats(grades[1], totals[1]),
        '2': stats(grades[2], totals[2]),
        'all': stats(grades[1] + grades[2], totals[1] + totals[2]),
    }
