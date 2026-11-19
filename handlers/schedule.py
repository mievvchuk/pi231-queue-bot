import asyncio
import datetime
import html
import io
import logging
import re
import uuid
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from handlers.common import (
    get_reply_menu,
    get_schedule_reply_menu,
    make_keyboard,
    make_reply_keyboard,
)
from services import admins, recognition, replacements, schedule, working_days
from services.errors import BotError

log = logging.getLogger(__name__)
router = Router()

# Тимчасове збереження розпізнаних замін для підтвердження
_PENDING_RECOGNITIONS: dict[str, list[dict]] = {}


class EditReplacementState(StatesGroup):
    waiting_for_value = State()


# ---------- Розклад: Сьогодні / Завтра / Тиждень ----------

_private_only = F.chat.type == "private"


@router.message(_private_only, F.text == "📅 Розклад", StateFilter("*"))
async def open_schedule_menu(message: Message, state: FSMContext, conn):
    await state.clear()
    sch_data = schedule.load_schedule_data(conn)
    now = datetime.datetime.now(ZoneInfo(config.TIMEZONE))

    if now.hour >= 15:
        target_dt = schedule.get_tomorrow_date()
        date_heading = f"на завтра ({target_dt.strftime('%d.%m.%Y')})"
    else:
        target_dt = schedule.get_current_date()
        date_heading = f"на сьогодні ({target_dt.strftime('%d.%m.%Y')})"

    schedule_text = schedule.format_schedule_for_date(conn, sch_data, target_dt, config.STUDENT_GROUP)

    await message.answer(
        f"📅 <b>Розклад занять {date_heading}:</b>\n\n"
        f"{schedule_text}\n\n"
        "Оберіть потрібний період або дію в меню нижче 👇",
        reply_markup=get_schedule_reply_menu(),
    )


@router.message(_private_only, F.text == "📅 Сьогодні", StateFilter("*"))
@router.message(Command("today"), StateFilter("*"))
async def show_today_schedule(message: Message, conn):
    sch_data = schedule.load_schedule_data(conn)
    today_dt = schedule.get_current_date()
    text = schedule.format_schedule_for_date(conn, sch_data, today_dt, config.STUDENT_GROUP)
    await message.answer(text)


@router.message(_private_only, F.text == "➡️ Завтра", StateFilter("*"))
@router.message(Command("tomorrow"), StateFilter("*"))
async def show_tomorrow_schedule(message: Message, conn):
    sch_data = schedule.load_schedule_data(conn)
    tomorrow_dt = schedule.get_tomorrow_date()
    text = schedule.format_schedule_for_date(conn, sch_data, tomorrow_dt, config.STUDENT_GROUP)
    await message.answer(text)


@router.message(_private_only, F.text == "🗓 Весь тиждень", StateFilter("*"))
@router.message(Command("week"), StateFilter("*"))
async def show_week_schedule(message: Message, conn):
    sch_data = schedule.load_schedule_data(conn)
    today_dt = schedule.get_current_date()
    days = schedule.format_week_schedule(conn, sch_data, today_dt, config.STUDENT_GROUP)
    for day_text in days:
        await message.answer(day_text)


@router.message(Command("schedule"), StateFilter("*"))
async def show_base_schedule(message: Message, conn):
    sch_data = schedule.load_schedule_data(conn)
    group = html.escape(config.STUDENT_GROUP)
    for week_type, week_name in schedule.WEEK_NAMES_UK.items():
        lines = [f"📌 <b>Базовий розклад групи {group} · {week_name}</b>"]
        for day_key in schedule.DAY_KEYS:
            lessons = schedule.get_base_schedule_for_day(sch_data, day_key, week_type)
            if day_key in ("saturday", "sunday") and not lessons:
                continue
            slots = schedule.format_lesson_slots(lessons, compact=True)
            day_text = f"<b>{schedule.DAY_NAMES_UK[day_key]}</b>\n{slots}"
            if len("\n\n".join([*lines, day_text])) > 4096:
                await message.answer("\n\n".join(lines))
                lines.clear()
            lines.append(day_text)
        await message.answer("\n\n".join(lines))


# ---------- Заміни ----------

