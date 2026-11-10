import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from queue_bot import config
from queue_bot.services import channel_import as channel, recognition
from queue_bot.services.errors import BotError


def test_album_and_untrusted_images():
    page = '''<div class="tgme_widget_message" data-post="replacements_xpk/828">
    <a class="tgme_widget_message_photo_wrap" style="background-image:url('https://cdn4.telesco.pe/file/a')"></a>
    <a class="tgme_widget_message_photo_wrap" style="background-image:url('https://cdn4.telesco.pe/file/b')"></a>
    <a class="tgme_widget_message_photo_wrap" style="background-image:url('https://evil.example/a')"></a></div>'''
    posts = channel.parse_posts(page, 'replacements_xpk')
    assert len(posts) == 1
    assert len(posts[0]['photos']) == 2


@pytest.mark.asyncio
async def test_poll_persists_draft_and_does_not_repeat(conn, monkeypatch):
    monkeypatch.setattr(channel, 'read_bounded', AsyncMock(side_effect=lambda _, url, limit: b'page' if url.startswith('https://t.me/s/') else b'\xff\xd8\xffimage'))
    monkeypatch.setattr(channel, 'parse_posts', lambda *_: [dict(id=828, photos=['image'], url='https://t.me/replacements_xpk/828')])
    recognize = AsyncMock(return_value=dict(date='2026-11-09', replacements=[], confidence=.95))
    monkeypatch.setattr(recognition, 'recognize_replacements_from_images', recognize)
    bot = MagicMock(send_message=AsyncMock())
    await channel.poll_once(bot, conn, None)
    await channel.poll_once(bot, conn, None)
    assert recognize.await_count == 1
    assert bot.send_message.await_count == 1
    assert 'У групи ПІ-231 замін немає. 📅 09.11.2026' in bot.send_message.call_args.args[1]
    assert channel.get_state(conn, 'channel_draft:replacements_xpk:828')['status'] == 'pending'
    assert conn.execute('SELECT COUNT(*) FROM replacements').fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('readable,absent,confidence,accepted', [(True,True,.95,True),(False,True,.95,False),(True,False,.95,False),(True,True,.6,False)])
async def test_empty_requires_explicit_readable_table(conn, monkeypatch, readable, absent, confidence, accepted):
    monkeypatch.setattr(config, 'RECOGNITION_API_KEY', 'test-key')
    monkeypatch.setattr(config, 'RECOGNITION_PROVIDER', 'openai')
    result = dict(date='2026-11-09', replacements=[], confidence=confidence, tableReadable=readable, noReplacements=absent)
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={'choices':[{'message':{'content':json.dumps(result)}}]})
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.post.return_value = request
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    monkeypatch.setattr(recognition.aiohttp, 'ClientSession', lambda: context)
    if accepted:
        assert (await recognition.recognize_replacements_from_images([b'a', b'b'], conn=conn, allow_empty=True))['replacements'] == []
        assert len(session.post.call_args.kwargs['json']['messages'][0]['content']) == 3
    else:
        with pytest.raises(BotError):
            await recognition.recognize_replacements_from_images([b'a'], conn=conn, allow_empty=True)


@pytest.mark.asyncio
async def test_confirm_empty_clears_date_once(conn, monkeypatch):
    from queue_bot.handlers.schedule import confirm_channel_import
    from queue_bot.services import updates, replacements
    monkeypatch.setattr(config, 'OWNER_ID', 100)
    draft = dict(date='2026-11-09', replacements=[], status='pending', source='https://t.me/replacements_xpk/828')
    channel.set_state(conn, 'channel_draft:replacements_xpk:828', draft)
    replacements.save_replacement(conn, dict(date='2026-11-09', group_name='ПІ-231', lesson_number=1, new_subject='Предмет'))
    broadcast = AsyncMock()
    monkeypatch.setattr(updates, 'broadcast_message_to_users', broadcast)
    monkeypatch.setattr(updates, 'get_active_user_ids', lambda _: [200])
    callback = MagicMock(data='ch_yes:828', from_user=MagicMock(id=100), answer=AsyncMock())
    callback.message.edit_text = AsyncMock()
    await confirm_channel_import(callback, conn)
    import asyncio
    await asyncio.sleep(0)
    assert replacements.get_replacements(conn, date='2026-11-09') == []
    assert channel.get_state(conn, 'channel_draft:replacements_xpk:828')['status'] == 'confirmed'
    await confirm_channel_import(callback, conn)
    assert broadcast.await_count == 1
    assert broadcast.call_args_list[0].args[2] == "У групи ПІ-231 замін немає. 📅 09.11.2026"


@pytest.mark.asyncio
async def test_unauthorized_cannot_confirm(conn, monkeypatch):
    from queue_bot.handlers.schedule import confirm_channel_import
    callback = MagicMock(from_user=MagicMock(id=999), answer=AsyncMock())
    await confirm_channel_import(callback, conn)
    callback.answer.assert_awaited_once()
    assert callback.answer.call_args.kwargs['show_alert'] is True


