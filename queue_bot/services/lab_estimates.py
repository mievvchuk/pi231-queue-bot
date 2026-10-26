"""Validate and display the student's own workload estimate, never a guessed time."""
from queue_bot.services.errors import BotError

MAX_LAB_COUNT = 20


def validate_lab_count(count: int | None) -> None:
    if count is not None and (type(count) is not int or not 1 <= count <= MAX_LAB_COUNT):
        raise BotError(f'Введіть кількість лабораторних від 1 до {MAX_LAB_COUNT}.')


def validate_planned_minutes(minutes: int | None, *, allow_unknown=True) -> None:
    if minutes is None and allow_unknown:
        return
    if type(minutes) is not int or not 5 <= minutes <= 2**31 - 1:
        raise BotError('Введіть цілу кількість хвилин, не менше 5.')


def format_lab_details(count: int | None, minutes: int | None = None) -> str:
    validate_lab_count(count)
    validate_planned_minutes(minutes)
    parts = []
    if count is not None:
        parts.append(f'{count} лаб.')
    if minutes is not None:
        parts.append(f'≈{minutes} хв')
    return ' · '.join(parts)
