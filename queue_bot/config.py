"""Central environment configuration; no personal IDs or credentials in source."""
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]


def load_env_file(path=None):
    target = Path(path or os.getenv('ENV_FILE', '.env'))
    if not target.is_absolute():
        target = ROOT / target
    if not target.exists():
        return
    for line in target.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip().removeprefix('export ')
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', key):
            continue
        if value.startswith(('"', "'")) and value[0] in value[1:]:
            value = value[1:value.find(value[0], 1)]
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
        os.environ.setdefault(key, value)


def integer(name, default, minimum=0, maximum=2**63 - 1):
    try:
        value = int(os.getenv(name, str(default)))
        if not minimum <= value <= maximum:
            raise ValueError
        return value
    except ValueError:
        raise RuntimeError(f'{name} must be an integer between {minimum} and {maximum}') from None


def data_path(name, filename):
    configured = os.getenv(name, '').strip()
    if configured:
        target = Path(configured)
        if not target.is_absolute():
            target = ROOT / target
        if target == ROOT / filename and not target.exists():
            target = ROOT / 'data' / filename
    else:
        legacy = ROOT / filename
        target = legacy if legacy.exists() else ROOT / 'data' / filename
    return str(target)


load_env_file()
DATABASE_URL = os.getenv('DATABASE_URL', '').strip()
OWNER_ID = integer('OWNER_ID', 0)
GROUP_CHAT_ID = os.getenv('GROUP_CHAT_ID', '').strip()
SUBJECTS_PATH = data_path('SUBJECTS_PATH', 'subjects.json')
SCHEDULE_PATH = data_path('SCHEDULE_PATH', 'schedule.json')
LAB_CATALOG_PATH = data_path('LAB_CATALOG_PATH', 'lab_catalog.json')
COURSEWORK_PRIORITY_PATH = data_path('COURSEWORK_PRIORITY_PATH', 'coursework_priority.json')
STUDENT_GROUP = os.getenv('STUDENT_GROUP', 'ПІ-231')
NOTIFICATION_TIME = os.getenv('NOTIFICATION_TIME', '17:00')
TIMEZONE = os.getenv('TZ', 'Europe/Kyiv')
HTTP_HOST = os.getenv('HTTP_HOST', '0.0.0.0')
PORT = integer('PORT', 8080, 1, 65535)
MINI_APP_URL = os.getenv('MINI_APP_URL', '').strip()
TELEGRAM_LOGIN_CLIENT_ID = os.getenv('TELEGRAM_LOGIN_CLIENT_ID', '').strip()
TELEGRAM_LOGIN_CLIENT_SECRET = os.getenv('TELEGRAM_LOGIN_CLIENT_SECRET', '').strip()
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
POLLING_RETRY_SECONDS = integer('POLLING_RETRY_SECONDS', 5, 1, 3600)
POLLS_WATCH_INTERVAL = integer('POLLS_WATCH_INTERVAL', 15, 1, 3600)
SCHEDULE_CHECK_INTERVAL = integer('SCHEDULE_CHECK_INTERVAL', 60, 1, 3600)
RECOGNITION_API_KEY = os.getenv('RECOGNITION_API_KEY', '').strip() or os.getenv('OPENAI_API_KEY', '').strip()
RECOGNITION_PROVIDER = os.getenv('RECOGNITION_PROVIDER', 'auto' if RECOGNITION_API_KEY else 'disabled').lower()
GROQ_API_URL = os.getenv('GROQ_API_URL', 'https://api.groq.com/openai/v1/chat/completions')
GROQ_MODEL = os.getenv('GROQ_MODEL', 'qwen/qwen3.8-27b')
OPENAI_API_URL = os.getenv('OPENAI_API_URL', 'https://api.openai.com/v1/chat/completions')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
RECOGNITION_TIMEOUT_SECONDS = integer('RECOGNITION_TIMEOUT_SECONDS', 45, 1, 300)


def recognition_settings():
    provider = RECOGNITION_PROVIDER
    if provider == 'auto':
        provider = 'groq' if RECOGNITION_API_KEY.startswith('gsk_') else 'openai'
    if provider == 'groq':
        return GROQ_API_URL, GROQ_MODEL, 'Groq'
    return OPENAI_API_URL, OPENAI_MODEL, 'OpenAI'


def get_bot_token():
    token = os.getenv('BOT_TOKEN', '').strip()
    if not token:
        raise RuntimeError('Set BOT_TOKEN in the hosting environment or .env')
    return token


def get_initial_admin_ids():
    raw = os.getenv('INITIAL_ADMIN_IDS', '').strip() or (str(OWNER_ID) if OWNER_ID else '')
    parts = [part.strip() for part in raw.split(',') if part.strip()]
    if any(not part.isdigit() or int(part) <= 0 for part in parts):
        raise RuntimeError('INITIAL_ADMIN_IDS must contain positive Telegram IDs separated by commas')
    return list(dict.fromkeys(int(part) for part in parts))


def validate_runtime():
    from queue_bot.services.lab_catalog import load_catalog
    load_catalog(LAB_CATALOG_PATH)
    if not DATABASE_URL:
        raise RuntimeError('Set DATABASE_URL in the hosting environment or .env')
    get_bot_token()
    if OWNER_ID <= 0:
        raise RuntimeError('Set OWNER_ID in the hosting environment or .env')
    get_initial_admin_ids()
    if MINI_APP_URL:
        try:
            target = urlsplit(MINI_APP_URL)
            if (target.scheme != 'https' or not target.hostname or target.username or target.password
                    or target.fragment or target.query or target.path.rstrip('/') != '/app'):
                raise ValueError
            target.port
        except ValueError:
            raise RuntimeError('MINI_APP_URL must be an HTTPS URL ending in /app/ without credentials, query or fragment') from None
    if TELEGRAM_LOGIN_CLIENT_ID or TELEGRAM_LOGIN_CLIENT_SECRET:
        if not (TELEGRAM_LOGIN_CLIENT_ID.isdigit() and TELEGRAM_LOGIN_CLIENT_SECRET and MINI_APP_URL):
            raise RuntimeError('Telegram Login requires TELEGRAM_LOGIN_CLIENT_ID, TELEGRAM_LOGIN_CLIENT_SECRET and MINI_APP_URL')
    try:
        ZoneInfo(TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        raise RuntimeError('TZ must be an IANA timezone, for example Europe/Kyiv') from None
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', NOTIFICATION_TIME):
        raise RuntimeError('NOTIFICATION_TIME must use HH:MM')
    if LOG_LEVEL not in {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}:
        raise RuntimeError('LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL')
    if RECOGNITION_PROVIDER not in {'disabled', 'auto', 'groq', 'openai'}:
        raise RuntimeError('RECOGNITION_PROVIDER must be disabled, auto, groq or openai')
    if RECOGNITION_PROVIDER in {'groq', 'openai'} and not RECOGNITION_API_KEY:
        raise RuntimeError('Set RECOGNITION_API_KEY or disable recognition')
    if GROUP_CHAT_ID and not GROUP_CHAT_ID.lstrip('-').isdigit():
        raise RuntimeError('GROUP_CHAT_ID must be a Telegram chat ID')


# Public channel ingestion; no Telegram user session is required.
REPLACEMENTS_CHANNEL = os.getenv("REPLACEMENTS_CHANNEL", "replacements_xpk").strip("@ ")
CHANNEL_POLL_SECONDS = integer("CHANNEL_POLL_SECONDS", 300, 60, 86400)