@router.message(_private_only, F.text.in_({"🔄 Заміни", "🔄 Активні заміни"}), StateFilter("*"))
@router.message(Command("replacements"), StateFilter("*"))
async def show_replacements(message: Message, conn):
    items = replacements.get_replacements(conn, group_name=config.STUDENT_GROUP)
    if not items:
        is_priv = getattr(message.chat, "type", "private") == "private"
        admin_hint = (
            "\n<i>(Щоб додати заміну, надішліть фотографію таблиці замін у чат)</i>"
            if is_priv and admins.is_admin(conn, message.from_user.id)
            else ""
        )
        await message.answer(
            f"🔄 <b>Активні заміни для групи {html.escape(config.STUDENT_GROUP)}:</b>\n\n"
            f"Замін ще немає.{admin_hint}"
        )
        return

    text_parts = [f"🔄 <b>Активні заміни для групи {html.escape(config.STUDENT_GROUP)}:</b>\n"]
    for it in items:
        text_parts.append(replacements.format_replacement(it))
        text_parts.append("────────────────")

    await message.answer("\n\n".join(text_parts))


@router.message(_private_only, Command("clear_replacements"), StateFilter("*"))
async def request_clear_replacements(message: Message, conn):
    if not admins.is_admin(conn, message.from_user.id):
        raise BotError("Очищати заміни можуть лише адміністратори.")

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Так, очистити всі", callback_data="clear_rep:yes"),
                InlineKeyboardButton(text="❌ Скасувати", callback_data="clear_rep:no"),
            ]
        ]
    )
    await message.answer("Видалити всі збережені заміни для групи?", reply_markup=markup)


@router.callback_query(F.data == "clear_rep:no")
async def cancel_clear_replacements(callback: CallbackQuery):
    await callback.answer("Скасовано.")
    await callback.message.edit_text("❌ Очищення замін скасовано.")


@router.callback_query(F.data == "clear_rep:yes")
async def confirm_clear_replacements(callback: CallbackQuery, conn):
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("У вас немає прав адміністратора.", show_alert=True)
        return

    count = replacements.clear_replacements(conn, group_name=config.STUDENT_GROUP)
    admins.log_action(conn, callback.from_user.id, "clear_replacements", f"deleted={count}")
    await callback.answer("Заміни очищено!")
    await callback.message.edit_text(f"✅ Успішно видалено замін: <b>{count}</b>.")


FIELD_NAMES_UK = {
    "lesson_number": "Номер пари",
    "new_subject": "Новий предмет",
    "teacher": "Викладач",
    "room": "Аудиторія",
    "date": "Дата",
    "old_subject": "Що знімається (було)",
}


def normalize_date_input(val: str) -> str:
    val = val.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", val):
        return val
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", val)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    m = re.match(r"^(\d{1,2})\.(\d{1,2})$", val)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = datetime.datetime.now(ZoneInfo(config.TIMEZONE)).year
        return f"{year:04d}-{month:02d}-{day:02d}"
    return val


def render_main_preview(token: str, reps: list[dict], conf_pct: int | None = None) -> tuple[str, InlineKeyboardMarkup]:
    if not reps:
        text = "⚠️ Немає розпізнаних замін для збереження."
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Додати заміну вручну", callback_data=f"rec_add:{token}")],
                [InlineKeyboardButton(text="❌ Закрити", callback_data=f"rec_rj:{token}")],
            ]
        )
        return text, markup

    cards = [replacements.format_replacement(r) for r in reps]
    cards_text = "\n\n────────────────\n\n".join(cards)
    conf_str = f"Точність: ~{conf_pct}%\n\n" if conf_pct is not None else ""

    text = (
        f"🔍 <b>Розпізнані заміни для групи {html.escape(config.STUDENT_GROUP)}:</b>\n"
        f"{conf_str}"
        f"{cards_text}\n\n"
        "Зберегти ці заміни в розклад?"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Підтвердити заміни", callback_data=f"rec_cf:{token}"),
                InlineKeyboardButton(text="❌ Відхилити", callback_data=f"rec_rj:{token}"),
            ],
            [
                InlineKeyboardButton(text="✏️ Внести корективи", callback_data=f"rec_ed:{token}"),
            ],
        ]
    )
    return text, markup


