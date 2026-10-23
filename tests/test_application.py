from unittest.mock import AsyncMock, MagicMock
import pytest

from queue_bot import application, config


@pytest.mark.asyncio
async def test_lifecycle_closes_resources_without_external_calls(monkeypatch):
    monkeypatch.setattr(config, 'validate_runtime', lambda: None)
    monkeypatch.setattr(config, 'get_bot_token', lambda: 'offline-token')
    runner = MagicMock(cleanup=AsyncMock())
    conn = MagicMock()
    bot = MagicMock()
    bot.session.close = AsyncMock()
    dp = MagicMock(start_polling=AsyncMock())
    monkeypatch.setattr(application, 'run_web_server', AsyncMock(return_value=runner))
    monkeypatch.setattr(application, 'connect', lambda target: conn)
    monkeypatch.setattr(application, 'init_admins', lambda *args: None)
    monkeypatch.setattr(application, 'load_subjects', lambda *args, **kwargs: [])
    monkeypatch.setattr(application, 'create_dispatcher', lambda *args: dp)
    monkeypatch.setattr(application, 'Bot', lambda *args, **kwargs: bot)
    from queue_bot.services import updates, schedule_notifier, polls
    monkeypatch.setattr(updates, 'check_and_broadcast_updates', AsyncMock())
    monkeypatch.setattr(schedule_notifier, 'start_schedule_notifier', AsyncMock())
    monkeypatch.setattr(polls, 'start_polls_watcher', AsyncMock())
    await application.main()
    dp.start_polling.assert_awaited_once_with(bot, close_bot_session=False)
    bot.session.close.assert_awaited_once()
    runner.cleanup.assert_awaited_once()
    conn.close.assert_called_once()


def test_legacy_data_file_takes_priority_and_custom_path_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    monkeypatch.delenv('TEST_DATA_PATH', raising=False)
    legacy = tmp_path / 'schedule.json'
    legacy.write_text('{}')
    assert config.data_path('TEST_DATA_PATH', 'schedule.json') == str(legacy)
    monkeypatch.setenv('TEST_DATA_PATH', 'persist/custom.json')
    assert config.data_path('TEST_DATA_PATH', 'schedule.json') == str(tmp_path / 'persist/custom.json')


@pytest.mark.parametrize('key,value', [('OWNER_ID', 0), ('NOTIFICATION_TIME', '24:90'), ('TIMEZONE', 'Invalid/Zone'), ('RECOGNITION_PROVIDER', 'unknown')])
def test_invalid_runtime_config_fails_before_side_effects(monkeypatch, key, value):
    monkeypatch.setattr(config, 'DATABASE_URL', ':memory:')
    monkeypatch.setenv('BOT_TOKEN', 'offline-token')
    monkeypatch.setattr(config, 'OWNER_ID', 100)
    monkeypatch.setattr(config, key, value)
    with pytest.raises(RuntimeError):
        config.validate_runtime()


def test_blank_initial_admins_use_configured_owner(monkeypatch):
    monkeypatch.setattr(config, 'OWNER_ID', 123)
    monkeypatch.setenv('INITIAL_ADMIN_IDS', '')
    assert config.get_initial_admin_ids() == [123]
