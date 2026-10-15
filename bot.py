import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import config
from db import connect
from handlers import admin, start, student
from handlers.common import Guard
from services.admins import init_admins
from subjects import load_subjects


import os
from aiohttp import web


BOT_VERSION = "v1.6"


async def healthcheck(request: web.Request) -> web.Response:
    return web.Response(text=f"PI-231 Queue Bot is running! [{BOT_VERSION}]")


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
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Please specify Neon PostgreSQL URL in .env")
    conn = connect(config.DATABASE_URL)
    init_admins(conn, config.get_initial_admin_ids())

    # conn і subjects доступні в обробниках як іменовані параметри
    dp = Dispatcher(conn=conn, subjects=load_subjects(config.SUBJECTS_PATH))
    dp.message.outer_middleware(Guard())
    dp.callback_query.outer_middleware(Guard())
    dp.include_routers(start.router, admin.router, student.router, start.fallback_router)

    bot = Bot(config.get_bot_token(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await run_web_server()

    from services.updates import check_and_broadcast_updates
    asyncio.create_task(check_and_broadcast_updates(bot, conn))

    if getattr(config, "ALERTS_TOKEN", None):
        from services.alerts import start_alerts_checker
        db_target = config.DATABASE_URL or getattr(config, "SQLITE_PATH", "bot.db")
        asyncio.create_task(start_alerts_checker(bot, db_target, config.ALERTS_TOKEN))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

