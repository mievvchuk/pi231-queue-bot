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


@pytest.mark.asyncio
async def test_nvidia_key_selects_nvidia_endpoint_without_json_mode_and_accepts_fenced_json(monkeypatch):
    monkeypatch.setattr(config, 'RECOGNITION_API_KEY', 'nvapi-test')
    monkeypatch.setattr(config, 'RECOGNITION_PROVIDER', 'auto')
    monkeypatch.setattr(config, 'NVIDIA_API_URL', 'https://example.invalid/nvidia')
    monkeypatch.setattr(config, 'NVIDIA_MODEL', 'vision-model')
    content = '```json\n{"date":"2026-10-26","confidence":0.99,"replacements":[{"group":"ПІ-231","lessonNumber":1,"newSubject":"Тест"}]}\n```'
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={'choices': [{'message': {'content': content}}]})
    response_context = MagicMock()
    response_context.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.post.return_value = response_context
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    monkeypatch.setattr(recognition.aiohttp, 'ClientSession', lambda: session_context)
    await recognition.recognize_replacements_from_image(b'offline-test')
    args, kwargs = session.post.call_args
    assert args[0] == 'https://example.invalid/nvidia'
    assert kwargs['json']['model'] == 'vision-model'
    assert 'response_format' not in kwargs['json']
    image = kwargs['json']['messages'][0]['content'][1]['image_url']
    assert 'detail' not in image


@pytest.mark.asyncio
async def test_retired_model_410_names_the_model_setting(monkeypatch):
    from queue_bot.services.errors import BotError
    monkeypatch.setattr(config, 'RECOGNITION_API_KEY', 'nvapi-test')
    monkeypatch.setattr(config, 'RECOGNITION_PROVIDER', 'nvidia')
    monkeypatch.setattr(config, 'NVIDIA_MODEL', 'old/vision-model')
    response = MagicMock(status=410)
    response.text = AsyncMock(return_value='{"detail":"model is no longer available"}')
    response_context = MagicMock()
    response_context.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.post.return_value = response_context
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    monkeypatch.setattr(recognition.aiohttp, 'ClientSession', lambda: session_context)
    with pytest.raises(BotError, match='old/vision-model знято з NVIDIA.*NVIDIA_MODEL'):
        await recognition.recognize_replacements_from_image(b'offline-test')


@pytest.mark.asyncio
async def test_nvidia_disables_thinking_and_explains_empty_content(monkeypatch):
    from queue_bot.services.errors import BotError
    monkeypatch.setattr(config, 'RECOGNITION_API_KEY', 'nvapi-test')
    monkeypatch.setattr(config, 'RECOGNITION_PROVIDER', 'nvidia')
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={'choices': [{'finish_reason': 'length', 'message': {
        'content': None, 'reasoning_content': 'Let me look at the table...'}}]})
    response_context = MagicMock()
    response_context.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.post.return_value = response_context
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    monkeypatch.setattr(recognition.aiohttp, 'ClientSession', lambda: session_context)
    with pytest.raises(BotError, match='NVIDIA не повернув таблицю: модель витратила весь ліміт'):
        await recognition.recognize_replacements_from_image(b'offline-test')
    assert session.post.call_args.kwargs['json']['chat_template_kwargs'] == {'enable_thinking': False}


@pytest.mark.parametrize('key, provider, tokens, timeout', [
    ('nvapi-test', 'auto', 4096, 120),
    ('any-key', 'nvidia', 4096, 120),
    ('gsk_test', 'auto', 900, 45),
])
def test_token_and_timeout_defaults_depend_on_provider(monkeypatch, key, provider, tokens, timeout):
    import importlib
    for name in ('RECOGNITION_MAX_TOKENS', 'RECOGNITION_TIMEOUT_SECONDS', 'OPENAI_API_KEY'):
        monkeypatch.setenv(name, '')
        monkeypatch.delenv(name)
    monkeypatch.setenv('RECOGNITION_API_KEY', key)
    monkeypatch.setenv('RECOGNITION_PROVIDER', provider)
    try:
        importlib.reload(config)
        assert (config.RECOGNITION_MAX_TOKENS, config.RECOGNITION_TIMEOUT_SECONDS) == (tokens, timeout)
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_group_list_with_several_prefixes_and_a_wrapped_cell():
    rows = [
        {'group': 'КІ-231,233,', 'lessons': '9-10', 'subject': 'Осн.нац.спрот.', 'teacher': 'Матіяш', 'room': '111'},
        {'group': 'ПІ-231,232', 'lessons': '', 'subject': '(Законодавство)', 'teacher': '', 'room': ''},
        {'group': 'ПІ-232', 'lessons': '1-2', 'subject': 'По розкладу', 'teacher': 'Барабаш', 'room': '234'},
    ]
    assert recognition.expand_groups('КІ-231,233, ПІ-231,232') == {'кi231', 'кi233', 'pi231', 'pi232'}
    picked = recognition.rows_to_replacements(rows, 'ПІ-231')
    assert [(p['lessonNumber'], p['newSubject']) for p in picked] == [(5, 'Осн.нац.спрот. (Законодавство)')]
    assert [p['lessonNumber'] for p in recognition.rows_to_replacements(rows, 'ПІ-232')] == [5, 1]
    assert recognition.rows_to_replacements(rows, 'КІ-232') == []


