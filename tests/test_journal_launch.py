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
    await choose(app, 'Оцінка · —')
    await send(app, '4,5', uid=1)
    data = journal.view(conn, 1, app[0]['subjects'])
    record, = data['records']
    assert (record['grade'], record['user_id'], record['number'], record['part']) == (4.5, data['viewer_id'], 1, 1)
    await choose(app, 'Атестація 2')
    assert '✓ Атестація 2' in str(app[1].call_args.args[0].reply_markup)
    await choose(app, 'Атестація 1')
    assert 'Оцінка · 4.5' in str(app[1].call_args.args[0].reply_markup)

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
    original = journal_image.render_journal
    exported = []

    def render(subject, student, period, records, averages, coursework):
        exported.append((student["name"], [r["grade"] for r in records]))
        return original(subject, student, period, records, averages, coursework)

    monkeypatch.setattr(journal_image, "render_journal", render)
    await send(app, "Електронний журнал", uid=uid)
    if admin_mode:
        await choose(app, "Журнал групи", uid=uid)
        await choose(app, "Петренко Іван", uid=uid)
    await choose(app, "Предмет", uid=uid)
    assert exported == [("Петренко Іван", [4.25])]
    photo, menu = [call.args[0] for call in app[1].call_args_list[-2:]]
    assert "Оцінка ·" in str(menu.reply_markup)
    with Image.open(BytesIO(photo.photo.data)) as png:
        assert png.format == "PNG"
        assert png.getpixel((0, 0)) == (255, 255, 255)
    await choose(app, "🖼 Зображення оцінок", uid=uid)
    assert exported == [("Петренко Іван", [4.25])] * 2


@pytest.mark.asyncio
async def test_private_message_assigns_architecture_digits_to_own_grade_cells(app, conn):
    key = subject_name("Архітектура та проєкт ПЗ")
    period = journal.current_period()
    own = users.get_user(conn, 1)["id"]
    other = users.get_user(conn, 100)["id"]
    for uid, number, part, grade, note in ((1, 1, 1, 1, "Зберегти примітку"), (1, 2, 3, 3, ""), (100, 1, 1, 2, "")):
        journal.submit(conn, uid, app[0]["subjects"], {
            "period": period, "subject_key": key, "kind": "lab", "number": number, "part": part,
            "grade": grade, "note": note,
        })

    await send(app, "Архітектура 4545540", uid=1)
    assert "Перші 7 клітинок" in app[1].call_args.args[0].text
    rows = journal.view(conn, 100, app[0]["subjects"], group=True, period=period)["records"]
    own_rows = sorted((row for row in rows if row["user_id"] == own), key=lambda row: (row["number"], row["part"]))
    assert [(row["number"], row["part"], row["grade"]) for row in own_rows] == [
        (1, 1, 4), (1, 2, 5), (1, 3, 4), (1, 4, 5), (1, 5, 5), (2, 1, 4), (2, 2, 0), (2, 3, 3),
    ]
    assert own_rows[0]["note"] == "Зберегти примітку" and own_rows[0]["revision"] == 1
    assert next(row["grade"] for row in rows if row["user_id"] == other) == 2
    audit = conn.execute("SELECT COUNT(*) FROM journal_cell_events").fetchone()[0]
    await send(app, "Архітектура 4545540", uid=1)
    assert conn.execute("SELECT COUNT(*) FROM journal_cell_events").fetchone()[0] == audit


@pytest.mark.asyncio
async def test_physical_education_shortcut_and_invalid_grade_series(app, conn):
    await send(app, "Фізкультура 5674", uid=1)
    assert "цифр від 0 до 5" in app[1].send_message.await_args.args[1]
    assert conn.execute("SELECT COUNT(*) FROM journal_cells").fetchone()[0] == 0
    await send(app, "Фізра 54545", uid=1)
    assert "4 клітинок" in app[1].send_message.await_args.args[1]
    assert conn.execute("SELECT COUNT(*) FROM journal_cells").fetchone()[0] == 0
    await send(app, "ФВ 4505", uid=100)
    assert "Фізичне виховання" in app[1].call_args.args[0].text
    grades = conn.execute("SELECT user_id, grade FROM journal_cells ORDER BY number").fetchall()
    assert [row["grade"] for row in grades] == [4, 5, 0, 5]
    assert {row["user_id"] for row in grades} == {users.get_user(conn, 100)["id"]}


