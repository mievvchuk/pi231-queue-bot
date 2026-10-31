"""Hosting health endpoint plus the additive Telegram Mini App."""
import logging
from pathlib import Path
from aiohttp import web
from queue_bot import __version__, config
from queue_bot.mini_app import api, browser_auth


async def healthcheck(request: web.Request) -> web.Response:
    return web.Response(text=f'PI-231 Queue Bot is running! [{__version__}]\nStatus: OK\n', content_type='text/plain')


async def asset(request):
    name = request.match_info.get('asset') or 'index.html'
    if name not in ('index.html', 'app.js', 'app.css', 'theme.css', 'glass.css', 'icons.js', 'features.js', 'pwa.js', 'manifest.webmanifest', 'sw.js', 'icon-192.png', 'icon-512.png', 'onest-cyrillic.woff2', 'onest-latin.woff2', 'FONT-LICENSE.txt', 'BOOTSTRAP-ICONS-LICENSE.txt'):
        raise web.HTTPNotFound()
    response = web.FileResponse(Path(__file__).parent / 'mini_app' / 'static' / name)
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; connect-src 'self'; font-src 'self'; img-src 'self' data: blob:; base-uri 'none'; object-src 'none'"
    return response


def create_web_app(*, conn=None, subjects=None, bot=None, bot_token=None):
    app = web.Application(middlewares=[browser_auth.no_auth_cache, api.guard], client_max_size=16*1024*1024)
    app[api.CONTEXT] = {'ready': conn is not None, 'conn': conn, 'subjects': subjects or [], 'bot': bot, 'token': bot_token}
    api.setup(app)
    browser_auth.setup(app)
    app.router.add_route('*', '/', healthcheck)
    app.router.add_get('/health', healthcheck)
    app.router.add_get('/app', redirect_to_app)
    app.router.add_get('/app/', asset)
    app.router.add_get('/app/{asset}', asset)
    return app


async def redirect_to_app(request):
    raise web.HTTPFound('/app/')


def configure_mini_app(runner, *, conn, subjects, bot):
    runner.app[api.CONTEXT].update(conn=conn, subjects=subjects, bot=bot, token=config.get_bot_token(), ready=True)


def prepare_shutdown(runner):
    runner.app[api.CONTEXT]['ready'] = False


async def run_web_server():
    app = create_web_app()
    # OIDC callback URLs carry short-lived authorization codes. Keep them out
    # of aiohttp's default access log (application errors log paths only).
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, config.HTTP_HOST, config.PORT).start()
    logging.info('Healthcheck web server started on port %s', config.PORT)
    return runner
