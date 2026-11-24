import asyncio
import datetime
import html
import io
import logging
import re
import uuid
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from queue_bot import config
from queue_bot.handlers.common import (
    get_reply_menu,
    make_keyboard,
    make_reply_keyboard,
    show,
)
from queue_bot.services import admins, recognition, replacements, schedule, working_days
from queue_bot.services.errors import BotError
from queue_bot.services.schedule_notes import normalize_group, resolve_header_notes
from queue_bot.handlers.photo_albums import PhotoAlbums
from queue_bot.storage.database import transaction

log = logging.getLogger(__name__)
router = Router()

@dataclass
class ReplacementDraft:
    date: str
    items: list[dict]
    from_text: bool = False


_PENDING_RECOGNITIONS: dict[str, ReplacementDraft] = {}
_PHOTO_ALBUMS = PhotoAlbums()


class EditReplacementState(StatesGroup):
    waiting_for_value = State()


# ---------- Розклад: Сьогодні / Завтра / Тиждень ----------

_private_only = F.chat.type == "private"


def _schedule_keyboard(active, *, days=(), private=True):
    tabs = [('today', 'Сьогодні'), ('tomorrow', 'Завтра'), ('week', 'Тиждень')]
    rows = [[((f"✓ {label}" if active == key else label), f"sched_view:{key}") for key, label in tabs]]
    if days:
        buttons = [(f"{label} {day:%d.%m}", f"sched_view:day:{day.isoformat()}")
                   for day, label in zip(days, ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"))]
        rows.extend([buttons[:4], buttons[4:]])
    if private:
        rows.append([("Заміни", "sched_view:replacements"), ("Залишок пар", "sched_view:remaining")])
    return make_keyboard(rows)


def _weekly_schedule(conn, today, *, private):
    monday = today - datetime.timedelta(days=today.weekday())
    days = [monday + datetime.timedelta(days=i) for i in range(7)]
    data = schedule.read_schedule(conn)
    lines = [f"🗓 <b>Тиждень {days[0]:%d.%m}–{days[-1]:%d.%m}</b>"]
    for day in days:
        lessons, changes = schedule.get_actual_schedule(conn, data, day, config.STUDENT_GROUP)
        summary = ", ".join(dict.fromkeys(lesson["subject"] for lesson in lessons))
        if len(summary) > 160:
            summary = summary[:157] + "…"
        suffix = " · є заміни" if changes else ""
        lines.append(f"\n<b>{schedule.DAY_NAMES_UK[schedule.DAY_KEYS[day.weekday()]]} {day:%d.%m}</b>{suffix}\n"
                     f"{html.escape(summary) if summary else 'Занять немає'}")
    return "\n".join(lines), _schedule_keyboard("week", days=days, private=private)


def _daily_schedule(conn, day, *, active=None, private=True, heading=""):
    text = schedule.format_schedule_for_date(conn, schedule.read_schedule(conn), day, config.STUDENT_GROUP)
    return heading + text, _schedule_keyboard(active, private=private)


@router.message(_private_only, F.text == "📅 Розклад", StateFilter("*"))
async def open_schedule_menu(message: Message, state: FSMContext, conn):
    await state.clear()
    now = datetime.datetime.now(ZoneInfo(config.TIMEZONE))
    today = now.date()
    target = today if now.hour < 15 else working_days.get_next_working_day(conn, today)
    if target == today:
        active, label = "today", "на сьогодні"
    elif target == today + datetime.timedelta(days=1):
        active, label = "tomorrow", "на завтра"
    else:
        active, label = None, "на наступний навчальний день"
    text, markup = _daily_schedule(conn, target, active=active,
                                   heading=f"<b>Розклад {label}</b>\n\n")
    await message.answer(text, reply_markup=markup)


@router.message(_private_only, F.text == "📅 Сьогодні", StateFilter("*"))
@router.message(Command("today"), StateFilter("*"))
async def show_today_schedule(message: Message, conn):
    text, markup = _daily_schedule(conn, schedule.get_current_date(), active="today", private=message.chat.type == "private")
    await message.answer(text, reply_markup=markup)


@router.message(_private_only, F.text == "➡️ Завтра", StateFilter("*"))
@router.message(Command("tomorrow"), StateFilter("*"))
async def show_tomorrow_schedule(message: Message, conn):
    text, markup = _daily_schedule(conn, schedule.get_tomorrow_date(), active="tomorrow", private=message.chat.type == "private")
    await message.answer(text, reply_markup=markup)


@router.message(_private_only, F.text == "🗓 Весь тиждень", StateFilter("*"))
@router.message(Command("week"), StateFilter("*"))
async def show_week_schedule(message: Message, conn):
    text, markup = _weekly_schedule(conn, schedule.get_current_date(), private=message.chat.type == "private")
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("sched_view:"))
async def switch_schedule_view(callback: CallbackQuery, conn, subjects):
    _, action, *parts = callback.data.split(":")
    private = callback.message.chat.type == "private"
    if action in ("replacements", "remaining"):
        if not private:
            await callback.answer()
            return
        await callback.answer()
        if action == "replacements":
            await show_replacements(callback.message, conn, viewer_id=callback.from_user.id)
        else:
            await show_remaining_pairs(callback.message, conn, subjects)
        return

    today = schedule.get_current_date()
    if action == "week":
        text, markup = _weekly_schedule(conn, today, private=private)
    else:
        if action == "day" and parts:
            try:
                day = datetime.date.fromisoformat(parts[0])
            except ValueError:
                await callback.answer("Некоректна дата.", show_alert=True)
                return
        elif action == "today":
            day = today
        elif action == "tomorrow":
            day = today + datetime.timedelta(days=1)
        else:
            await callback.answer("Оберіть день ще раз.", show_alert=True)
            return
        active = "today" if day == today else "tomorrow" if day == today + datetime.timedelta(days=1) else None
        text, markup = _daily_schedule(conn, day, active=active, private=private)
    await callback.answer()
    await show(callback, text, markup)


@router.message(_private_only, F.text == "📚 Пари до кінця семестру", StateFilter("*"))
@router.message(_private_only, Command("remaining"), StateFilter("*"))
async def show_remaining_pairs(message: Message, conn, subjects):
    from queue_bot.services.lesson_scheduler import subject_name

    plans = schedule.get_remaining_pairs(conn)
    if not plans:
        await message.answer("Адміністратор ще не вніс залишок пар.")
        return
    if not any(plan["remaining"] for plan in plans.values()):
        await message.answer("За предметами з внесеним залишком пар більше немає.")
        return

    catalog = {subject_name(subject["name"]): subject["name"] for subject in subjects}
    rows = [(catalog.get(key, plan["subject_name"]), plan) for key, plan in plans.items() if plan["remaining"]]
    lines = ["📚 <b>Пари до кінця семестру</b>", ""]
    for name, plan in sorted(rows, key=lambda row: row[0].casefold()):
        last = plan["finish_date"]
        detail = (f"остання пара {datetime.date.fromisoformat(last):%d.%m.%Y}"
                  if last else "дати останньої пари ще немає в розкладі")
        lines.append(f"{html.escape(name)} — <b>{plan['remaining']}</b> · {detail}")
    await message.answer("\n".join(lines))


@router.message(Command("schedule"), StateFilter("*"))
async def show_base_schedule(message: Message, conn):
    sch_data = schedule.read_schedule(conn)
    group = html.escape(config.STUDENT_GROUP)
    lines = [f"📌 <b>Базовий розклад групи {group}</b>"]
    for day_key in schedule.DAY_KEYS:
        slots = schedule.get_base_week_slots(sch_data, day_key)
        if day_key in ("saturday", "sunday") and not any(any(variants.values()) for variants in slots.values()):
            continue
        day_text = f"<b>{schedule.DAY_NAMES_UK[day_key]}</b>\n{schedule.format_base_week_slots(slots)}"
        if len("\n\n".join([*lines, day_text])) > 4096:
            await message.answer("\n\n".join(lines))
            lines.clear()
        lines.append(day_text)
    await message.answer("\n\n".join(lines))


# ---------- Заміни ----------

@router.message(_private_only, F.text.in_({"🔄 Заміни", "🔄 Активні заміни"}), StateFilter("*"))
@router.message(Command("replacements"), StateFilter("*"))
async def show_replacements(message: Message, conn, *, viewer_id=None):
    from queue_bot.services.channel_import import confirmed_results, approval_keyboard, format_no_replacements
    results = [r for r in confirmed_results(conn) if r['date'] >= replacements.get_today_key()]
    markup = None
    if results and message.chat.type == 'private' and (viewer_id or message.from_user.id) == config.OWNER_ID:
        markup = approval_keyboard(results[0]['post_id'], confirmed=True)
    items = replacements.get_replacements(conn, group_name=config.STUDENT_GROUP)
    lines = [f"🔄 <b>Актуальні заміни для групи {html.escape(config.STUDENT_GROUP)}:</b>\n"]
    for result in results:
        if not result['replacements'] and not any(item['date'] == result['date'] for item in items):
            lines.append(format_no_replacements(result['date']))
    lines.extend(replacements.format_replacement(item) for item in items)
    if len(lines) == 1:
        lines.append('Підтверджених даних про заміни ще немає. Бот перевіряє канал автоматично.')
    await message.answer('\n\n'.join(lines), reply_markup=markup)


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


def render_main_preview(token: str, draft: ReplacementDraft) -> tuple[str, InlineKeyboardMarkup]:
    channel = token.startswith("ch")
    buttons = []
    if channel or draft.items:
        publish = f"ch_sv:{token[2:]}" if channel else f"rec_cf:{token}"
        buttons.append([("✅ Опублікувати", publish)])
    buttons.append([
        ("📅 Змінити дату", f"rec_date:{token}"),
        ("✏️ Виправити пари", f"rec_ed:{token}"),
    ])
    buttons.append([("Закрити" if channel else "Відхилити", f"rec_rj:{token}")])
    return replacements.format_review(draft.date, draft.items), _replacement_markup(*buttons)


def _replacement_editor_allowed(conn, uid, token):
    return admins.is_admin(conn, uid) and (not token.startswith("ch") or uid == config.OWNER_ID)


def _replacement_markup(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=action) for label, action in row]
        for row in rows
    ])


