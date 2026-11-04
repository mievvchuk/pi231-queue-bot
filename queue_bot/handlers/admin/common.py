import html
import logging
import re
from datetime import date
from aiogram import BaseMiddleware, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
)
from queue_bot.handlers.common import (
    find_session_by_label,
    format_entry,
    format_queue,
    get_admin_polls_menu,
    get_admin_queues_menu,
    get_admin_reply_menu,
    get_admin_schedule_menu,
    get_admin_settings_menu,
    get_admin_students_menu,
    get_admin_subjects_menu,
    get_admin_users_menu,
    get_paginated_reply_keyboard,
    get_user_card_menu,
    make_keyboard,
    make_reply_keyboard,
    notify_next_students,
    paginate,
    queue_label,
    session_names,
    show,
)
from queue_bot import config
from queue_bot.services import subjects as subjects_module
from queue_bot.services import admins, queues, users
from queue_bot.services.errors import BotError, PermissionDenied
from .states import AdminPanelState, AdminUserState, AdminSubjectsState, AdminOrderState, AdminManageState

log = logging.getLogger(__name__)
MAX_POSITION = 60
HISTORY_MARK = {
    "completed": "✅ здав",
    "transferred": "↪ перенесено",
    "removed": "✖ не здав / видалено",
    "left": "вийшов із черги",
}