def render_corrections_menu(token: str, reps: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    if not reps:
        text = "⚠️ Список замін порожній. Ви можете додати заміну вручну:"
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Додати нову заміну", callback_data=f"rec_add:{token}")],
                [InlineKeyboardButton(text="🔙 Повернутися", callback_data=f"rec_done:{token}")],
            ]
        )
        return text, markup

    text = (
        f"✏️ <b>Внесення коректив до замін ({len(reps)} шт.):</b>\n\n"
        "Оберіть заміну зі списку для коригування або додайте нову 👇"
    )
    kb = []
    for idx, r in enumerate(reps):
        pair = r.get("lesson_number", "?")
        subj = r.get("new_subject", "Без назви")
        btn_text = f"Пара {pair}: {subj}"
        if len(btn_text) > 36:
            btn_text = btn_text[:33] + "..."
        kb.append([InlineKeyboardButton(text=f"✏️ {btn_text}", callback_data=f"rec_it:{token}:{idx}")])

    kb.append([InlineKeyboardButton(text="➕ Додати заміну вручну", callback_data=f"rec_add:{token}")])
    kb.append([InlineKeyboardButton(text="🔙 Готово (до підтвердження)", callback_data=f"rec_done:{token}")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


def render_item_edit_menu(token: str, idx: int, r: dict) -> tuple[str, InlineKeyboardMarkup]:
    card = replacements.format_replacement(r)
    text = (
        f"✏️ <b>Редагування заміни #{idx + 1}</b>\n\n"
        f"{card}\n\n"
        "Оберіть поле, яке бажаєте змінити 👇"
    )
    kb = [
        [
            InlineKeyboardButton(text="⏰ Номер пари", callback_data=f"rec_fld:{token}:{idx}:lesson_number"),
            InlineKeyboardButton(text="📚 Новий предмет", callback_data=f"rec_fld:{token}:{idx}:new_subject"),
        ],
        [
            InlineKeyboardButton(text="👨‍🏫 Викладач", callback_data=f"rec_fld:{token}:{idx}:teacher"),
            InlineKeyboardButton(text="🚪 Аудиторія", callback_data=f"rec_fld:{token}:{idx}:room"),
        ],
        [
            InlineKeyboardButton(text="📅 Дата", callback_data=f"rec_fld:{token}:{idx}:date"),
            InlineKeyboardButton(text="❌ Було (що знято)", callback_data=f"rec_fld:{token}:{idx}:old_subject"),
        ],
        [
            InlineKeyboardButton(text="🗑 Видалити цю заміну", callback_data=f"rec_del:{token}:{idx}"),
            InlineKeyboardButton(text="➕ Додати ще одну", callback_data=f"rec_add:{token}"),
        ],
        [
            InlineKeyboardButton(text="🔙 До списку замін", callback_data=f"rec_ed:{token}"),
            InlineKeyboardButton(text="✅ Готово (до збереження)", callback_data=f"rec_done:{token}"),
        ],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


# ---------- Обробка фотографій таблиць замін ----------

@router.message(_private_only, F.photo)
async def handle_photo_replacement(message: Message, conn):
    if not admins.is_admin(conn, message.from_user.id):
        return

    photo = message.photo[-1]
    bot = message.bot
    status_msg = await message.answer("⏳ Отримано фото. Аналізую таблицю замін...")

    try:
        file = await bot.get_file(photo.file_id)
        img_stream = io.BytesIO()
        await bot.download_file(file.file_path, destination=img_stream)
        img_bytes = img_stream.getvalue()

        result = await recognition.recognize_replacements_from_image(img_bytes, group_name=config.STUDENT_GROUP, conn=conn)
        reps = result["replacements"]
        token = str(uuid.uuid4())[:8]
        _PENDING_RECOGNITIONS[token] = reps

        conf_pct = int(result["confidence"] * 100)
        preview_text, markup = render_main_preview(token, reps, conf_pct=conf_pct)
        await status_msg.edit_text(preview_text, reply_markup=markup)

    except BotError as err:
        await status_msg.edit_text(f"⚠️ {html.escape(str(err))}")
    except Exception as e:
        log.exception(f"Error handling replacement photo: {e}")
        await status_msg.edit_text(f"⚠️ <b>Помилка розпізнавання:</b> {html.escape(str(e))}")


@router.callback_query(F.data.startswith("rec_rj:"))
async def reject_recognition(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    token = callback.data.split(":", 1)[1]
    _PENDING_RECOGNITIONS.pop(token, None)
    await callback.answer("Відхилено.")
    await callback.message.edit_text("❌ Заміни не збережено.")


@router.callback_query(F.data.startswith("rec_cf:"))
async def confirm_recognition(callback: CallbackQuery, state: FSMContext, conn):
    await state.clear()
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("У вас немає прав адміністратора.", show_alert=True)
        return

    token = callback.data.split(":", 1)[1]
    reps = _PENDING_RECOGNITIONS.pop(token, None)
    if not reps:
        await callback.answer("Дані вже збережені або застаріли.", show_alert=True)
        return

    target_date = reps[0].get("date") if reps else None
    archived_count = 0
    if target_date:
        archived_count = replacements.archive_previous_replacements(conn, target_date, group_name=config.STUDENT_GROUP)

    saved_count = 0
    for r in reps:
        if replacements.save_replacement(conn, r, auto_archive_previous=False):
            saved_count += 1

    admins.log_action(conn, callback.from_user.id, "save_replacements", f"count={saved_count}, date={target_date}, archived={archived_count}")
    await callback.answer("Заміни збережено!")

    broadcast_info = ""
    if saved_count > 0:
        from services.updates import broadcast_message_to_users, get_active_user_ids
        broadcast_text = replacements.format_replacements_broadcast(reps, target_date or "")
        uids = get_active_user_ids(conn)
        asyncio.create_task(broadcast_message_to_users(callback.bot, uids, broadcast_text))
        broadcast_info = f"\n📢 Сповіщення автоматично надіслано студентам ({len(uids)} користувачів)."

    arch_info = f"\n📦 Попередніх замін автоматично архівовано: <b>{archived_count}</b>." if archived_count > 0 else ""
    await callback.message.edit_text(
        f"✅ <b>Успішно збережено замін: {saved_count}!</b>\n"
        f"📅 Дата: <code>{target_date}</code>\n"
        f"Вони вже враховані в розкладі занять.{broadcast_info}{arch_info}"
    )


@router.callback_query(F.data.startswith("rec_ed:"))
async def open_corrections_menu_handler(callback: CallbackQuery, state: FSMContext, conn):
    await state.clear()
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("Тільки для адміністраторів.", show_alert=True)
        return

    token = callback.data.split(":", 1)[1]
    reps = _PENDING_RECOGNITIONS.get(token)
    if reps is None:
        await callback.answer("Дані застаріли або вже збережені.", show_alert=True)
        return

    await callback.answer()
    if len(reps) == 1:
        text, kb = render_item_edit_menu(token, 0, reps[0])
    else:
        text, kb = render_corrections_menu(token, reps)
    await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("rec_it:"))
async def open_item_edit_handler(callback: CallbackQuery, state: FSMContext, conn):
    await state.clear()
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("Тільки для адміністраторів.", show_alert=True)
        return

    parts = callback.data.split(":")
    token, idx_str = parts[1], parts[2]
    reps = _PENDING_RECOGNITIONS.get(token)
    if reps is None:
        await callback.answer("Дані застаріли або вже збережені.", show_alert=True)
        return

    idx = int(idx_str)
    if idx >= len(reps):
        text, kb = render_corrections_menu(token, reps)
        await callback.message.edit_text(text, reply_markup=kb)
        return

    await callback.answer()
    text, kb = render_item_edit_menu(token, idx, reps[idx])
    await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("rec_done:"))
async def finish_corrections_handler(callback: CallbackQuery, state: FSMContext, conn):
    await state.clear()
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("Тільки для адміністраторів.", show_alert=True)
        return

    token = callback.data.split(":", 1)[1]
    reps = _PENDING_RECOGNITIONS.get(token)
    if reps is None:
        await callback.answer("Дані застаріли або вже збережені.", show_alert=True)
        return

    await callback.answer()
    text, kb = render_main_preview(token, reps)
    await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("rec_fld:"))
async def edit_field_prompt_handler(callback: CallbackQuery, state: FSMContext, conn):
    await state.clear()
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("Тільки для адміністраторів.", show_alert=True)
        return

    parts = callback.data.split(":")
    token, idx_str, field = parts[1], parts[2], parts[3]
    reps = _PENDING_RECOGNITIONS.get(token)
    if reps is None:
        await callback.answer("Дані застаріли або вже збережені.", show_alert=True)
        return

    idx = int(idx_str)
    if idx >= len(reps):
        await callback.answer()
        text, kb = render_corrections_menu(token, reps)
        await callback.message.edit_text(text, reply_markup=kb)
        return

    r = reps[idx]
    await callback.answer()

    if field == "lesson_number":
        pair_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="1 пара", callback_data=f"rec_sp:{token}:{idx}:1"),
                    InlineKeyboardButton(text="2 пара", callback_data=f"rec_sp:{token}:{idx}:2"),
                    InlineKeyboardButton(text="3 пара", callback_data=f"rec_sp:{token}:{idx}:3"),
                ],
                [
                    InlineKeyboardButton(text="4 пара", callback_data=f"rec_sp:{token}:{idx}:4"),
                    InlineKeyboardButton(text="5 пара", callback_data=f"rec_sp:{token}:{idx}:5"),
                    InlineKeyboardButton(text="6 пара", callback_data=f"rec_sp:{token}:{idx}:6"),
                ],
                [
                    InlineKeyboardButton(text="🔙 Скасувати", callback_data=f"rec_it:{token}:{idx}"),
                ],
            ]
        )
        await callback.message.edit_text(
            f"⏰ <b>Оберіть номер пари для заміни #{idx + 1}:</b>\nПоточна: <b>{r.get('lesson_number')} пара</b>",
            reply_markup=pair_kb,
        )
        return

    if field == "date":
        today_str = replacements.get_today_key()
        next_work_dt = working_days.get_next_working_day(conn)
        next_work_str = next_work_dt.strftime("%Y-%m-%d")
        w_idx = next_work_dt.weekday()
        day_name = schedule.DAY_NAMES_UK.get(schedule.DAY_KEYS_ORDER[w_idx] if w_idx < 5 else "saturday", "Робочий день")

        date_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"💼 Наст. роб. день: {next_work_str} ({day_name})",
                        callback_data=f"rec_sd:{token}:{idx}:{next_work_str}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=f"📅 Сьогодні ({today_str})",
                        callback_data=f"rec_sd:{token}:{idx}:{today_str}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="✏️ Ввести іншу дату вручну",
                        callback_data=f"rec_txtdate:{token}:{idx}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 Скасувати",
                        callback_data=f"rec_it:{token}:{idx}",
                    ),
                ],
            ]
        )
        await callback.message.edit_text(
            f"📅 <b>Оберіть дату для заміни #{idx + 1}:</b>\nПоточна: <code>{r.get('date')}</code>",
            reply_markup=date_kb,
        )
        return

    f_title = FIELD_NAMES_UK.get(field, field)
    await state.set_state(EditReplacementState.waiting_for_value)
    await state.update_data(token=token, idx=idx, field=field, message_id=callback.message.message_id)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Скасувати", callback_data=f"rec_it:{token}:{idx}")],
        ]
    )
    cur_val = r.get(field) or "не вказано"
    await callback.message.edit_text(
        f"✏️ <b>Редагування поля: {f_title}</b> (заміна #{idx + 1})\n\n"
        f"Поточне значення: <b>{html.escape(str(cur_val))}</b>\n\n"
        "Надішліть нове значення в цей чат текстовим повідомленням 👇\n"
        "<i>(надішліть «-» щоб очистити, або натисніть «↩️ Скасувати» нижче)</i>",
        reply_markup=cancel_kb,
    )


