from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from queue_bot.mini_app import api
from queue_bot.services import restrictions
from tests.test_mini_app import client, headers


def photos(client):
    bot = client.app[api.CONTEXT]['bot']
    image = BytesIO()
    Image.new('RGB', (32, 32), '#0a84ff').save(image, format='PNG')
    bot.get_me.return_value = SimpleNamespace(id=999)
    bot.get_user_profile_photos.return_value = SimpleNamespace(photos=[[SimpleNamespace(file_id='private-photo', file_size=1000)]])
    bot.get_file.return_value = SimpleNamespace(file_path='photos/test.png')
    bot.download_file.side_effect = lambda *args, **kw: BytesIO(image.getvalue())
    return bot


@pytest.mark.asyncio
async def test_avatar_proxy_uses_signed_identity_and_caches_private_bytes(client):
    bot = photos(client)
    result = await client.get('/api/miniapp/avatar/me', headers=headers(1))
    assert result.status == 200 and result.content_type == 'image/jpeg'
    assert result.headers['Cache-Control'] == 'no-store'
    assert Image.open(BytesIO(await result.read())).size == (32, 32)
    bot.get_user_profile_photos.assert_awaited_once_with(user_id=1, limit=1)
    assert (await client.get('/api/miniapp/avatar/me?user_id=2', headers=headers(1))).status == 200
    assert bot.get_user_profile_photos.await_count == 1
    assert (await client.get('/api/miniapp/avatar/bot', headers=headers(1))).status == 200
    assert bot.get_user_profile_photos.await_args.kwargs['user_id'] == 999
    assert (await client.get('/api/miniapp/avatar/2', headers=headers(1))).status == 400
    assert (await client.get('/api/miniapp/avatar/me')).status == 401


@pytest.mark.asyncio
async def test_no_photo_and_provider_failure_keep_initials_available(client):
    bot = photos(client)
    bot.get_user_profile_photos.return_value = SimpleNamespace(photos=[])
    assert (await client.get('/api/miniapp/avatar/me', headers=headers(1))).status == 204
    bot.get_user_profile_photos.side_effect = RuntimeError('offline')
    assert (await client.get('/api/miniapp/avatar/bot', headers=headers(1))).status == 204


@pytest.mark.asyncio
async def test_avatar_is_not_returned_after_late_ban(client, conn):
    bot = photos(client)
    original = bot.download_file.side_effect
    def late_ban(*args, **kwargs):
        uid = conn.execute('SELECT id FROM users WHERE telegram_id = 1').fetchone()[0]
        restrictions.set_restriction(conn, 100, uid, days=1)
        return original(*args, **kwargs)
    bot.download_file.side_effect = late_ban
    assert (await client.get('/api/miniapp/avatar/me', headers=headers(1))).status == 403

@pytest.mark.asyncio
async def test_bot_avatar_can_use_chat_photo_when_album_is_empty(client):
    bot = photos(client)
    bot.get_user_profile_photos.return_value = SimpleNamespace(photos=[])
    bot.get_chat.return_value = SimpleNamespace(photo=SimpleNamespace(small_file_id='bot-chat-photo'))
    result = await client.get('/api/miniapp/avatar/bot', headers=headers(1))
    assert result.status == 200
    bot.get_chat.assert_awaited_once_with(999)
    bot.get_file.assert_awaited_once_with('bot-chat-photo')


@pytest.mark.asyncio
async def test_student_profile_is_admin_only_and_reuses_private_photo_cache(client, conn):
    bot = photos(client)
    bot.get_chat.return_value = SimpleNamespace(username='student_example')
    uid = conn.execute('SELECT id FROM users WHERE telegram_id=1').fetchone()[0]
    path = f'/api/miniapp/admin/users/{uid}'
    assert (await client.get(path+'/telegram?mode=admin', headers=headers(1))).status == 403
    assert (await client.get(path+'/telegram', headers=headers(100))).status == 403
    result = await client.get(path+'/telegram?mode=admin', headers=headers(100))
    assert result.status == 200
    assert await result.json() == dict(username='student_example', has_photo=True, username_available=True)
    result = await client.get(path+'/avatar?mode=admin', headers=headers(100))
    assert result.status == 200 and result.content_type == 'image/jpeg'
    assert result.headers['Cache-Control'] == 'no-store'
    bot.get_chat.assert_awaited_once_with(1)
    bot.get_user_profile_photos.assert_awaited_once_with(user_id=1, limit=1)


@pytest.mark.asyncio
async def test_student_profile_unavailable_and_missing_target_are_safe(client, conn):
    bot = photos(client)
    bot.get_chat.side_effect = RuntimeError('private')
    bot.get_user_profile_photos.side_effect = RuntimeError('private')
    uid = conn.execute('SELECT id FROM users WHERE telegram_id=1').fetchone()[0]
    result = await client.get(f'/api/miniapp/admin/users/{uid}/telegram?mode=admin', headers=headers(100))
    assert await result.json() == dict(username=None, has_photo=False, username_available=False)
    bot.get_chat.reset_mock()
    result = await client.get('/api/miniapp/admin/users/999999/telegram?mode=admin', headers=headers(100))
    assert result.status == 400
    bot.get_chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_student_photo_not_returned_after_admin_loses_permission(client, conn):
    bot = photos(client)
    original = bot.download_file.side_effect
    def late_ban(*args, **kwargs):
        uid = conn.execute('SELECT id FROM users WHERE telegram_id=100').fetchone()[0]
        conn.execute('UPDATE users SET is_blocked=1 WHERE id=?', (uid,))
        conn.commit()
        return original(*args, **kwargs)
    bot.download_file.side_effect = late_ban
    uid = conn.execute('SELECT id FROM users WHERE telegram_id=1').fetchone()[0]
    result = await client.get(f'/api/miniapp/admin/users/{uid}/avatar?mode=admin', headers=headers(100))
    assert result.status == 403


@pytest.mark.asyncio
async def test_placeholder_student_does_not_contact_telegram(client, conn):
    from queue_bot.services import users
    bot = photos(client)
    student = users.admin_create_placeholder(conn, 100, 'Тест Безтелеграмний')
    result = await client.get(f"/api/miniapp/admin/users/{student['id']}/telegram?mode=admin", headers=headers(100))
    assert await result.json() == dict(username=None, username_available=False, has_photo=False)
    bot.get_chat.assert_not_awaited()
    bot.get_user_profile_photos.assert_not_awaited()
