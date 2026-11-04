from queue_bot.services.lesson_scheduler import subject_name


def layout(name):
    key = subject_name(name)
    counts, optional = [1], False
    if key == subject_name('Конструювання програмного забезпечення'):
        counts = [5] + [1] * 10
    elif key == subject_name('Людино-машинна взаємодія'):
        counts = [1] * 10
    elif key == subject_name('Машинне навчання'):
        counts, optional = [3] * 9, True
    elif key == subject_name('Програмування в Інтернет'):
        counts = [3] * 7
    elif key == subject_name('Архітектура та проєкт ПЗ'):
        counts = [5] * 3
    return {'labs': [{'number': number, 'parts': parts, 'optional_last': optional}
                     for number, parts in enumerate(counts, 1)],
            'single_grade': counts == [1]}
