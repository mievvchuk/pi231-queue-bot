"""Student HTTP workflows shared with the Telegram bot's domain services."""
import html
import base64
import binascii
from contextlib import suppress
from aiohttp import web
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BufferedInputFile
from queue_bot.services import admins, bugs, queues, subjects, users
from queue_bot.services.errors import BotError


def text(value, label, *, maximum=2000, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise BotError(f'{label}: введіть від {minimum} до {maximum} символів.')
    return value.strip()


def public_catalog(ctx):
    return [{'id':s['id'], 'name':s['name'], 'teachers':[{'id':t['id'],'name':t['name']} for t in s['teachers']]} for s in ctx['subjects']]


async def catalog(request):
    from . import api
    ctx = request[api.REQ_CONTEXT]
    catalog = public_catalog(ctx)
    if not request[api.REQ_ADMIN]:
        from queue_bot.services.subgroups import available_to
        user = users.get_user(ctx['conn'], request[api.REQ_UID])
        subgroup = user['subgroup'] if user else None
        for subject in catalog:
            subject['teachers'] = [teacher for teacher in subject['teachers']
                if available_to({'subject_id': subject['id'], 'teacher_id': teacher['id']}, subgroup)]
        catalog = [subject for subject in catalog if subject['teachers']]
    return web.json_response({'subjects':catalog})


async def register(request):
    from . import api
    ctx,uid=request[api.REQ_CONTEXT],request[api.REQ_UID]
    data=await api.body(request)
    user=users.register_user(ctx['conn'],uid,text(data.get('full_name'),'Прізвище та ім’я',maximum=100))
    from queue_bot.handlers.common import notify_admins_user_registered
    with suppress(Exception):
        await notify_admins_user_registered(ctx['bot'],ctx['conn'],uid,user['full_name'])
    return web.json_response({'message':'Реєстрацію завершено.'})


async def create_queue(request):
    from . import api
    ctx,uid=request[api.REQ_CONTEXT],request[api.REQ_UID]
    data=await api.body(request)
    subject=subjects.find_subject(ctx['subjects'],api.integer(data.get('subject_id')))
    if not subject:
        raise BotError('Предмет не знайдено.')
    teacher=subjects.find_teacher(subject,api.integer(data.get('teacher_id')))
    if not teacher:
        raise BotError('Викладач не належить цьому предмету.')
    if not request[api.REQ_ADMIN]:
        from queue_bot.services.subgroups import require_queue_access
        require_queue_access(ctx['conn'], uid, {'subject_id': subject['id'], 'teacher_id': teacher['id']})
    kind=data.get('type','normal')
    if kind not in ('normal','lesson','consultation'):
        raise BotError('Оберіть тип черги.')
    kind='lesson' if kind=='normal' else kind
    if 'Курсова' in subject['name']:
        kind='consultation'
    existing=queues.find_active_session_by_subject_teacher(ctx['conn'],subject['id'],teacher['id'],queue_type=kind)
    if existing:
        return web.json_response({'message':'Ця черга вже існує. Відкрийте її у списку.','session_id':existing['id']})
    sid=queues.create_queue(ctx['conn'],uid,subject['id'],teacher['id'],queue_type=kind)
    from queue_bot.handlers.common import notify_admins_queue_opened
    with suppress(Exception):
        await notify_admins_queue_opened(ctx['bot'],ctx['conn'],uid,request[api.REQ_USER]['full_name'],subject['name'],teacher['name'])
    return web.json_response({'message':'Чергу створено.','session_id':sid})


async def bug_reports(request):
    from . import api
    ctx,uid=request[api.REQ_CONTEXT],request[api.REQ_UID]
    if request.method=='GET':
        rows=ctx['conn'].execute('SELECT id, text, status, admin_reply, created_at, photo_file_id FROM bug_reports WHERE user_telegram_id = ? ORDER BY id DESC LIMIT 100',(uid,)).fetchall()
        return web.json_response({'items':[{k:r[k] for k in ('id','text','status','admin_reply')} | {'created_at':str(r['created_at']),'has_photo':bool(r['photo_file_id'])} for r in rows]})
    data=await api.body(request)
    photo_id = None
    image = data.get('image')
    if image:
        if not isinstance(image,str) or len(image)>1500000:
            raise BotError('Скріншот має бути до 1 МБ.')
        try:
            raw=base64.b64decode(image.split(',',1)[-1],validate=True)
        except (ValueError,binascii.Error):
            raise BotError('Некоректний файл зображення.')
        if len(raw)>1024*1024 or not (raw.startswith(b'\x89PNG\r\n\x1a\n') or raw.startswith(b'\xff\xd8\xff') or (raw.startswith(b'RIFF') and raw[8:12]==b'WEBP')):
            raise BotError('Оберіть PNG, JPEG або WEBP розміром до 1 МБ.')
        message = await ctx['bot'].send_photo(uid, BufferedInputFile(raw,filename='screenshot.png'),caption='Скріншот до звернення')
        api.revalidate(request)
        photo_id = message.photo[-1].file_id
    description = text(data.get('text') or '', 'Опис проблеми',maximum=2000,minimum=0 if photo_id else 1)
    report_id=bugs.create_bug_report(ctx['conn'],uid,description,photo_file_id=photo_id)
    await bugs.notify_admins_new_bug(ctx['bot'],ctx['conn'],report_id)
    return web.json_response({'message':'Повідомлення про проблему надіслано.','report_id':report_id})


async def proposals(request):
    from . import api
    ctx,uid=request[api.REQ_CONTEXT],request[api.REQ_UID]
    if request.method=='GET':
        rows=[r for r in subjects._load_requests(ctx['conn']).values() if r.get('user_telegram_id')==uid]
        return web.json_response({'items':[{'id':r['id'],'kind':'teacher' if r['type']=='add_teacher' else 'subject','subject_name':r['subject_name'],'teacher_name':r['teacher_name'],'status':r['status']} for r in reversed(rows)]})
    data=await api.body(request)
    kind=data.get('kind')
    if kind not in ('teacher','subject'):
        raise BotError('Оберіть тип пропозиції.')
    teacher_name=text(data.get('teacher_name'),'Викладач',minimum=2,maximum=80)
    sid=None
    if kind=='teacher':
        sid=api.integer(data.get('subject_id'))
        subject=subjects.find_subject(ctx['subjects'],sid)
        if not subject:
            raise BotError('Предмет не знайдено.')
        name=subject['name']
    else:
        name=text(data.get('subject_name'),'Назва предмета',minimum=2,maximum=80)
    requests=subjects._load_requests(ctx['conn'])
    if sum(r.get('user_telegram_id')==uid and r.get('status')=='pending' for r in requests.values())>=10:
        raise BotError('У вас уже є 10 пропозицій без відповіді. Дочекайтеся рішення власника.')
    item=subjects.create_subject_request(ctx['conn'],uid,request[api.REQ_USER]['full_name'],'add_teacher' if kind=='teacher' else 'add_subject',sid,name,teacher_name)
    keyboard=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='Погодити',callback_data=f"subreq_ok:{item['id']}"),
        InlineKeyboardButton(text='Відхилити',callback_data=f"subreq_no:{item['id']}")]])
    for owner in admins.get_owner_ids(ctx['conn']):
        with suppress(Exception):
            await ctx['bot'].send_message(owner,f"📚 Нова пропозиція\n{html.escape(name)} — {html.escape(teacher_name)}\nВід: {html.escape(request[api.REQ_USER]['full_name'])}",reply_markup=keyboard)
    return web.json_response({'message':'Пропозицію передано власнику.','id':item['id']})


