import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from queue_bot import config
from queue_bot.services import recognition
from queue_bot.services.errors import BotError
from queue_bot.handlers.photo_albums import PhotoAlbums


def photo(mid, album='album', chat=100, actor=100):
    event = MagicMock()
    event.message_id = mid
    event.media_group_id = album
    event.chat.id = chat
    event.from_user.id = actor
    event.photo = [MagicMock(file_id=f'photo-{mid}')]
    event.answer = AsyncMock()
    event.answer.return_value.edit_text = AsyncMock()
    return event


@pytest.mark.asyncio
async def test_album_collects_two_parts_once_in_message_order():
    collector = PhotoAlbums(quiet_seconds=0.02)
    second, first = photo(2), photo(1)
    results = await asyncio.gather(collector.collect(second), collector.collect(first))
    assert results == [[first, second], None]
    assert await collector.collect(first) is None
    assert not collector.pending


@pytest.mark.asyncio
async def test_single_photo_is_immediate_and_albums_are_isolated():
    collector = PhotoAlbums(quiet_seconds=0.02)
    single = photo(1, album=None)
    assert await collector.collect(single) == [single]
    a, b, c = photo(2), photo(3, chat=101), photo(4, album='other')
    assert await asyncio.gather(*(collector.collect(x) for x in (a, b, c))) == [[a], [b], [c]]


@pytest.mark.asyncio
async def test_more_than_two_photos_rejects_whole_album_without_partial_processing():
    collector = PhotoAlbums(quiet_seconds=0.02)
    results = await asyncio.gather(*(collector.collect(photo(i)) for i in (1, 2, 3)), return_exceptions=True)
    assert isinstance(results[0], BotError)
    assert results[1:] == [None, None]
    assert not collector.pending


@pytest.mark.asyncio
async def test_duplicate_update_does_not_count_as_second_image():
    collector = PhotoAlbums(quiet_seconds=0.02)
    first = photo(1)
    assert await asyncio.gather(collector.collect(first), collector.collect(first)) == [[first], None]


@pytest.mark.asyncio
async def test_second_part_arriving_during_wait_is_included():
    collector = PhotoAlbums(quiet_seconds=0.04)
    first, second = photo(1), photo(2)
    task = asyncio.create_task(collector.collect(first))
    await asyncio.sleep(0.01)
    assert await collector.collect(second) is None
    assert await task == [first, second]


@pytest.mark.asyncio
async def test_cancelled_collection_does_not_leave_pending_album():
    collector = PhotoAlbums(quiet_seconds=1)
    task = asyncio.create_task(collector.collect(photo(1)))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not collector.pending


@pytest.mark.asyncio
async def test_late_unknown_part_explains_how_to_retry():
    collector = PhotoAlbums(quiet_seconds=0.01)
    await collector.collect(photo(1))
    with pytest.raises(BotError, match='новим альбомом'):
        await collector.collect(photo(2))


@pytest.mark.asyncio
@pytest.mark.parametrize('images', [[], [b'1', b'2', b'3'], [b''], ['text']])
async def test_recognition_rejects_invalid_photo_batches_before_api(images):
    with pytest.raises(BotError):
        await recognition.recognize_replacements_from_images(images)


@pytest.mark.asyncio
async def test_both_images_share_one_vision_request_and_header_context(monkeypatch):
    monkeypatch.setattr(config, 'RECOGNITION_API_KEY', 'offline-test')
    monkeypatch.setattr(config, 'RECOGNITION_PROVIDER', 'groq')
    payload = {'date': '2026-10-30', 'confidence': .99,
               'scheduleOverrides': [{'targetPair': 4, 'sourceDay': 'thursday', 'sourcePair': 1, 'weekType': 'denominator'}],
               'replacements': [{'group': 'ПІ-231', 'lessonNumber': 3, 'lessons': '5-6', 'newSubject': 'Філософія', 'teacher': 'Багас', 'room': '138'}]}
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={'choices': [{'message': {'content': json.dumps(payload)}}]})
    request_context = MagicMock()
    request_context.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.post.return_value = request_context
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    monkeypatch.setattr(recognition.aiohttp, 'ClientSession', lambda: session_context)
    result = await recognition.recognize_replacements_from_images([b'header', b'continuation'])
    assert [(x['lesson_number'], x['new_subject']) for x in result['replacements']] == [(3, 'Філософія'), (4, 'Людино-машинна взаємодія')]
    session.post.assert_called_once()
    content = session.post.call_args.kwargs['json']['messages'][0]['content']
    assert [x['image_url']['url'] for x in content if x['type'] == 'image_url'] == [
        'data:image/jpeg;base64,' + base64.b64encode(x).decode() for x in (b'header', b'continuation')]


