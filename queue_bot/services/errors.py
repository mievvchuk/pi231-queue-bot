class BotError(Exception):
    """Помилка бізнес-логіки. Текст показується користувачу."""


class PermissionDenied(BotError):
    pass


class UnreadableTable(BotError):
    """The photo was delivered and answered, but it is not a readable replacements table."""
