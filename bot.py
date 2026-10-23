"""Hosting-compatible entrypoint. Application code lives in queue_bot/."""
import asyncio
from queue_bot.application import main

if __name__ == '__main__':
    asyncio.run(main())
