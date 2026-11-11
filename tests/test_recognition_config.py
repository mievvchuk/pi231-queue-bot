from unittest.mock import AsyncMock, MagicMock
import pytest
from queue_bot import config
from queue_bot.services import recognition


@pytest.mark.asyncio
async def test_recognition_uses_configured_provider_model_endpoint_and_timeout(monkeypatch):
    monkeypatch.setattr(config, 'RECOGNITION_API_KEY', 'test-key-without-provider-prefix')
    monkeypatch.setattr(config, 'RECOGNITION_PROVIDER', 'groq')
    monkeypatch.setattr(config, 'GROQ_API_URL', 'https://example.invalid/vision')
    monkeypatch.setattr(config, 'GROQ_MODEL', 'configured-vision-model')
    monkeypatch.setattr(config, 'RECOGNITION_TIMEOUT_SECONDS', 17)
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={'choices': [{'message': {'content': '{"date":"2026-10-26","confidence":0.99,"replacements":[{"group":"ПІ-231","lessonNumber":1,"newSubject":"Тест"}]}'}}]})
    response_context = MagicMock()
    response_context.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.post.return_value = response_context
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    monkeypatch.setattr(recognition.aiohttp, 'ClientSession', lambda: session_context)
    await recognition.recognize_replacements_from_image(b'offline-test')
    args, kwargs = session.post.call_args
    assert args[0] == 'https://example.invalid/vision'
    assert kwargs['json']['model'] == 'configured-vision-model'
    assert kwargs['timeout'].total == 17


@pytest.mark.asyncio
async def test_payment_required_explains_balance_and_request_caps_tokens(monkeypatch):
    from queue_bot.services.errors import BotError
    monkeypatch.setattr(config, 'RECOGNITION_API_KEY', 'sk-or-test')
    monkeypatch.setattr(config, 'RECOGNITION_PROVIDER', 'openai')
    response = MagicMock(status=402)
    response.text = AsyncMock(return_value='{"error":{"message":"Insufficient credits"}}')
    response_context = MagicMock()
    response_context.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.post.return_value = response_context
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    monkeypatch.setattr(recognition.aiohttp, 'ClientSession', lambda: session_context)
    with pytest.raises(BotError, match='закінчились кошти'):
        await recognition.recognize_replacements_from_image(b'offline-test')
    assert session.post.call_args.kwargs['json']['max_tokens'] == 900
