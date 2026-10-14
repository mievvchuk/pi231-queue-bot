import asyncio
import logging

from aiogram import Bot, Dispatcher

import config
from db import connect
from handlers import admin, start, student
from handlers.common import Guard
from services.admins import init_admins
from subjects import load_subjects


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    conn = connect(config.DB_PATH)
    init_admins(conn, config.get_initial_admin_ids())

    # conn і subjects доступні в обробниках як іменовані параметри
    dp = Dispatcher(conn=conn, subjects=load_subjects(config.SUBJECTS_PATH))
    dp.message.outer_middleware(Guard())
    dp.callback_query.outer_middleware(Guard())
    dp.include_routers(start.router, student.router, admin.router, start.fallback_router)

    bot = Bot(config.get_bot_token())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
