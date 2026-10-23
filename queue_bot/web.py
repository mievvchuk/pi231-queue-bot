"""HTTP process health endpoint used by the hosting platform."""
import logging
from aiohttp import web
from queue_bot import __version__, config


async def healthcheck(request: web.Request) -> web.Response:
    return web.Response(text=f'PI-231 Queue Bot is running! [{__version__}]\nStatus: OK\n', content_type='text/plain')


async def run_web_server():
    app = web.Application()
    app.router.add_route('*', '/{tail:.*}', healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, config.HTTP_HOST, config.PORT).start()
    logging.info('Healthcheck web server started on port %s', config.PORT)
    return runner
