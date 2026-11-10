import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from queue_bot import config
from queue_bot.services import recognition, replacements, schedule
from queue_bot.services.errors import BotError


@pytest.fixture
def vision(monkeypatch, tmp_path):
    source = {
        'thursday': [
            {'pair': 1, 'subject': 'Знаменник', 'teacher': 'Викладач Чт', 'room': '201', 'week_type': 'denominator'},
            {'pair': 1, 'subject': 'Чисельник', 'week_type': 'numerator'},
        ],
        'friday': [
            {'pair': 1, 'subject': 'Звичайна перша'},
            {'pair': 4, 'subject': 'Звичайна четверта', 'startTime': '14:00', 'endTime': '15:20'},
        ],
    }
    path = tmp_path / 'schedule.json'
    path.write_text(json.dumps(source), encoding='utf-8')
    monkeypatch.setattr(config, 'SCHEDULE_PATH', str(path))
    monkeypatch.setattr(config, 'RECOGNITION_API_KEY', 'offline-test')
    monkeypatch.setattr(config, 'RECOGNITION_PROVIDER', 'groq')
    response = MagicMock(status=200)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    client = MagicMock()
    client.post.return_value = context
    client_context = MagicMock()
    client_context.__aenter__ = AsyncMock(return_value=client)
    monkeypatch.setattr(recognition.aiohttp, 'ClientSession', lambda: client_context)
    data = {'date': '2026-10-30', 'confidence': 0.99, 'replacements': [], 'scheduleOverrides': [{
        'targetPair': 4, 'sourceDay': 'thursday', 'sourcePair': 1, 'weekType': 'denominator',
        'text': '4-ті пари проводяться за розкладом четверга / перша пара / знаменник',
    }]}

    async def recognize():
        response.json = AsyncMock(return_value={'choices': [{'message': {'content': json.dumps(data)}}]})
        return await recognition.recognize_replacements_from_image(b'offline')
    return data, recognize, source, client


@pytest.mark.asyncio
async def test_header_without_group_rows_changes_only_fourth_pair_after_save(conn, vision):
    data, recognize, source, client = vision
    result = await recognize()
    item, = result['replacements']
    assert (item['lesson_number'], item['new_subject'], item['teacher'], item['room']) == (4, 'Знаменник', 'Викладач Чт', '201')
    assert item['old_subject'] == 'Звичайна четверта'
    assert 'четверга' in item['source_note']
    assert 'четверга' in replacements.format_replacement(item)
    replacements.save_replacement(conn, item)
    actual, _ = schedule.get_actual_schedule(conn, source, date(2026, 10, 30))
    assert [r['subject'] for r in actual] == ['Звичайна перша', 'Знаменник']
    # Header remapping keeps the target pair and uses the group's fixed bell times.
    assert actual[1]['startTime'] == '13:20'
    assert actual[1]['endTime'] == '14:40'
    prompt = client.post.call_args.kwargs['json']['messages'][0]['content'][0]['text']
    assert 'scheduleOverrides' in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize('subject,expected', [('Конкретна заміна', 'Конкретна заміна'), ('По розкладу', 'Знаменник')])
async def test_specific_row_precedence_and_as_scheduled(vision, subject, expected):
    data, recognize, _, _ = vision
    data['replacements'] = [{'group': 'ПІ-231', 'lessonNumber': 4, 'newSubject': subject}]
    result = await recognize()
    assert len(result['replacements']) == 1
    assert result['replacements'][0]['new_subject'] == expected


@pytest.mark.asyncio
async def test_other_groups_do_not_override_header(vision):
    data, recognize, _, _ = vision
    data['replacements'] = [{'group': 'КІ-231', 'lessonNumber': 4, 'newSubject': 'Чужа заміна'}]
    assert (await recognize())['replacements'][0]['new_subject'] == 'Знаменник'


@pytest.mark.asyncio
async def test_as_scheduled_row_keeps_explicit_room_and_teacher(vision):
    data, recognize, _, _ = vision
    data['replacements'] = [{'group': 'ПІ-231', 'lessonNumber': 4, 'newSubject': 'По розкладу', 'teacher': 'Новий Викладач', 'room': '305'}]
    item, = (await recognize())['replacements']
    assert item['new_subject'] == 'Знаменник'
    assert (item['teacher'], item['room']) == ('Новий Викладач', '305')