# ---------- Обробка фотографій таблиць замін ----------

_NOTICE_DATE = r"(?P<day>післязавтра|завтра|сьогодні|\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})"
_SCHEDULE_REFERENCE = r"\b(?:за|по|згідно\s+з)\s+розклад\w*\b"
_TEXT_REPLACEMENT = re.compile(rf"^\s*{_NOTICE_DATE}\b.*\bпар\w*\b.*{_SCHEDULE_REFERENCE}", re.IGNORECASE | re.DOTALL)
_RELATIVE_NOTICE_DAYS = {"сьогодні": 0, "завтра": 1, "післязавтра": 2}


def is_text_replacement(message: Message) -> bool:
    return bool(message.text and _TEXT_REPLACEMENT.match(message.text))


async def handle_text_replacement(message: Message, conn):
    if not admins.is_admin(conn, message.from_user.id):
        return

    text = " ".join(message.text.split())
    groups = re.findall(r"\b[а-яіїєґa-z]{1,5}[-–]\d{2,4}\b", text, re.IGNORECASE)
    if groups and normalize_group(config.STUDENT_GROUP) not in {
        normalize_group(group.replace("–", "-")) for group in groups
    }:
        return
    match = _TEXT_REPLACEMENT.match(text)
    value = match["day"].casefold()
    if value in _RELATIVE_NOTICE_DAYS:
        sent_at = (message.forward_origin or message).date
        sent_day = sent_at.astimezone(ZoneInfo(config.TIMEZONE)).date()
        day = (sent_day + datetime.timedelta(days=_RELATIVE_NOTICE_DAYS[value])).isoformat()
    else:
        day = normalize_date_input(value)
    replacements.validate_publication_date(day)

    week = schedule.get_week_type(datetime.date.fromisoformat(day))
    note = recognition.parse_header_note(text, week)
    if note is None or len(re.findall(_SCHEDULE_REFERENCE, text, re.IGNORECASE)) != 1:
        raise BotError("Не вдалося визначити заміну. Надішліть одну зміну у форматі «Завтра 4 пара за розкладом 1 пари вівторка».")
    items = resolve_header_notes({"scheduleOverrides": [note]}, day, config.STUDENT_GROUP, [],
                                 schedule.read_schedule(conn))
    token = uuid.uuid4().hex[:8]
    draft = ReplacementDraft(day, items, from_text=True)
    _PENDING_RECOGNITIONS[token] = draft
    preview, markup = render_main_preview(token, draft)
    await message.answer(preview, reply_markup=markup)