@pytest.mark.asyncio
async def test_handler_album_downloads_both_and_creates_one_preview(conn, monkeypatch):
    from queue_bot.handlers import schedule as handlers
    monkeypatch.setattr(handlers, '_PHOTO_ALBUMS', PhotoAlbums(quiet_seconds=0.02))
    monkeypatch.setattr(handlers, '_PENDING_RECOGNITIONS', {})
    recognize = AsyncMock(return_value={'confidence': .99, 'replacements': [
        {'date': '2026-10-30', 'group_name': 'ПІ-231', 'lesson_number': 3, 'new_subject': 'Філософія'}]})
    monkeypatch.setattr(recognition, 'recognize_replacements_from_images', recognize)
    first, second = photo(1), photo(2)
    bot = MagicMock()
    bot.get_file = AsyncMock(side_effect=lambda fid: MagicMock(file_path=fid))

    async def download(path, destination):
        destination.write(path.encode())
    bot.download_file = AsyncMock(side_effect=download)
    first.bot = second.bot = bot
    await asyncio.gather(handlers.handle_photo_replacement(first, conn), handlers.handle_photo_replacement(second, conn))
    recognize.assert_awaited_once_with([b'photo-1', b'photo-2'], group_name=config.STUDENT_GROUP, conn=conn)
    assert bot.download_file.await_count == 2
    assert len(handlers._PENDING_RECOGNITIONS) == 1
    assert first.answer.await_count + second.answer.await_count == 1
    first.answer.return_value.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_nonadmin_photos_do_not_enter_collector(conn, monkeypatch):
    from queue_bot.handlers import schedule as handlers
    collector = PhotoAlbums(quiet_seconds=0.02)
    monkeypatch.setattr(handlers, '_PHOTO_ALBUMS', collector)
    await handlers.handle_photo_replacement(photo(1, actor=999), conn)
    assert not collector.pending


@pytest.mark.asyncio
async def test_bad_photo_date_keeps_batch_editable_and_prevents_partial_publication(conn, monkeypatch):
    from queue_bot.handlers import schedule as handlers
    from queue_bot.services import replacements, updates

    monkeypatch.setattr(replacements, 'get_today_key', lambda: '2026-11-23')
    broadcast = AsyncMock()
    monkeypatch.setattr(updates, 'broadcast_message_to_users', broadcast)
    items = [
        dict(date='2026-11-24', lesson_number=2, new_subject='Людино-машинна взаємодія'),
        dict(date='2020-11-24', lesson_number=4, new_subject='Перездача заборгованостей'),
    ]
    monkeypatch.setattr(handlers, '_PENDING_RECOGNITIONS', {'photo': items})
    callback = MagicMock(data='rec_cf:photo', from_user=MagicMock(id=100), answer=AsyncMock())
    callback.message.edit_text = AsyncMock()
    state = MagicMock(clear=AsyncMock(), get_data=AsyncMock(return_value={
        'token': 'photo', 'idx': 1, 'field': 'date',
    }))
    await handlers.confirm_recognition(callback, state, conn)
    assert '24.11.2020' in callback.answer.call_args.args[0]
    assert conn.execute('SELECT COUNT(*) FROM replacements').fetchone()[0] == 0
    broadcast.assert_not_awaited()
    assert handlers._PENDING_RECOGNITIONS['photo'] is items

    state.clear.reset_mock()
    message = MagicMock(text='30.02.2026', from_user=MagicMock(id=100), answer=AsyncMock())
    await handlers.process_field_value_text(message, state, conn)
    assert 'коректну дату' in message.answer.call_args.args[0]
    assert items[1]['date'] == '2020-11-24'
    state.clear.assert_not_awaited()

    message.text = '24.11.2026'
    await handlers.process_field_value_text(message, state, conn)
    await handlers.confirm_recognition(callback, state, conn)
    await asyncio.sleep(0)
    assert [r['lesson_number'] for r in replacements.get_replacements(conn, date='2026-11-24')] == [2, 4]
    broadcast.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('day, weekday', [(24, 'Вівторок'), (28, 'Субота')])
async def test_replacement_date_editor_opens_and_applies_next_working_day(conn, monkeypatch, day, weekday):
    from datetime import date

    from queue_bot.handlers import schedule as handlers

    next_working_day = date(2026, 11, day)
    monkeypatch.setattr(handlers.working_days, 'get_next_working_day', lambda _: next_working_day)
    monkeypatch.setattr(handlers.replacements, 'get_today_key', lambda: '2026-11-23')
    item = dict(date='2020-11-24', lesson_number=2, new_subject='Людино-машинна взаємодія')
    monkeypatch.setattr(handlers, '_PENDING_RECOGNITIONS', {'photo': [item]})
    callback = MagicMock(data='rec_fld:photo:0:date', from_user=MagicMock(id=100), answer=AsyncMock())
    callback.message.edit_text = AsyncMock()
    state = MagicMock(clear=AsyncMock())

    await handlers.edit_field_prompt_handler(callback, state, conn)

    keyboard = callback.message.edit_text.call_args.kwargs['reply_markup'].inline_keyboard
    assert weekday in keyboard[0][0].text
    assert keyboard[2][0].callback_data == 'rec_txtdate:photo:0'
    callback.data = keyboard[0][0].callback_data
    await handlers.set_date_value_handler(callback, conn)
    assert item['date'] == next_working_day.isoformat()