@pytest.mark.asyncio
async def test_coursework_shows_one_grade_menu_after_its_photo(app, conn):
    title = "Курсова робота з КПЗ"
    app[0]["subjects"].append({"id": 7, "name": title, "teachers": []})
    await send(app, "Електронний журнал", uid=1)
    await choose(app, title)
    photo, menu = [call.args[0] for call in app[1].call_args_list[-2:]]
    assert photo.photo.data.startswith(b"\x89PNG")
    buttons = [button.text for row in menu.reply_markup.inline_keyboard for button in row]
    assert "Додати оцінку" in buttons
    assert not any("атестація" in label.lower() for label in buttons)
    await choose(app, "Додати оцінку")
    await choose(app, "5")
    record, = journal.view(conn, 1, app[0]["subjects"])["records"]
    assert (record["subject_key"], record["grade"], record["number"], record["part"]) == (
        subject_name(title), 5, 1, 1,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_photos", [1, 2])
async def test_journal_retries_photo_and_keeps_grade_menu_available(app, conn, monkeypatch, failed_photos):
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import SendMessage, SendPhoto

    from queue_bot.handlers import journal as handler

    monkeypatch.setattr(handler, "_SEND_RETRY_DELAY", 0)
    await send(app, "Електронний журнал", uid=1)
    attempts = []
    delivered = []

    async def transport(method, **kwargs):
        if isinstance(method, SendPhoto):
            attempts.append(method.photo.data)
            if len(attempts) <= failed_photos:
                raise TelegramNetworkError(method=method, message="Server disconnected")
        if isinstance(method, (SendMessage, SendPhoto)):
            delivered.append(method)
        return True

    app[1].side_effect = transport
    await choose(app, "Предмет")
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    if failed_photos == 1:
        assert isinstance(delivered[0], SendPhoto)
    menu = delivered[-1]
    assert isinstance(menu, SendMessage)
    assert ("Telegram тимчасово" in menu.text) == (failed_photos == 2)
    assert "Зображення оцінок" in str(menu.reply_markup)
    await choose(app, "Оцінка · —")
    await choose(app, "4")
    row, = journal.view(conn, 1, app[0]["subjects"])["records"]
    assert row["grade"] == 4


@pytest.mark.asyncio
async def test_journal_keeps_original_subject_buttons_when_menu_delivery_fails(app, monkeypatch):
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import SendMessage

    from queue_bot.handlers import journal as handler
    from tests.test_navigation_bans import data

    monkeypatch.setattr(handler, "_SEND_RETRY_DELAY", 0)
    await send(app, "Електронний журнал", uid=1)
    before = (await data(app, uid=1))["journal"]
    failures = []

    async def transport(method, **kwargs):
        if isinstance(method, SendMessage) and method.reply_markup:
            failures.append(method)
            raise TelegramNetworkError(method=method, message="Server disconnected")
        return True

    app[1].side_effect = transport
    await choose(app, "Предмет")
    assert len(failures) == 2
    assert (await data(app, uid=1))["journal"] == before


@pytest.mark.asyncio
async def test_journal_grade_deletion_requires_telegram_confirmation(app, conn):
    subjects = app[0]['subjects']
    journal.submit(conn, 1, subjects, {
        'subject_key': subject_name('Предмет'), 'kind': 'lab', 'number': 1, 'grade': 4,
    })
    await send(app, 'Електронний журнал', uid=1)
    await choose(app, 'Предмет')
    await choose(app, 'Оцінка · 4')
    await choose(app, 'Видалити оцінку')
    assert journal.view(conn, 1, subjects)['records'][0]['grade'] == 4
    await choose(app, 'Скасувати')
    assert journal.view(conn, 1, subjects)['records'][0]['grade'] == 4
    await choose(app, 'Видалити оцінку')
    await choose(app, 'Так, видалити')
    assert not journal.view(conn, 1, subjects)['records']
    assert 'Оцінку видалено' in app[1].call_args.args[0].text
    await choose(app, 'Оцінка · —')
    await choose(app, '0')
    assert journal.view(conn, 1, subjects)['records'][0]['grade'] == 0
