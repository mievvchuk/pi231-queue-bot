from aiohttp import web

from queue_bot.services import journal
from queue_bot.services.errors import BotError


async def handle(request):
    from . import api
    ctx, uid = request[api.REQ_CONTEXT], request[api.REQ_UID]
    if request.method == 'GET':
        group = request.query.get('scope') == 'group'
        if group:
            api.require_admin(request)
        return web.json_response(journal.view(ctx['conn'], uid, ctx['subjects'], group=group,
                                              period=request.query.get('period')))
    data = await api.body(request)
    action = data.get('action', 'submit')
    if action == 'submit':
        journal.submit(ctx['conn'], uid, ctx['subjects'], data, admin_mode=request[api.REQ_ADMIN])
        message = 'Оцінку збережено.'
    elif action == 'date':
        api.require_admin(request)
        journal.set_date(ctx['conn'], uid, ctx['subjects'], data)
        message = 'Дату атестації збережено.'
    else:
        raise BotError('Невідома дія журналу.')
    return web.json_response({'message': message})


def setup(app):
    app.router.add_get('/api/miniapp/journal', handle)
    app.router.add_post('/api/miniapp/journal', handle)