@router.callback_query(F.data.startswith("rec_txtdate:"))
async def edit_custom_date_prompt(callback: CallbackQuery, state: FSMContext, conn):
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("Тільки для адміністраторів.", show_alert=True)
        return

    parts = callback.data.split(":")
    token, idx_str = parts[1], parts[2]
    idx = int(idx_str)
    reps = _PENDING_RECOGNITIONS.get(token)
    if reps is None or idx >= len(reps):
        await callback.answer("Дані застаріли.", show_alert=True)
        return

    await state.set_state(EditReplacementState.waiting_for_value)
    await state.update_data(token=token, idx=idx, field="date", message_id=callback.message.message_id)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Скасувати", callback_data=f"rec_it:{token}:{idx}")],
        ]
    )
    await callback.answer()
    await callback.message.edit_text(
        f"📅 <b>Введіть дату для заміни #{idx + 1}:</b>\n\n"
        "Формат: <code>РРРР-ММ-ДД</code> (наприклад <code>2026-10-16</code>) або <code>ДД.ММ.РРРР</code>\n\n"
        "Надішліть дату текстовим повідомленням у чат 👇",
        reply_markup=cancel_kb,
    )


@router.callback_query(F.data.startswith("rec_sp:"))
async def set_pair_number_handler(callback: CallbackQuery, conn):
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("Тільки для адміністраторів.", show_alert=True)
        return

    parts = callback.data.split(":")
    token, idx_str, pair_str = parts[1], parts[2], parts[3]
    reps = _PENDING_RECOGNITIONS.get(token)
    if reps is None:
        await callback.answer("Дані застаріли.", show_alert=True)
        return

    idx = int(idx_str)
    if idx >= len(reps):
        await callback.answer()
        return

    reps[idx]["lesson_number"] = int(pair_str)
    replacements.fill_old_subject(conn, reps[idx], force=True)
    await callback.answer(f"Встановлено {pair_str} пару!")
    text, kb = render_item_edit_menu(token, idx, reps[idx])
    await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("rec_sd:"))