@router.message(_private_only, F.photo)
async def handle_photo_replacement(message: Message, conn):
    if not admins.is_admin(conn, message.from_user.id):
        return

    try:
        messages = await _PHOTO_ALBUMS.collect(message)
    except BotError as err:
        await message.answer(f"⚠️ {html.escape(str(err))}")
        return
    if messages is None:
        return
    bot = message.bot
    status_msg = await message.answer(f"⏳ Отримано фото: {len(messages)}. Аналізую таблицю замін...")

    try:
        images = []
        for part in messages:
            file = await bot.get_file(part.photo[-1].file_id)
            img_stream = io.BytesIO()
            await bot.download_file(file.file_path, destination=img_stream)
            images.append(img_stream.getvalue())

        result = await recognition.recognize_replacements_from_images(images, group_name=config.STUDENT_GROUP, conn=conn)
        token = uuid.uuid4().hex[:8]
        draft = ReplacementDraft(result["date"], result["replacements"])
        _PENDING_RECOGNITIONS[token] = draft
        preview_text, markup = render_main_preview(token, draft)
        await status_msg.edit_text(preview_text, reply_markup=markup)

    except BotError as err:
        await status_msg.edit_text(f"⚠️ {html.escape(str(err))}")
    except Exception as e:
        log.exception(f"Error handling replacement photo: {e}")
        await status_msg.edit_text(f"⚠️ <b>Помилка розпізнавання:</b> {html.escape(str(e))}")


