from datetime import datetime

from aiogram.types import CallbackQuery, Chat, Message, Update, User
import pytest

from queue_bot import config
from queue_bot.handlers.common import get_admin_reply_menu, get_reply_menu
from queue_bot.services import journal, users
from queue_bot.services.lesson_scheduler import subject_name
from tests.test_navigation_bans import app, send


def test_journal_reply_buttons_are_plain_text(conn):
    for menu in (get_reply_menu(conn, 999), get_reply_menu(conn, 100), get_admin_reply_menu()):
        buttons = [button for row in menu.keyboard for button in row if button.text == 'Електронний журнал']
        assert len(buttons) == 1
        assert buttons[0].web_app is None


async def choose(app, label, uid=1):
    bot = app[1]
    markup = next(
        call.args[0].reply_markup for call in reversed(bot.call_args_list)
        if call.args and getattr(getattr(call.args[0], 'reply_markup', None), 'inline_keyboard', None)
    )
    button = next(b for row in markup.inline_keyboard for b in row if b.text == label)
    message = Message(message_id=10, date=datetime.now(), chat=Chat(id=uid, type='private'),
                      from_user=User(id=5, is_bot=True, first_name='Бот'), text='Журнал')
    callback = CallbackQuery(id='journal', from_user=User(id=uid, is_bot=False, first_name='Тест'),
                             chat_instance='private', message=message, data=button.callback_data)
    await app[0].feed_update(bot, Update(update_id=2, callback_query=callback))


@pytest.mark.asyncio
async def test_journal_edit_works_without_mini_app_url_and_uses_shared_records(app, conn, monkeypatch):
    monkeypatch.setattr(config, 'MINI_APP_URL', '')
    await send(app, 'Електронний журнал', uid=1)
    assert 'Мої оцінки' in app[1].call_args.args[0].text
    assert 'Журнал групи' not in str(app[1].call_args.args[0].reply_markup)

    await choose(app, 'Навчальний період')
    await choose(app, journal.current_period().replace(':', ' · семестр '))
    await choose(app, 'Предмет')
    await choose(app, 'Роботи · атестація 1')
    await choose(app, 'Оцінка · —')
    await send(app, '4,5', uid=1)
    data = journal.view(conn, 1, app[0]['subjects'])
    record, = data['records']
    assert (record['grade'], record['user_id'], record['number'], record['part']) == (4.5, data['viewer_id'], 1, 1)

    monkeypatch.setattr(config, 'MINI_APP_URL', 'https://bot.example/app/')
    await send(app, 'Електронний журнал', uid=1)
    buttons = [b for row in app[1].call_args.args[0].reply_markup.inline_keyboard for b in row]
    assert next(b for b in buttons if b.web_app).web_app.url == 'https://bot.example/app/?page=journal'


@pytest.mark.asyncio
async def test_admin_edits_group_grade_with_revision_and_preserves_existing_note(app, conn):
    student = users.get_user(conn, 1)
    journal.submit(conn, 1, app[0]['subjects'], {
        'subject_key': subject_name('Предмет'), 'kind': 'lab', 'number': 1, 'part': 1, 'grade': 2, 'note': 'Нотатка',
    })
    await send(app, 'Електронний журнал', uid=100)
    await choose(app, 'Журнал групи', uid=100)
    await choose(app, 'Петренко Іван', uid=100)
    await choose(app, 'Предмет', uid=100)
    await choose(app, 'Роботи · атестація 1', uid=100)
    await choose(app, 'Оцінка · 2', uid=100)
    await choose(app, '5', uid=100)
    record, = journal.view(conn, 1, app[0]['subjects'])['records']
    assert (record['user_id'], record['grade'], record['note'], record['revision']) == (student['id'], 5, 'Нотатка', 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("admin_mode", [False, True])
async def test_journal_image_is_sent_with_only_the_selected_students_grades(app, conn, monkeypatch, admin_mode):
    from io import BytesIO

    from PIL import Image

    from queue_bot.services import journal_image

    uid = 100 if admin_mode else 1
    for student, grade in ((1, 4.25), (100, 5)):
        journal.submit(conn, student, app[0]["subjects"], {
            "subject_key": subject_name("Предмет"), "kind": "lab", "number": 1, "part": 1, "grade": grade,
        })
    monkeypatch.setattr(config, "JOURNAL_IMAGE_API_KEY", "")
    original = journal_image.render_journal
    exported = []

    def render(subject, student, period, records, averages, artwork):
        exported.append((student["name"], [r["grade"] for r in records]))
        return original(subject, student, period, records, averages, artwork)

    monkeypatch.setattr(journal_image, "render_journal", render)
    await send(app, "Електронний журнал", uid=uid)
    if admin_mode:
        await choose(app, "Журнал групи", uid=uid)
        await choose(app, "Петренко Іван", uid=uid)
    await choose(app, "Предмет", uid=uid)
    await choose(app, "🖼 Зображення оцінок", uid=uid)
    assert exported == [("Петренко Іван", [4.25])]
    photo = app[1].call_args.args[0].photo
    with Image.open(BytesIO(photo.data)) as png:
        assert png.format == "PNG"
