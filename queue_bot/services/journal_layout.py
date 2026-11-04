"""Single place for journal structure: columns per work and the attestation split."""
from queue_bot.services.lesson_scheduler import subject_name

# Tests are stored as lab numbers TEST_BASE + n, so no schema change is needed.
TEST_BASE = 50

# parts: grade columns per lab; first: labs in attestation 1 (the rest go to 2);
# tests/first_tests: same for tests; title: column label instead of «ЛР».
STRUCTURE = {
    'Конструювання програмного забезпечення': {'parts': [5] + [1] * 10, 'first': 7},
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
    conf = _BY_KEY.get(subject_name(name), DEFAULT)
    title = conf.get('title', 'ЛР')
    single = conf['parts'] == [1] and 'tests' not in conf
    labs = [{'number': n, 'parts': parts, 'optional_last': conf.get('optional', False),
             'title': title if single else f'{title} {n}', 'attestation': 1 if n <= conf['first'] else 2}
            for n, parts in enumerate(conf['parts'], 1)]
    labs += [{'number': TEST_BASE + n, 'parts': 1, 'optional_last': False, 'title': f'Тест {n}',
              'attestation': 1 if n <= conf['first_tests'] else 2}
             for n in range(1, conf.get('tests', 0) + 1)]
    return {'labs': labs}


def summary(subject, cells):
    """Averages per attestation and overall from one student's cells; empty cells are not zeros."""
    works = {w['number']: w for w in subject['labs']}
    grades = {1: [], 2: []}
    for cell in cells:
        work = works.get(cell['number'])
        if cell['kind'] == 'lab' and work:
            grades[work['attestation']].append(cell['grade'])

    def stats(values, attestations):
        required = sum(w['parts'] - w['optional_last'] for w in works.values() if w['attestation'] in attestations)
        return {'average': round(sum(values) / len(values), 2) if values else None,
                'filled': len(values), 'total': max(required, len(values))}
    return {'1': stats(grades[1], {1}), '2': stats(grades[2], {2}), 'all': stats(grades[1] + grades[2], {1, 2})}