@router.callback_query(F.data.startswith("rec_rj:"))
async def reject_recognition(callback: CallbackQuery, state: FSMContext, conn):
    token = callback.data.split(":", 1)[1]
    if not _replacement_editor_allowed(conn, callback.from_user.id, token):
        await callback.answer("Недостатньо прав.", show_alert=True)
        return
    await state.clear()
    _PENDING_RECOGNITIONS.pop(token, None)
    await callback.answer()
    await callback.message.edit_text("Чернетку закрито.")


@router.callback_query(F.data.startswith("rec_cf:"))
async def confirm_recognition(callback: CallbackQuery, state: FSMContext, conn):
    token = callback.data.split(":", 1)[1]
    if token.startswith("ch") or not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("У вас немає прав адміністратора.", show_alert=True)
        return
    await state.clear()

    draft = _PENDING_RECOGNITIONS.get(token)
    if not draft or not draft.items:
        await callback.answer("Дані вже збережені або застаріли.", show_alert=True)
        return

    target_date = draft.date
    try:
        replacements.validate_review(target_date, draft.items)
    except BotError as err:
        await callback.answer(str(err), show_alert=True)
        return

    reps = [dict(item, date=target_date, group_name=config.STUDENT_GROUP) for item in draft.items]
    with transaction(conn):
        archived_count = 0
        if draft.from_text:
            for pair in {item["lesson_number"] for item in reps}:
                conn.execute("""UPDATE replacements SET is_archived = TRUE
                    WHERE date = ? AND group_name = ? AND lesson_number = ?""",
                    (target_date, config.STUDENT_GROUP, pair))
        else:
            archived_count = replacements.archive_previous_replacements(conn, target_date, group_name=config.STUDENT_GROUP)
        saved_count = sum(replacements.save_replacement(conn, item, auto_archive_previous=False) for item in reps)
        admins.log_action(conn, callback.from_user.id, "save_replacements", f"count={saved_count}, date={target_date}, archived={archived_count}")

    _PENDING_RECOGNITIONS.pop(token, None)
    await callback.answer("Заміни збережено!")

    broadcast_info = ""
    if saved_count > 0:
        from queue_bot.services.updates import broadcast_message_to_users, get_active_user_ids
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


