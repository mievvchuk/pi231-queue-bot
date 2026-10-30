"""Validate signed Telegram initData; never trust initDataUnsafe."""
import json
import time
from urllib.parse import parse_qsl

from aiogram.utils.web_app import check_webapp_signature


class AuthenticationError(ValueError):
    pass


def authenticate(raw, token, *, now=None, max_age=3600):
    try:
        if not isinstance(raw, str) or not raw or len(raw) > 8192:
            raise ValueError
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=30)
        data = dict(pairs)
        if len(pairs) != len(data) or not check_webapp_signature(token, raw):
            raise ValueError
        stamp = int(data['auth_date'])
        age = (time.time() if now is None else now) - stamp
        if not -30 <= age <= max_age:
            raise ValueError
        user = json.loads(data['user'])
        uid = user['id']
        if type(uid) is not int or not 0 < uid < 2**52 or user.get('is_bot'):
            raise ValueError
        return uid
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AuthenticationError('Відкрийте мініапку з меню бота в Telegram. Якщо вона вже відкрита — закрийте й відкрийте знову.') from None