async def set_date_value_handler(callback: CallbackQuery, conn):
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("Тільки для адміністраторів.", show_alert=True)
        return

    parts = callback.data.split(":")
    token, idx_str, date_str = parts[1], parts[2], parts[3]
    reps = _PENDING_RECOGNITIONS.get(token)
    if reps is None:
        await callback.answer("Дані застаріли.", show_alert=True)
        return

    idx = int(idx_str)
    if idx >= len(reps):
        await callback.answer()
        return

    reps[idx]["date"] = date_str
    replacements.fill_old_subject(conn, reps[idx], force=True)
    await callback.answer(f"Дату встановлено: {date_str}!")
    text, kb = render_item_edit_menu(token, idx, reps[idx])
    await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("rec_del:"))
async def delete_replacement_item_handler(callback: CallbackQuery, conn):
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("Тільки для адміністраторів.", show_alert=True)
        return

    parts = callback.data.split(":")
    token, idx_str = parts[1], parts[2]
    reps = _PENDING_RECOGNITIONS.get(token)
    if reps is None:
        await callback.answer("Дані застаріли.", show_alert=True)
        return

    idx = int(idx_str)
    if idx < len(reps):
        reps.pop(idx)
        await callback.answer("Заміну видалено!")
    else:
        await callback.answer()

    text, kb = render_corrections_menu(token, reps)
    await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("rec_add:"))
