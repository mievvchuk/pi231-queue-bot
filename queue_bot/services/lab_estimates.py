"""Planned workload per queue entry; duration is a rough estimate, not a wait time."""
from queue_bot import config
from queue_bot.services.errors import BotError

MAX_LAB_COUNT = 20


def validate_lab_count(count: int | None) -> None:
    if count is not None and (type(count) is not int or not 1 <= count <= MAX_LAB_COUNT):
        raise BotError(f'Введіть кількість лабораторних від 1 до {MAX_LAB_COUNT}.')


def format_lab_details(count: int | None) -> str:
    if count is None:
        return ''
    validate_lab_count(count)
    low = count * config.LAB_MIN_MINUTES
    high = count * config.LAB_MAX_MINUTES
    if count > 1:
        low = max(5, ((low + 2) // 5) * 5)
    low = min(low, high)
    return f'{count} лаб. · ≈{low}–{high} хв'