def test_enlarge_upscales_small_screenshots_only():
    from io import BytesIO
    from PIL import Image
    def png(size):
        out = BytesIO(); Image.new('RGB', size, 'white').save(out, 'PNG'); return out.getvalue()
    assert Image.open(BytesIO(recognition.enlarge(png((581, 820))))).size == (1700, 2400)
    big = png((1800, 2400))
    assert recognition.enlarge(big) is big
    assert recognition.enlarge(b'not an image') == b'not an image'


@pytest.mark.asyncio
async def test_nvidia_copies_whole_table_and_code_picks_group_and_date(monkeypatch):
    import json
    monkeypatch.setattr(config, 'RECOGNITION_API_KEY', 'nvapi-test')
    monkeypatch.setattr(config, 'RECOGNITION_PROVIDER', 'nvidia')
    monkeypatch.setattr(config, 'RECOGNITION_MAX_TOKENS', 900)  # an old explicit limit must not shrink the reply
    answer = {'dateText': 'Заміни в розкладі 17 листопада 2026 р. Вівторок – чисельник', 'date': '2026-10-17',
              'confidence': 0.95, 'scheduleOverrides': [], 'unparsedNotes': [], 'notes': [], 'tableReadable': True,
              'rows': [['КІ-231', '1-2', '', 'Немає пари', '', ''],
                       ['ПІ-231', '3-4', '', 'Машинне\nнавчання', 'Барабаш', '234'],
                       ['', '5-6', 'Барабаш', 'Архітектура', 'Ковальчук', '232']]}
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={'choices': [{'message': {'content': json.dumps(answer, ensure_ascii=False)}}]})
    response_context = MagicMock()
    response_context.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.post.return_value = response_context
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    monkeypatch.setattr(recognition.aiohttp, 'ClientSession', lambda: session_context)
    result = await recognition.recognize_replacements_from_images([b'offline-test'], 'ПІ-231', None, allow_empty=True)
    assert result['date'] == '2026-11-17'
    assert [(r['lesson_number'], r['new_subject']) for r in result['replacements']] == [(2, 'Машинне навчання'), (3, 'Архітектура')]
    sent = session.post.call_args.kwargs['json']
    assert sent['max_tokens'] == 4096
    assert 'ВСЮ таблицю' in sent['messages'][0]['content'][0]['text']


@pytest.mark.asyncio
async def test_nvidia_whole_table_without_group_rows_reports_no_replacements(monkeypatch):
    import json
    monkeypatch.setattr(config, 'RECOGNITION_API_KEY', 'nvapi-test')
    monkeypatch.setattr(config, 'RECOGNITION_PROVIDER', 'nvidia')
    answer = {'date': '2026-11-17', 'confidence': 0.95, 'scheduleOverrides': [], 'unparsedNotes': [], 'notes': [],
              'tableReadable': True, 'rows': [['КІ-231', '1-2', '', 'Немає пари', '', '']]}
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={'choices': [{'message': {'content': json.dumps(answer, ensure_ascii=False)}}]})
    response_context = MagicMock()
    response_context.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.post.return_value = response_context
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    monkeypatch.setattr(recognition.aiohttp, 'ClientSession', lambda: session_context)
    result = await recognition.recognize_replacements_from_images([b'offline-test'], 'ПІ-231', None, allow_empty=True)
    assert result['replacements'] == []