async def add_replacement_item_handler(callback: CallbackQuery, conn):
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("Тільки для адміністраторів.", show_alert=True)
        return

    token = callback.data.split(":", 1)[1]
    reps = _PENDING_RECOGNITIONS.setdefault(token, [])

    last_pair = reps[-1].get("lesson_number", 0) if reps else 0
    next_pair = (last_pair + 1) if (isinstance(last_pair, int) and last_pair < 6) else 1
    default_date = reps[0].get("date") if reps else working_days.get_next_working_day_key(conn)

    new_item = {
        "date": default_date,
        "group_name": config.STUDENT_GROUP,
        "lesson_number": next_pair,
        "new_subject": "Новий предмет",
        "teacher": "",
        "room": "",
        "old_subject": "",
    }
    replacements.fill_old_subject(conn, new_item)
    reps.append(new_item)
    idx = len(reps) - 1

    await callback.answer("Додано заміну!")
    text, kb = render_item_edit_menu(token, idx, new_item)
    await callback.message.edit_text(text, reply_markup=kb)


@router.message(_private_only, EditReplacementState.waiting_for_value)
async def process_field_value_text(message: Message, state: FSMContext, conn):
    if not admins.is_admin(conn, message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    token = data.get("token")
    idx = data.get("idx")
    field = data.get("field")
    await state.clear()

    reps = _PENDING_RECOGNITIONS.get(token)
    if not reps or idx >= len(reps):
        await message.answer("⚠️ Дані замін застаріли або не знайдені.")
        return

    val = message.text.strip()
    if val.lower() in {"скасувати", "відміна", "/cancel", "cancel"}:
        await message.answer("Редагування скасовано.")
        text, kb = render_item_edit_menu(token, idx, reps[idx])
        await message.answer(text, reply_markup=kb)
        return

    if val == "-":
        val = ""
    else:
        if field in ("new_subject", "old_subject"):
            val = recognition.expand_subject(val)
        elif field == "teacher":
            val = recognition.expand_teacher(val)
        elif field == "date":
            val = normalize_date_input(val)

    reps[idx][field] = val
    if field == "date":
        replacements.fill_old_subject(conn, reps[idx], force=True)
    f_title = FIELD_NAMES_UK.get(field, field)
    await message.answer(f"✅ Поле <b>{f_title}</b> оновлено на: <b>{html.escape(str(val or '—'))}</b>")

    text, kb = render_item_edit_menu(token, idx, reps[idx])
    await message.answer(text, reply_markup=kb)


# ---------- Статус системи ----------

@router.message(Command("status"), StateFilter("*"))
async def show_bot_status(message: Message, conn):
    from bot import BOT_VERSION
    from services import maintenance, updates
    from zoneinfo import ZoneInfo
    from datetime import datetime

    uids = updates.get_active_user_ids(conn)
    reps = replacements.get_replacements(conn, group_name=config.STUDENT_GROUP)
    is_maint = maintenance.is_maintenance_active(conn)
    end_t = maintenance.get_maintenance_end_time(conn)

    maint_status = f"🔴 Увімкнено (до {end_t})" if is_maint else "🟢 Вимкнено (штатний режим)"
    now_str = datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%d.%m.%Y %H:%M:%S")

    prov = "Вимкнено"
    if config.RECOGNITION_API_KEY:
        prov = "Groq (qwen/qwen3.8-27b)" if config.RECOGNITION_API_KEY.startswith("gsk_") else "OpenAI (gpt-4o-mini)"

    text = (
        f"🟢 <b>Статус системи</b>\n\n"
        f"• <b>Версія бота:</b> <code>{BOT_VERSION}</code>\n"
        f"• <b>Група:</b> {html.escape(config.STUDENT_GROUP)}\n"
        f"• <b>Режим техробіт:</b> {maint_status}\n"
        f"• <b>База даних:</b> Підключено (OK)\n"
        f"• <b>Активних користувачів:</b> {len(uids)}\n"
        f"• <b>Збережених замін:</b> {len(reps)}\n"
        f"• <b>Серверний час:</b> <code>{now_str}</code> ({config.TIMEZONE})\n"
        f"• <b>Розпізнавання фото:</b> {prov}"
    )
    await message.answer(text)


# ---------- Редагування базового розкладу ----------

class EditScheduleState(StatesGroup):
    waiting_for_lesson = State()


def _schedule_day_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"base_day:{day}")]
        for day, label in schedule.DAY_NAMES_UK.items()
    ])


