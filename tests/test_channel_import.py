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
    import datetime
    upcoming = datetime.date.today() + datetime.timedelta(days=1)
    recognize = AsyncMock(return_value=dict(date=upcoming.isoformat(), replacements=[], confidence=.95))
    monkeypatch.setattr(recognition, 'recognize_replacements_from_images', recognize)
    bot = MagicMock(send_message=AsyncMock())
    await channel.poll_once(bot, conn, None)
    await channel.poll_once(bot, conn, None)
    assert recognize.await_count == 1
    assert bot.send_message.await_count == 1
    assert f'У групи ПІ-231 замін немає. 📅 {upcoming:%d.%m.%Y}' in bot.send_message.call_args.args[1]
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


@pytest.mark.asyncio
async def test_phone_editor_edits_channel_draft_with_buttons(conn, monkeypatch):
    from queue_bot.handlers import schedule as handlers
    monkeypatch.setattr(config, 'OWNER_ID', 100)
    monkeypatch.setattr(handlers.admins, 'is_admin', lambda *_: True)
    item = dict(date='2026-11-11', group_name='ПІ-231', lesson_number=3, new_subject='Системне програм', teacher='Задніпровський', room='232')
    channel.set_state(conn, 'channel_draft:replacements_xpk:837', dict(date='2026-11-11', replacements=[item], status='pending'))
    cb = lambda data: MagicMock(data=data, from_user=MagicMock(id=100), answer=AsyncMock(),
                                message=MagicMock(answer=AsyncMock(), edit_text=AsyncMock()))
    state = MagicMock(clear=AsyncMock())
    await handlers.edit_channel_result(cb('ch_edit:837'), state, conn)
    subjects = handlers.schedule_choices('new_subject')
    pick = subjects.index('Машинне навчання')
    await handlers.pick_field_value_handler(cb(f'rec_pk:ch837:0:new_subject:{pick}'), state, conn)
    await handlers.pick_field_value_handler(cb('rec_pk:ch837:0:room:-1'), state, conn)
    await handlers.set_pair_number_handler(cb('rec_sp:ch837:0:4'), conn)
    saved = cb('ch_sv:837')
    await handlers.save_channel_edit(saved, conn)
    edit = channel.get_state(conn, 'channel_draft:replacements_xpk:837')['edit']
    assert edit['date'] == '2026-11-11'
    assert [(r['lesson_number'], r['new_subject'], r['room']) for r in edit['replacements']] == [(4, 'Машинне навчання', '')]
    assert saved.message.edit_text.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data == 'ch_yes:837'


@pytest.mark.asyncio
async def test_edit_confirmed_result_requires_new_approval_and_updates_schedule(conn, monkeypatch):
    from queue_bot.handlers.schedule import confirm_channel_import
    from queue_bot.services import updates, schedule
    monkeypatch.setattr(config, 'OWNER_ID', 100)
    key = 'channel_draft:replacements_xpk:828'
    draft = dict(date='2026-11-09', replacements=[], status='confirmed', source='https://t.me/replacements_xpk/828')
    draft['edit'] = dict(date='2026-11-09', replacements=[dict(date='2026-11-09', group_name='ПІ-231', lesson_number=1,
                         new_subject='Виправлений предмет', teacher='Викладач', room='123', old_subject=None)])
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
    result = dict(date='2026-11-09', confidence=.95, tableReadable=True, noReplacements=True, notes=[], scheduleOverrides=[], rows=[])
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


@pytest.mark.asyncio
async def test_one_ai_request_per_poll_newest_first(conn, monkeypatch):
    monkeypatch.setattr(channel, 'read_bounded', AsyncMock(side_effect=lambda _, url, limit: b'page' if url.startswith('https://t.me/s/') else b'\xff\xd8\xffimage'))
    monkeypatch.setattr(channel, 'parse_posts', lambda *_: [dict(id=i, photos=['image'], url=f'https://t.me/replacements_xpk/{i}') for i in (840, 841)])
    channel.set_state(conn, 'channel_cursor:replacements_xpk', 839)
    recognize = AsyncMock(return_value=dict(date='2026-11-12', replacements=[], confidence=.95))
    monkeypatch.setattr(recognition, 'recognize_replacements_from_images', recognize)
    bot = MagicMock(send_message=AsyncMock())
    await channel.poll_once(bot, conn, None)
    assert recognize.await_count == 1 and channel.get_state(conn, 'channel_cursor:replacements_xpk') == 840
    await channel.poll_once(bot, conn, None)
    assert recognize.await_count == 2 and channel.get_state(conn, 'channel_cursor:replacements_xpk') == 841


def test_monitor_status_displays_disabled_pending_and_errors(conn, monkeypatch):
    monkeypatch.setattr(config, 'RECOGNITION_PROVIDER', 'disabled')
    monkeypatch.setattr(config, 'REPLACEMENTS_CHANNEL', 'replacements_xpk')
    channel.set_state(conn, 'channel_last_success', '2026-11-17T10:00:00+02:00')
    channel.set_state(conn, 'channel_error_reason', 'CDN <error>')
    channel.set_state(conn, 'channel_draft:replacements_xpk:900', {'status': 'pending'})
    text = channel.monitoring_status(conn)
    assert 'вимкнене' in text and '2026-11-17T10:00' in text
    assert 'CDN &lt;error&gt;' in text
    assert 'Очікують підтвердження: 1' in text