@pytest.mark.asyncio
async def test_download_refreshes_embed_after_cdn_500(monkeypatch):
    import aiohttp
    error = aiohttp.ClientResponseError(MagicMock(), (), status=500)
    page = b'''<div class="tgme_widget_message" data-post="replacements_xpk/828"><a class="tgme_widget_message_photo_wrap" style="background-image:url('https://cdn4.telesco.pe/file/new')"></a></div>'''
    read = AsyncMock(side_effect=[error, page, b'\xff\xd8\xffimage'])
    monkeypatch.setattr(channel, 'read_bounded', read)
    monkeypatch.setattr(channel.asyncio, 'sleep', AsyncMock())
    post = dict(id=828, photos=['https://cdn4.telesco.pe/file/old'], url='https://t.me/replacements_xpk/828')
    assert await channel.download_post_images(None, post, 'replacements_xpk') == [b'\xff\xd8\xffimage']
    assert read.call_args_list[-1].args[1] == 'https://cdn4.telesco.pe/file/new'


@pytest.mark.asyncio
async def test_cdn_failure_never_advances_cursor_or_calls_ocr(conn, monkeypatch):
    monkeypatch.setattr(channel, 'read_bounded', AsyncMock(return_value=b'page'))
    monkeypatch.setattr(channel, 'parse_posts', lambda *_: [dict(id=828, photos=['image'], url='https://t.me/replacements_xpk/828')])
    monkeypatch.setattr(channel, 'download_post_images', AsyncMock(side_effect=BotError('CDN HTTP 500')))
    recognize = AsyncMock()
    monkeypatch.setattr(recognition, 'recognize_replacements_from_images', recognize)
    with pytest.raises(BotError):
        await channel.poll_once(MagicMock(send_message=AsyncMock()), conn, None)
    assert channel.get_state(conn, 'channel_cursor:replacements_xpk') is None
    recognize.assert_not_awaited()


def test_edit_parser_validates_and_allows_empty():
    assert channel.parse_edit('09.11.2026\nнемає') == dict(date='2026-11-09', replacements=[])
    result = channel.parse_edit('09.11.2026\n2 | Машинне навчання | Ковальчук | 231')
    assert result['replacements'][0]['lesson_number'] == 2
    for value in ['31.02.2026\nнемає', '09.11.2026', '09.11.2026\n9 | X | - | -', '09.11.2026\n2 | X | - | -\n2 | Y | - | -']:
        with pytest.raises(BotError):
            channel.parse_edit(value)


@pytest.mark.asyncio
async def test_edit_confirmed_result_requires_new_approval_and_updates_schedule(conn, monkeypatch):
    from queue_bot.handlers.schedule import confirm_channel_import
    from queue_bot.services import updates, schedule
    monkeypatch.setattr(config, 'OWNER_ID', 100)
    key = 'channel_draft:replacements_xpk:828'
    draft = dict(date='2026-11-09', replacements=[], status='confirmed', source='https://t.me/replacements_xpk/828')
    draft['edit'] = channel.parse_edit('09.11.2026\n1 | Виправлений предмет | Викладач | 123')
    channel.set_state(conn, key, draft)
    # Editing a confirmed result must leave the published timetable unchanged.
    assert channel.confirmed_results(conn, '2026-11-09')[0]['replacements'] == []
    assert conn.execute('SELECT COUNT(*) FROM replacements').fetchone()[0] == 0
    broadcast = AsyncMock()
    monkeypatch.setattr(updates, 'broadcast_message_to_users', broadcast)
    monkeypatch.setattr(updates, 'get_active_user_ids', lambda _: [200])
    from queue_bot.services.schedule_notifier import mark_sent_today
    mark_sent_today(conn, 'evening_2026-11-08')
    cb = MagicMock(data='ch_yes:828', from_user=MagicMock(id=100), answer=AsyncMock())
    cb.message.edit_text = AsyncMock()
    await confirm_channel_import(cb, conn)
    import asyncio, datetime
    await asyncio.sleep(0)
    lessons, _ = schedule.get_actual_schedule(conn, schedule.load_schedule_data(), datetime.date(2026,11,9))
    assert any(item['subject'] == 'Виправлений предмет' for item in lessons)
    assert 'Виправлення попередніх даних' in broadcast.call_args_list[0].args[2]
    assert 'Повний розклад із замінами' in broadcast.call_args_list[1].args[2]
    assert 'Виправлений предмет' in broadcast.call_args_list[1].args[2]
    await confirm_channel_import(cb, conn)
    assert broadcast.await_count == 2


def test_schedule_shows_confirmed_empty_result_for_exact_date(conn):
    import datetime
    from queue_bot.services import schedule
    channel.set_state(conn, 'channel_draft:replacements_xpk:828', dict(date='2026-11-09', replacements=[], status='confirmed'))
    data = schedule.load_schedule_data()
    assert 'У групи ПІ-231 замін немає. 📅 09.11.2026' in schedule.format_schedule_for_date(conn, data, datetime.date(2026,11,9))
    assert 'замін немає' not in schedule.format_schedule_for_date(conn, data, datetime.date(2026,11,10))