async def bug_photo(request):
    from . import api
    ctx,uid=request[api.REQ_CONTEXT],request[api.REQ_UID]
    report=bugs.get_bug_report(ctx['conn'],api.path_id(request,'report_id'))
    if not report:
        raise BotError('Звернення не знайдено.')
    owner=report['user_telegram_id'] != uid
    if owner:
        api.require_admin(request)
        admins.require_owner(ctx['conn'],uid)
    if not report['photo_file_id']:
        raise BotError('Скріншот не додано.')
    file=await ctx['bot'].get_file(report['photo_file_id'])
    api.revalidate(request,owner=owner)
    stream=await ctx['bot'].download_file(file.file_path)
    api.revalidate(request,owner=owner)
    raw=stream.getvalue()
    if len(raw)>10*1024*1024:
        raise BotError('Зображення завелике для перегляду.')
    mime='image/png' if raw.startswith(b'\x89PNG') else 'image/webp' if raw.startswith(b'RIFF') else 'image/jpeg'
    return web.Response(body=raw,content_type=mime,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


def setup(app):
    prefix='/api/miniapp'
    app.router.add_get(prefix+'/catalog',catalog)
    app.router.add_post(prefix+'/register',register)
    app.router.add_get(prefix+'/bugs/{report_id}/photo',bug_photo)
    app.router.add_post(prefix+'/queues',create_queue)
    for path,handler in (('bugs',bug_reports),('proposals',proposals)):
        app.router.add_get(prefix+'/'+path,handler)
        app.router.add_post(prefix+'/'+path,handler)