@router.callback_query(F.data.regexp(r"^rec_(done|ed|date|sd|it|add|del):"))
async def edit_replacement_draft(callback: CallbackQuery, state: FSMContext, conn):
    action, token, *args = callback.data.split(":")
    if not _replacement_editor_allowed(conn, callback.from_user.id, token):
        await callback.answer("Недостатньо прав.", show_alert=True)
        return
    draft = _PENDING_RECOGNITIONS.get(token)
    if draft is None:
        await callback.answer("Чернетка застаріла. Відкрийте заміни знову.", show_alert=True)
        return
    await state.clear()
    back = [("‹ До перевірки", f"rec_done:{token}")]

    if action == "rec_ed":
        pair_buttons = [[(
            f"{item['lesson_number']} пара · {item['new_subject'][:32]}",
            f"rec_it:{token}:{idx}",
        )] for idx, item in enumerate(draft.items)]
        text = "Оберіть пару для виправлення."
        markup = _replacement_markup(*pair_buttons, [("➕ Додати пару", f"rec_add:{token}")], back)
    elif action == "rec_date":
        today = replacements.get_today_key()
        next_day = working_days.get_next_working_day(conn).isoformat()
        text = (
            f"<b>Дата всіх замін</b> · {html.escape(draft.date)}\n\n"
            "Оберіть дату або надішліть її текстом у форматі <code>ДД.ММ.РРРР</code>."
        )
        markup = _replacement_markup([
            (f"Сьогодні · {today[8:]}.{today[5:7]}", f"rec_sd:{token}:{today}"),
            (f"Наст. навч. день · {next_day[8:]}.{next_day[5:7]}", f"rec_sd:{token}:{next_day}"),
        ], back)
        await state.set_state(EditReplacementState.waiting_for_value)
        await state.update_data(token=token, message_id=callback.message.message_id)
    elif action == "rec_sd":
        try:
            replacements.validate_publication_date(args[0])
        except BotError as err:
            await callback.answer(str(err), show_alert=True)
            return
        draft.date = args[0]
        text, markup = render_main_preview(token, draft)
    elif action == "rec_done":
        text, markup = render_main_preview(token, draft)
    else:
        index = None if action == "rec_add" else int(args[0])
        if index is not None and not 0 <= index < len(draft.items):
            await callback.answer("Цієї заміни вже немає.", show_alert=True)
            return
        if action == "rec_del":
            draft.items.pop(index)
            text, markup = render_main_preview(token, draft)
        else:
            item = draft.items[index] if index is not None else None
            fields = ("lesson_number", "new_subject", "teacher", "room", "old_subject")
            example = " | ".join(str(item.get(field) or "-") for field in fields) if item else "2 | Назва предмета"
            text = (
                "<b>Додати пару</b>" if item is None else "<b>Виправити пару</b>"
            ) + (
                "\n\nНадішліть номер пари й предмет через <code>|</code>. За потреби додайте викладача, "
                "аудиторію та попередній предмет у цьому порядку. Щоб очистити поле, введіть тире.\n\n"
                f"<code>{html.escape(example)}</code>"
            )
            remove = [[("Видалити пару", f"rec_del:{token}:{index}")]] if item else []
            markup = _replacement_markup(*remove, back)
            await state.set_state(EditReplacementState.waiting_for_value)
            await state.update_data(token=token, message_id=callback.message.message_id, edit_pair=True,
                                    index=index, previous=item.copy() if item else None)

    await callback.answer()
    await callback.message.edit_text(text, reply_markup=markup)


