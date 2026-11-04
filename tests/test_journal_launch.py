import pytest

from queue_bot import config
from queue_bot.handlers.common import get_admin_reply_menu, get_reply_menu
from tests.test_navigation_bans import app, send


def test_journal_reply_buttons_are_plain_text_so_launch_goes_through_inline_button(conn, monkeypatch):
    # A reply-keyboard web_app opens the Mini App without initData, which forces a login screen.
    monkeypatch.setattr(config,'MINI_APP_URL','https://bot.example/app/')
    for menu in (get_reply_menu(conn,999),get_reply_menu(conn,100),get_admin_reply_menu()):
        buttons = [button for row in menu.keyboard for button in row if button.text == 'Електронний журнал']
        assert len(buttons) == 1
        assert buttons[0].web_app is None


@pytest.mark.asyncio
async def test_typed_journal_button_has_launch_link_and_missing_configuration_explains_reason(app, monkeypatch):
    monkeypatch.setattr(config,'MINI_APP_URL','https://bot.example/app/')
    await send(app,'Електронний журнал',uid=1)
    markup = app[1].call_args.args[0].reply_markup
    assert markup.inline_keyboard[0][0].web_app.url == 'https://bot.example/app/?page=journal'
    monkeypatch.setattr(config,'MINI_APP_URL','')
    await send(app,'Електронний журнал',uid=1)
    assert 'налаштувати адресу Mini App' in app[1].send_message.await_args.args[1]
