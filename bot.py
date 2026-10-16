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


BOT_VERSION = "v1.9"


async def healthcheck(request: web.Request) -> web.Response:
    return web.Response(
        text=f"PI-231 Queue Bot is running! [{BOT_VERSION}]\nStatus: OK\n",
        content_type="text/plain",
        status=200,
    )


async def run_web_server():
    port = int(os.getenv("PORT", "8080"))
    app = web.Application()
    # Підтримуємо будь-які HTTP методи (GET, HEAD, POST, OPTIONS) на будь-які шляхи (/ , /health, /ping тощо)
    app.router.add_route("*", "/", healthcheck)
    app.router.add_route("*", "/health", healthcheck)
    app.router.add_route("*", "/ping", healthcheck)
    app.router.add_route("*", "/{tail:.*}", healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Healthcheck web server started on port {port}")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    # 1. Запускаємо healthcheck сервер першим ділом, щоб Render/UptimeRobot миттєво отримували 200 OK
    await run_web_server()

    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Please specify Neon PostgreSQL URL in .env")
    conn = connect(config.DATABASE_URL)
    init_admins(conn, config.get_initial_admin_ids())

    from handlers import schedule as schedule_handler

    # conn і subjects доступні в обробниках як іменовані параметри
    dp = Dispatcher(conn=conn, subjects=load_subjects(config.SUBJECTS_PATH))
    dp.message.outer_middleware(Guard())
    dp.callback_query.outer_middleware(Guard())
    dp.include_routers(start.router, admin.router, schedule_handler.router, student.router, start.fallback_router)

    bot = Bot(config.get_bot_token(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    from services.updates import check_and_broadcast_updates
    asyncio.create_task(check_and_broadcast_updates(bot, conn))

    from services.schedule_notifier import start_schedule_notifier
    asyncio.create_task(start_schedule_notifier(bot, conn))

    if getattr(config, "ALERTS_TOKEN", None):
        from services.alerts import start_alerts_checker
        db_target = config.DATABASE_URL or getattr(config, "SQLITE_PATH", "bot.db")
        asyncio.create_task(start_alerts_checker(bot, db_target, config.ALERTS_TOKEN))

    # Стійкий цикл polling: бот автоматично відновлює роботу при тимчасових мережевих збоях
    while True:
        try:
            await dp.start_polling(bot)
        except Exception as e:
            logging.error(f"Telegram polling error: {e}. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())