@router.message(_private_only, F.text, EditReplacementState.waiting_for_value)
async def process_field_value_text(message: Message, state: FSMContext, conn):
    data = await state.get_data()
    token = data.get("token", "")
    draft = _PENDING_RECOGNITIONS.get(token)
    if not _replacement_editor_allowed(conn, message.from_user.id, token):
        await state.clear()
        return
    if draft is None:
        await state.clear()
        await message.answer("Чернетка застаріла. Відкрийте заміни знову.")
        return

    value = message.text.strip()
    if value.lower() not in {"скасувати", "відміна", "/cancel", "cancel"}:
        try:
            if data.get("edit_pair"):
                index = data["index"]
                previous = data["previous"]
                if index is not None and (index >= len(draft.items) or draft.items[index] != previous):
                    raise BotError("Цю пару вже змінено. Відкрийте її знову.")
                fields = [part.strip() for part in value.split("|")]
                if not 2 <= len(fields) <= 5 or not re.fullmatch(r"[1-9]|10", fields[0]):
                    raise BotError("Введіть номер пари від 1 до 10 і назву у форматі «2 | Архітектура».")
                number, subject = fields[:2]
                if not subject or subject == "-" or any(len(part) > 200 for part in fields[1:]):
                    raise BotError("Вкажіть предмет. Кожне текстове поле має містити до 200 символів.")
                item = dict(previous or {}, lesson_number=int(number), new_subject=recognition.expand_subject(subject))
                for field, part in zip(("teacher", "room", "old_subject"), fields[2:]):
                    value = "" if part == "-" else part
                    if field == "teacher":
                        value = recognition.expand_teacher(value)
                    elif field == "old_subject":
                        value = recognition.expand_subject(value)
                    item[field] = value
                if index is None:
                    draft.items.append(item)
                else:
                    draft.items[index] = item
            else:
                day = normalize_date_input(value)
                replacements.validate_publication_date(day)
                draft.date = day
        except BotError as err:
            await message.answer(f"⚠️ {html.escape(str(err))}")
            return

    await state.clear()
    text, markup = render_main_preview(token, draft)
    try:
        await message.bot.edit_message_text(text=text, chat_id=message.chat.id, message_id=data["message_id"],
                                            reply_markup=markup)
    except TelegramBadRequest as err:
        if "message is not modified" not in str(err).lower():
            await message.answer(text, reply_markup=markup)


# ---------- Редагування базового розкладу ----------

class EditScheduleState(StatesGroup):
    waiting_for_lesson = State()


def _schedule_day_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"base_day:{day}")]
        for day, label in schedule.DAY_NAMES_UK.items()
    ])