@router.message(_private_only, F.text == "✏️ Редагувати розклад", StateFilter("*"))
@router.message(_private_only, Command("edit_schedule", "get_schedule", "export_schedule"), StateFilter("*"))
async def open_schedule_editor(message: Message, state: FSMContext, conn):
    if not admins.is_admin(conn, message.from_user.id):
        raise BotError("Редагувати розклад можуть лише адміністратори.")

    await state.clear()
    await message.answer("Оберіть день для редагування розкладу.", reply_markup=_schedule_day_keyboard())


@router.callback_query(F.message.chat.type == "private", F.data.startswith("base_day:"))
async def select_schedule_day(callback: CallbackQuery, state: FSMContext, conn):
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("У вас немає прав адміністратора.", show_alert=True)
        return

    day = callback.data.split(":")[1]
    if day not in schedule.DAY_KEYS:
        await callback.answer("Оберіть день ще раз.", show_alert=True)
        return

    await state.clear()
    data = schedule.load_schedule_data(conn)
    lines = [f"<b>{schedule.DAY_NAMES_UK[day]}</b>"]
    keyboard = []
    for week, label in schedule.WEEK_NAMES_UK.items():
        lessons = schedule.get_base_schedule_for_day(data, day, week)
        slots = schedule.format_lesson_slots(lessons, compact=True)
        lines.append(f"<b>{label}</b>\n{slots}")
        keyboard.append([
            InlineKeyboardButton(text=f"{label[:3]}. {pair}", callback_data=f"base_pair:{day}:{week}:{pair}")
            for pair in schedule.PAIR_TIMES
        ])
    keyboard.append([InlineKeyboardButton(text="🔙 До днів", callback_data="base_days")])
    await callback.answer()
    await callback.message.edit_text(
        "\n\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


@router.callback_query(F.message.chat.type == "private", F.data == "base_days")
async def return_to_schedule_days(callback: CallbackQuery, state: FSMContext, conn):
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("У вас немає прав адміністратора.", show_alert=True)
        return

    await state.clear()
    await callback.answer()
    await callback.message.edit_text("Оберіть день для редагування розкладу.", reply_markup=_schedule_day_keyboard())


@router.callback_query(F.message.chat.type == "private", F.data.startswith("base_pair:"))
async def select_schedule_pair(callback: CallbackQuery, state: FSMContext, conn):
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("У вас немає прав адміністратора.", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Оберіть пару ще раз.", show_alert=True)
        return
    _, day, week, pair_key = parts
    pair = int(pair_key) if pair_key.isascii() and pair_key.isdecimal() else 0
    if day not in schedule.DAY_KEYS or week not in schedule.WEEK_NAMES_UK or pair not in schedule.PAIR_TIMES:
        await callback.answer("Оберіть пару ще раз.", show_alert=True)
        return

    await state.set_data({"day": day, "week": week, "pair": pair})
    await state.set_state(EditScheduleState.waiting_for_lesson)
    await callback.answer()
    await callback.message.answer(
        f"<b>{schedule.DAY_NAMES_UK[day]} · {schedule.WEEK_NAMES_UK[week]} · {pair} пара</b>\n\n"
        "У першому рядку напишіть назву предмета. З наступного рядка можна вказати "
        "викладачів у форматі <code>Ім’я | аудиторія</code>, по одному на рядок.\n\n"
        "Надішліть <code>-</code>, щоб прибрати заняття, або /cancel, щоб скасувати редагування."
    )


@router.message(_private_only, EditScheduleState.waiting_for_lesson, F.text)
async def save_schedule_pair(message: Message, state: FSMContext, conn):
    if not admins.is_admin(conn, message.from_user.id):
        await state.clear()
        return

    value = message.text.strip()
    if value == "/cancel":
        await state.clear()
        await message.answer("Редагування скасовано.", reply_markup=_schedule_day_keyboard())
        return

    if not value:
        await message.answer("Напишіть назву предмета або надішліть «-», щоб прибрати заняття.")
        return

    selection = await state.get_data()
    day, week, pair = selection["day"], selection["week"], selection["pair"]
    lesson = None
    if value != "-":
        subject, *teacher_lines = [line.strip() for line in value.splitlines() if line.strip()]
        teachers = []
        for line in teacher_lines:
            name, _, room = line.partition("|")
            if not name.strip() or "|" in room:
                await message.answer("Вкажіть кожного викладача окремим рядком у форматі «Ім’я | аудиторія».")
                return
            teachers.append({"name": name.strip(), "room": room.strip() or None})
        if len(subject) > 160 or len(value) > 500 or len(teachers) > 8:
            await message.answer(
                "Назва предмета має містити до 160 символів. "
                "Увесь запис може містити до 500 символів і до 8 викладачів."
            )
            return
        lesson = {"subject": subject, "teachers_raw": teachers}

    schedule.set_schedule_pair(conn, day, week, pair, lesson)
    await state.clear()
    admins.log_action(conn, message.from_user.id, "edit_schedule", f"{day}:{week}:{pair}")
    summary = html.escape(lesson["subject"]) if lesson else "немає заняття"
    await message.answer(
        f"✅ {schedule.DAY_NAMES_UK[day]} · {schedule.WEEK_NAMES_UK[week]}\n"
        f"{pair} пара — {summary}",
        reply_markup=_schedule_day_keyboard(),
    )


@router.message(_private_only, F.text == "📦 Архів замін", StateFilter("*"))
@router.message(_private_only, Command("archived_replacements"), StateFilter("*"))
async def show_archived_replacements(message: Message, conn):
    if not admins.is_admin(conn, message.from_user.id):
        return
    items = replacements.get_archived_replacements(conn, group_name=config.STUDENT_GROUP, limit=30)
    if not items:
        await message.answer(
            f"📦 <b>Архів замін для групи {html.escape(config.STUDENT_GROUP)}:</b>\n\n"
            "Архів порожній. Попередніх заархівованих замін ще немає."
        )
        return

    by_date = {}
    for it in items:
        by_date.setdefault(it["date"], []).append(it)

    lines = [f"📦 <b>Архів попередніх замін ({html.escape(config.STUDENT_GROUP)}):</b>\n"]
    for d, reps_list in by_date.items():
        lines.append(f"📅 <b>Дата: {d}</b>")
        for r in reps_list:
            old_s = f" (було: {r['old_subject']})" if r.get("old_subject") else ""
            t_info = f", викл. {r['teacher']}" if r.get("teacher") else ""
            r_info = f", ауд. {r['room']}" if r.get("room") else ""
            lines.append(f"• Пара №{r['lesson_number']}: <b>{html.escape(r['new_subject'])}</b>{old_s}{t_info}{r_info}")
        lines.append("")

    lines.append("<i>💡 Попередні заміни автоматично архівуються при збереженні замін на новий робочий день.</i>")
    await message.answer("\n".join(lines))