@pytest.mark.asyncio
async def test_unknown_source_room_does_not_inherit_original_room(conn, vision, monkeypatch):
    data, recognize, source, _ = vision
    source['thursday'][0].pop('room')
    source['friday'][1]['room'] = 'Стара аудиторія'
    monkeypatch.setattr(schedule, 'load_schedule_data', lambda: source)
    item, = (await recognize())['replacements']
    replacements.save_replacement(conn, item)
    actual, _ = schedule.get_actual_schedule(conn, source, date(2026, 10, 30))
    assert actual[1]['room'] != 'Стара аудиторія'


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('sourceDay', 'unknown'), ('sourcePair', 5), ('weekType', None), ('targetPair', 0)])
async def test_unresolvable_header_is_not_silently_ignored(vision, field, value):
    data, recognize, _, _ = vision
    data['scheduleOverrides'][0][field] = value
    with pytest.raises(BotError):
        await recognize()


@pytest.mark.asyncio
async def test_unparsed_header_requires_manual_correction(vision):
    data, recognize, _, _ = vision
    data['unparsedNotes'] = ['Нерозбірлива загальна зміна розкладу']
    with pytest.raises(BotError, match='приміт'):
        await recognize()


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('replacements', None), ('replacements', ['bad row']), ('confidence', None), ('confidence', 'NaN'), ('date', '2026-02-30')])
async def test_malformed_model_fields_receive_helpful_error(vision, field, value):
    data, recognize, _, _ = vision
    data[field] = value
    with pytest.raises(BotError):
        await recognize()


@pytest.mark.parametrize('text,expected', [
    ('ЧЕТВЕРТІ ПАРИ ПРОВОДЯТЬСЯ ЗА РОЗКЛАДОМ 3-Ї пари ПОНЕДІЛКА (знаменник)', (4, 'monday', 3, 'denominator')),
    ('4-ті пари проводяться за розкладом четверга / перша пара / знаменник', (4, 'thursday', 1, 'denominator')),
    ("Другі пари проводяться за розкладом п’ятниці 5 пари", (2, 'friday', 5, 'numerator')),
])
def test_header_note_parsed_by_code(text, expected):
    o = recognition.parse_header_note(text, 'numerator')
    assert (o['targetPair'], o['sourceDay'], o['sourcePair'], o['weekType']) == expected
    assert recognition.parse_header_note('Черговий викладач: Фрімерштейн Г.О.') is None


def test_hours_and_group_cells():
    assert [recognition.lesson_pairs(h) for h in ('1-2', '3-4', '5-6', '7-8', '9-10', '3-6', '')] == [[1], [2], [3], [4], [5], [2, 3], []]
    assert recognition.expand_groups('КІ-251,252,253') == {recognition.normalize_group(g) for g in ('КІ-251', 'КІ-252', 'КІ-253')}


@pytest.mark.asyncio
async def test_full_table_rows_pick_only_target_group_and_header_slot(vision, monkeypatch):
    data, recognize, source, _ = vision
    source['monday'] = [{'pair': 3, 'subject': 'Понеділок третя', 'teacher': 'Викладач Пн', 'room': '231', 'week_type': 'denominator'}]
    monkeypatch.setattr(schedule, 'load_schedule_data', lambda: source)
    data.update(date='2026-11-11', scheduleOverrides=[], headerWeekType='denominator',
                notes=['ЧЕТВЕРТІ ПАРИ ПРОВОДЯТЬСЯ ЗА РОЗКЛАДОМ 3-Ї пари ПОНЕДІЛКА (знаменник)'])
    data.pop('replacements')
    data['rows'] = [
        {'group': 'КІ-231', 'lessons': '1-2', 'subject': 'Системне програм', 'teacher': 'Задніпровський', 'room': '232'},
        {'group': '', 'lessons': '7-8', 'replaced': 'Шимків', 'subject': 'Немає пари'},
        {'group': 'ПІ-231', 'lessons': '1-2', 'subject': 'ЛМВ', 'teacher': 'Кондратюк', 'room': '231'},
        {'group': 'ПІ-232', 'lessons': '7-8', 'replaced': 'Багас', 'subject': 'Проект.інтерфейс', 'teacher': 'Атаман', 'room': '235'},
    ]
    first, fourth = (await recognize())['replacements']
    assert (first['lesson_number'], first['new_subject'], first['teacher'], first['room']) == (1, 'Людино-машинна взаємодія (ЛМВ)', 'Кондратюк Є.С.', '231')
    assert (fourth['lesson_number'], fourth['new_subject']) == (4, 'Понеділок третя')
