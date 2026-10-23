"""Application lifecycle, separated from domain logic and Telegram UI."""
import asyncio
import logging
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from queue_bot import config
from queue_bot.routing import create_dispatcher
from queue_bot.storage.database import connect
from queue_bot.services.admins import init_admins
from queue_bot.services.subjects import load_subjects
from queue_bot.web import run_web_server


async def main():
    config.validate_runtime()
    logging.basicConfig(level=config.LOG_LEVEL)
    runner = await run_web_server()
    conn = None
    bot = None
    tasks = []
    try:
        conn = connect(config.DATABASE_URL)
        init_admins(conn, config.get_initial_admin_ids())
        dp = create_dispatcher(conn, load_subjects(config.SUBJECTS_PATH, conn=conn))
        bot = Bot(config.get_bot_token(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        from queue_bot.services.updates import check_and_broadcast_updates
        from queue_bot.services.schedule_notifier import start_schedule_notifier
        from queue_bot.services.polls import start_polls_watcher
        tasks = [
            asyncio.create_task(check_and_broadcast_updates(bot, conn)),
            asyncio.create_task(start_schedule_notifier(bot, conn, interval_sec=config.SCHEDULE_CHECK_INTERVAL)),
            asyncio.create_task(start_polls_watcher(bot, conn, interval_sec=config.POLLS_WATCH_INTERVAL)),
        ]
        while True:
            try:
                await dp.start_polling(bot, close_bot_session=False)
                break
            except Exception:
                logging.exception('Telegram polling failed; retrying in %s seconds', config.POLLING_RETRY_SECONDS)
                await asyncio.sleep(config.POLLING_RETRY_SECONDS)
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if bot:
            await bot.session.close()
        if conn is not None:
            conn.close()
        await runner.cleanup()