@router.message(_private_only, F.text == "✏️ Редагувати розклад", StateFilter("*"))
@router.message(_private_only, Command("edit_schedule"), StateFilter("*"))
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
    slots = schedule.get_base_week_slots(schedule.read_schedule(conn), day)
    keyboard = [
        [
            InlineKeyboardButton(
                text=f"{pair} пара" if week == "both" else f"{pair} пара · {schedule.WEEK_NAMES_UK[week]}",
                callback_data=f"base_pair:{day}:{week}:{pair}",
            )
            for week in variants
        ]
        for pair, variants in slots.items()
        if pair in schedule.PAIR_TIMES
    ]
    keyboard.append([InlineKeyboardButton(text="🔙 До днів", callback_data="base_days")])
    await callback.answer()
    await callback.message.edit_text(
        f"<b>{schedule.DAY_NAMES_UK[day]}</b>\n\n{schedule.format_base_week_slots(slots)}",
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
    if day not in schedule.DAY_KEYS or week not in schedule.EDIT_WEEK_NAMES_UK or pair not in schedule.PAIR_TIMES:
        await callback.answer("Оберіть пару ще раз.", show_alert=True)
        return

    await state.set_data({"day": day, "week": week, "pair": pair})
    await state.set_state(EditScheduleState.waiting_for_lesson)
    await callback.answer()
    markup = None
    if week == "both":
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Лише {label.lower()}", callback_data=f"base_pair:{day}:{scope}:{pair}")]
            for scope, label in schedule.WEEK_NAMES_UK.items()
        ])
    await callback.message.answer(
        f"<b>{schedule.DAY_NAMES_UK[day]} · {schedule.EDIT_WEEK_NAMES_UK[week]} · {pair} пара</b>\n\n"
        "У першому рядку напишіть назву предмета. З наступного рядка можна вказати "
        "викладачів у форматі <code>Ім’я | аудиторія</code>, по одному на рядок. За потреби додайте рядок <code>Примітка: текст</code>.\n\n"
        "Надішліть <code>-</code>, щоб прибрати заняття, або /cancel, щоб скасувати редагування.",
        reply_markup=markup,
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
        note = None
        for line in teacher_lines:
            if line.lower().startswith("примітка:"):
                note = line.partition(":")[2].strip()
                continue
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
        if note is not None:
            lesson["note"] = note

    schedule.set_schedule_pair(conn, day, week, pair, lesson)
    await state.clear()
    admins.log_action(conn, message.from_user.id, "edit_schedule", f"{day}:{week}:{pair}")
    summary = html.escape(lesson["subject"]) if lesson else "немає заняття"
    await message.answer(
        f"✅ {schedule.DAY_NAMES_UK[day]} · {schedule.EDIT_WEEK_NAMES_UK[week]}\n"
        f"{pair} пара — {summary}",
        reply_markup=_schedule_day_keyboard(),
    )


@router.callback_query(F.data.regexp(r"^ch_(yes|no|sv):"))
async def confirm_channel_import(callback: CallbackQuery, conn):
    from queue_bot.services.channel_import import get_state, set_state, format_no_replacements, approval_keyboard
    if callback.from_user.id != config.OWNER_ID or not admins.is_admin(conn, callback.from_user.id):
        await callback.answer('Тільки для власника бота.', show_alert=True)
        return
    action, post_id = callback.data.split(':', 1)
    key = f'channel_draft:{config.REPLACEMENTS_CHANNEL}:{post_id}'
    draft = get_state(conn, key)
    candidate = _PENDING_RECOGNITIONS.get(f'ch{post_id}') if action == 'ch_sv' else None
    if (not draft or (action == 'ch_sv' and candidate is None)
            or (candidate is None and draft['status'] != 'pending' and not draft.get('edit'))):
        await callback.answer('Результат уже оброблено або застарів.', show_alert=True)
        return
    if action == 'ch_no':
        if draft['status'] != 'confirmed':
            draft['status'] = 'rejected'
        draft.pop('edit', None)
        set_state(conn, key, draft)
        _PENDING_RECOGNITIONS.pop(f'ch{post_id}', None)
        await callback.answer('Відхилено')
        await callback.message.edit_text('❌ Результат із каналу відхилено. Розклад не змінено.')
        return
    was_confirmed = draft['status'] == 'confirmed'
    previous_date = draft['date']
    edited = draft.pop('edit', None)
    if candidate is not None:
        draft.update(date=candidate.date, replacements=candidate.items)
    elif edited:
        draft.update(edited)
    try:
        replacements.validate_review(draft['date'], draft['replacements'])
    except BotError as err:
        await callback.answer(str(err), show_alert=True)
        return
    draft['replacements'] = [dict(item, date=draft['date'], group_name=config.STUDENT_GROUP)
                             for item in draft['replacements']]
    # No await inside the transaction: concurrent callbacks cannot apply twice.
    with transaction(conn):
        if was_confirmed and previous_date != draft['date']:
            conn.execute('DELETE FROM replacements WHERE date = ? AND group_name = ?',
                         (previous_date, config.STUDENT_GROUP))
        replacements.archive_previous_replacements(conn, draft['date'], group_name=config.STUDENT_GROUP)
        conn.execute('DELETE FROM replacements WHERE date = ? AND group_name = ?',
                     (draft['date'], config.STUDENT_GROUP))
        for item in draft['replacements']:
            replacements.save_replacement(conn, item, auto_archive_previous=False)
        draft['status'] = 'confirmed'
        set_state(conn, key, draft)
        admins.log_action(conn, callback.from_user.id, 'confirm_channel_import', draft['source'])
    _PENDING_RECOGNITIONS.pop(f'ch{post_id}', None)
    text = (replacements.format_replacements_broadcast(draft['replacements'], draft['date'])
            if draft['replacements'] else format_no_replacements(draft['date']))
    if was_confirmed:
        text = '✏️ <b>Виправлення попередніх даних</b>\n\n' + text
    await callback.answer('Підтверджено')
    await callback.message.edit_text('✅ Підтверджено.\n\n' + text,
                                     reply_markup=approval_keyboard(post_id, confirmed=True))
    from queue_bot.services.updates import broadcast_message_to_users, get_active_user_ids
    full = schedule.format_schedule_for_date(conn, schedule.read_schedule(conn),
                                             datetime.date.fromisoformat(draft['date']), config.STUDENT_GROUP)
    notes = dict.fromkeys(r['source_note'] for r in draft['replacements'] if r.get('source_note'))
    full = '🗓 <b>Повний розклад із замінами</b>\n\n' + full + ''.join(
        f'\n\n📝 <b>Примітка:</b> {html.escape(str(n))}' for n in notes)
    users = get_active_user_ids(conn)
    from queue_bot.services.schedule_notifier import has_sent_today
    day_before = datetime.date.fromisoformat(draft['date']) - datetime.timedelta(days=1)
    # Resend the timetable only if the 17:00 broadcast for that day already went out without these replacements.
    resend = has_sent_today(conn, f'evening_{day_before.isoformat()}')

    async def send_both():
        await broadcast_message_to_users(callback.bot, users, text)
        if resend:
            await broadcast_message_to_users(callback.bot, users, full)
    asyncio.create_task(send_both())


@router.callback_query(F.data.startswith('ch_edit:'))
async def edit_channel_result(callback: CallbackQuery, state: FSMContext, conn):
    from queue_bot.services.channel_import import get_state

    await state.clear()
    if callback.from_user.id != config.OWNER_ID or not admins.is_admin(conn, callback.from_user.id):
        await callback.answer('Тільки для власника бота.', show_alert=True)
        return
    post_id = callback.data.split(':', 1)[1]
    draft = get_state(conn, f'channel_draft:{config.REPLACEMENTS_CHANNEL}:{post_id}')
    if not draft or 'date' not in draft:
        await callback.answer('Дані не знайдено.', show_alert=True)
        return

    token = f'ch{post_id}'
    source = draft.get('edit') or draft
    candidate = ReplacementDraft(source['date'], [item.copy() for item in source['replacements']])
    _PENDING_RECOGNITIONS[token] = candidate
    await callback.answer()
    text, markup = render_main_preview(token, candidate)
    await callback.message.answer(text, reply_markup=markup)
