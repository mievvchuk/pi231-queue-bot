class BotError(Exception):
    """Помилка бізнес-логіки. Текст показується користувачу."""


class PermissionDenied(BotError):
    pass
