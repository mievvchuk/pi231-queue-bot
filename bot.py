import asyncio
import logging

from aiogram import Bot, Dispatcher

import config
from db import connect
from handlers import admin, start, student
from handlers.common import Guard
from services.admins import init_admins
from subjects import load_subjects


import os
from aiohttp import web


async def healthcheck(request: web.Request) -> web.Response:
    return web.Response(text="PI-231 Queue Bot is running!")


async def run_web_server():
    port = int(os.getenv("PORT", "8080"))
    app = web.Application()
    app.router.add_get("/", healthcheck)
    app.router.add_get("/health", healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Healthcheck web server started on port {port}")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    db_target = config.DATABASE_URL or config.DB_PATH
    conn = connect(db_target)
    init_admins(conn, config.get_initial_admin_ids())

    # conn і subjects доступні в обробниках як іменовані параметри
    dp = Dispatcher(conn=conn, subjects=load_subjects(config.SUBJECTS_PATH))
    dp.message.outer_middleware(Guard())
    dp.callback_query.outer_middleware(Guard())
    dp.include_routers(start.router, student.router, admin.router, start.fallback_router)

    bot = Bot(config.get_bot_token())
    await run_web_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

