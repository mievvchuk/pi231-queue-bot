"""Ordered Telegram router assembly."""
from aiogram import Dispatcher


def ordered_routers():
    from queue_bot.handlers import navigation, bans, start, polls, admin, schedule, student
    return (navigation.router, bans.router, start.router, polls.router, admin.router,
            schedule.router, student.router, start.fallback_router)


def create_dispatcher(conn, subjects):
    from queue_bot.handlers.common import Guard
    dp = Dispatcher(conn=conn, subjects=subjects)
    dp.message.outer_middleware(Guard())
    dp.callback_query.outer_middleware(Guard())
    dp.include_routers(*ordered_routers())
    return dp