@pytest.mark.asyncio
async def test_replacements_menu_shows_empty_date_and_edit_button(conn, monkeypatch):
    from queue_bot.handlers.schedule import show_replacements
    monkeypatch.setattr(config, 'OWNER_ID', 100)
    monkeypatch.setattr(channel.config, 'REPLACEMENTS_CHANNEL', 'replacements_xpk')
    from queue_bot.services import replacements
    monkeypatch.setattr(replacements, 'get_today_key', lambda: '2026-11-06')
    channel.set_state(conn, 'channel_draft:replacements_xpk:828', dict(date='2026-11-09', replacements=[], status='confirmed'))
    message = MagicMock(chat=MagicMock(type='private'), from_user=MagicMock(id=100), answer=AsyncMock())
    await show_replacements(message, conn)
    assert 'У групи ПІ-231 замін немає. 📅 09.11.2026' in message.answer.call_args.args[0]
    assert 'надішліть фотографію' not in message.answer.call_args.args[0]
    assert message.answer.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data == 'ch_edit:828'


@pytest.mark.asyncio
async def test_next_date_confirmation_archives_previous_even_if_new_date_has_no_replacements(conn, monkeypatch):
    from queue_bot.handlers.schedule import confirm_channel_import
    from queue_bot.services import updates, replacements
    monkeypatch.setattr(config, 'OWNER_ID', 100)
    replacements.save_replacement(conn, dict(date='2026-11-09', group_name='ПІ-231', lesson_number=1, new_subject='Попередня заміна'))
    channel.set_state(conn, 'channel_draft:replacements_xpk:830', dict(date='2026-11-10', replacements=[], status='pending', source='https://t.me/replacements_xpk/830'))
    assert len(replacements.get_replacements(conn, date='2026-11-09')) == 1
    monkeypatch.setattr(updates, 'broadcast_message_to_users', AsyncMock())
    monkeypatch.setattr(updates, 'get_active_user_ids', lambda _: [])
    callback = MagicMock(data='ch_yes:830', from_user=MagicMock(id=100), answer=AsyncMock())
    callback.message.edit_text = AsyncMock()
    await confirm_channel_import(callback, conn)
    import asyncio
    await asyncio.sleep(0)
    assert replacements.get_replacements(conn, date='2026-11-09') == []
    assert replacements.get_archived_replacements(conn)[0]['new_subject'] == 'Попередня заміна'


@pytest.mark.asyncio
async def test_full_table_without_group_rows_is_no_replacements(conn, monkeypatch):
    monkeypatch.setattr(config, 'RECOGNITION_API_KEY', 'test-key')
    monkeypatch.setattr(config, 'RECOGNITION_PROVIDER', 'openai')
    result = dict(date='2026-11-09', confidence=.95, tableReadable=True, notes=[], scheduleOverrides=[],
                  rows=[{'group': 'КІ-231', 'lessons': '1-2', 'subject': 'Системне програм'}])
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={'choices':[{'message':{'content':json.dumps(result)}}]})
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.post.return_value = request
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    monkeypatch.setattr(recognition.aiohttp, 'ClientSession', lambda: context)
    assert (await recognition.recognize_replacements_from_images([b'a'], conn=conn, allow_empty=True))['replacements'] == []
    assert channel.format_no_replacements('2026-11-09') == 'У групи ПІ-231 замін немає. 📅 09.11.2026'


@pytest.mark.asyncio
async def test_old_draft_for_upcoming_date_is_asked_again_once(conn, monkeypatch):
    import datetime
    upcoming = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
    past = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    monkeypatch.setattr(channel, 'read_bounded', AsyncMock(side_effect=lambda _, url, limit: b'page' if url.startswith('https://t.me/s/') else b'\xff\xd8\xffimage'))
    monkeypatch.setattr(channel, 'parse_posts', lambda *_: [dict(id=i, photos=['image'], url=f'https://t.me/replacements_xpk/{i}') for i in (830, 837)])
    channel.set_state(conn, 'channel_cursor:replacements_xpk', 837)
    channel.set_state(conn, 'channel_draft:replacements_xpk:830', dict(date=past, replacements=[], status='confirmed', notified=True))
    channel.set_state(conn, 'channel_draft:replacements_xpk:837', dict(date=upcoming, replacements=[], status='confirmed', notified=True))
    recognize = AsyncMock(return_value=dict(date=upcoming, replacements=[], confidence=.95))
    monkeypatch.setattr(recognition, 'recognize_replacements_from_images', recognize)
    bot = MagicMock(send_message=AsyncMock())
    await channel.poll_once(bot, conn, None)
    await channel.poll_once(bot, conn, None)
    assert recognize.await_count == 1 and bot.send_message.await_count == 1
    assert 'Повторне розпізнавання' in bot.send_message.call_args.args[1]
    assert channel.get_state(conn, 'channel_draft:replacements_xpk:837')['status'] == 'pending'
    assert channel.get_state(conn, 'channel_cursor:replacements_xpk') == 837