@pytest.mark.asyncio
async def test_successful_check_records_health_even_without_new_posts(conn, monkeypatch):
    monkeypatch.setattr(config, 'REPLACEMENTS_CHANNEL', 'replacements_xpk')
    monkeypatch.setattr(channel, 'read_bounded', AsyncMock(return_value=b'page'))
    monkeypatch.setattr(channel, 'parse_posts', lambda *_: [dict(id=828, photos=['image'])])
    channel.set_state(conn, 'channel_cursor:replacements_xpk', 828)
    await channel.poll_once(AsyncMock(), conn, None)
    assert channel.get_state(conn, 'channel_last_success')
    assert channel.get_state(conn, 'channel_last_attempt')
    assert channel.get_state(conn, 'channel_latest_post') == 828


def _channel_page(monkeypatch, posts):
    monkeypatch.setattr(channel, 'read_bounded', AsyncMock(side_effect=lambda _, url, limit: b'page' if url.startswith('https://t.me/s/') else b'\xff\xd8\xffimage'))
    monkeypatch.setattr(channel, 'parse_posts', lambda *_: [
        dict(id=i, photos=['image'] * n, url=f'https://t.me/replacements_xpk/{i}') for i, n in posts])


@pytest.mark.asyncio
async def test_unreadable_post_is_reported_once_and_does_not_block_later_posts(conn, monkeypatch):
    import datetime
    from queue_bot.services.errors import UnreadableTable
    upcoming = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    _channel_page(monkeypatch, [(844, 1), (845, 1)])
    channel.set_state(conn, 'channel_cursor:replacements_xpk', 842)
    recognize = AsyncMock(side_effect=[UnreadableTable('Не вдалося визначити дату таблиці.'),
                                       dict(date=upcoming, replacements=[], confidence=.95)])
    monkeypatch.setattr(recognition, 'recognize_replacements_from_images', recognize)
    bot = MagicMock(send_message=AsyncMock())
    await channel.poll_once(bot, conn, None)
    assert channel.get_state(conn, 'channel_cursor:replacements_xpk') == 844
    assert channel.get_state(conn, 'channel_draft:replacements_xpk:844')['status'] == 'failed'
    assert 'Допис №844' in bot.send_message.call_args.args[1]
    await channel.poll_once(bot, conn, None)
    await channel.poll_once(bot, conn, None)
    assert recognize.await_count == 2 and bot.send_message.await_count == 2
    assert channel.get_state(conn, 'channel_cursor:replacements_xpk') == 845
    assert channel.get_state(conn, 'channel_draft:replacements_xpk:845')['status'] == 'pending'


@pytest.mark.asyncio
async def test_provider_failure_still_retries_the_same_post(conn, monkeypatch):
    _channel_page(monkeypatch, [(844, 1)])
    channel.set_state(conn, 'channel_cursor:replacements_xpk', 842)
    monkeypatch.setattr(recognition, 'recognize_replacements_from_images',
                        AsyncMock(side_effect=BotError('Помилка OpenRouter API (429): перевищено ліміт запитів.')))
    with pytest.raises(BotError):
        await channel.poll_once(MagicMock(send_message=AsyncMock()), conn, None)
    assert channel.get_state(conn, 'channel_cursor:replacements_xpk') == 842
    assert channel.get_state(conn, 'channel_draft:replacements_xpk:844') is None


@pytest.mark.asyncio
async def test_big_album_is_skipped_without_ai_and_past_tables_are_not_announced(conn, monkeypatch):
    import datetime
    past = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
    _channel_page(monkeypatch, [(849, 4), (855, 2)])
    channel.set_state(conn, 'channel_cursor:replacements_xpk', 848)
    recognize = AsyncMock(return_value=dict(date=past, replacements=[], confidence=.95))
    monkeypatch.setattr(recognition, 'recognize_replacements_from_images', recognize)
    bot = MagicMock(send_message=AsyncMock())
    await channel.poll_once(bot, conn, None)
    assert recognize.await_count == 1  # the four-photo ad cost nothing; the table was read in the same poll
    assert channel.get_state(conn, 'channel_draft:replacements_xpk:849')['status'] == 'skipped'
    assert channel.get_state(conn, 'channel_draft:replacements_xpk:855')['status'] == 'expired'
    assert channel.get_state(conn, 'channel_cursor:replacements_xpk') == 855
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_errors_from_recognition_are_unreadable_table(conn, monkeypatch):
    from queue_bot.services.errors import UnreadableTable
    monkeypatch.setattr(config, 'RECOGNITION_PROVIDER', 'openai')
    monkeypatch.setattr(recognition, '_ask_ai', AsyncMock(return_value=dict(date='', replacements=[], confidence=.9)))
    with pytest.raises(UnreadableTable):
        await recognition.recognize_replacements_from_images([b'a'], conn=conn, allow_empty=True)
    monkeypatch.setattr(recognition, '_ask_ai', AsyncMock(side_effect=BotError('Помилка OpenAI API (500): x.')))
    with pytest.raises(BotError) as raised:
        await recognition.recognize_replacements_from_images([b'a'], conn=conn, allow_empty=True)
    assert not isinstance(raised.value, UnreadableTable)
