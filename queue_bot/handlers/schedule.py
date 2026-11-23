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

from queue_bot import config
from queue_bot.handlers.common import (
    get_reply_menu,
    get_schedule_reply_menu,
    make_keyboard,
    make_reply_keyboard,
)
from queue_bot.services import admins, recognition, replacements, schedule, working_days
from queue_bot.services.errors import BotError
from queue_bot.handlers.photo_albums import PhotoAlbums
from queue_bot.storage.database import transaction

log = logging.getLogger(__name__)
router = Router()

# Тимчасове збереження розпізнаних замін для підтвердження
_PENDING_RECOGNITIONS: dict[str, list[dict]] = {}
_PHOTO_ALBUMS = PhotoAlbums()


class EditReplacementState(StatesGroup):
    waiting_for_value = State()


# ---------- Розклад: Сьогодні / Завтра / Тиждень ----------

_private_only = F.chat.type == "private"


@router.message(_private_only, F.text == "📅 Розклад", StateFilter("*"))
async def open_schedule_menu(message: Message, state: FSMContext, conn):
    await state.clear()
    sch_data = schedule.read_schedule(conn)
    now = datetime.datetime.now(ZoneInfo(config.TIMEZONE))

    if now.hour >= 15:
        target_dt = working_days.get_next_working_day(conn, now.date())
        period = "на завтра" if target_dt == now.date() + datetime.timedelta(days=1) else "на наступний навчальний день"
        date_heading = f"{period} ({target_dt.strftime('%d.%m.%Y')})"
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
    sch_data = schedule.read_schedule(conn)
    today_dt = schedule.get_current_date()
    text = schedule.format_schedule_for_date(conn, sch_data, today_dt, config.STUDENT_GROUP)
    await message.answer(text)


@router.message(_private_only, F.text == "➡️ Завтра", StateFilter("*"))
@router.message(Command("tomorrow"), StateFilter("*"))
async def show_tomorrow_schedule(message: Message, conn):
    sch_data = schedule.read_schedule(conn)
    tomorrow_dt = schedule.get_tomorrow_date()
    text = schedule.format_schedule_for_date(conn, sch_data, tomorrow_dt, config.STUDENT_GROUP)
    await message.answer(text)


@router.message(_private_only, F.text == "🗓 Весь тиждень", StateFilter("*"))
@router.message(Command("week"), StateFilter("*"))
async def show_week_schedule(message: Message, conn):
    sch_data = schedule.read_schedule(conn)
    today_dt = schedule.get_current_date()
    days = schedule.format_week_schedule(conn, sch_data, today_dt, config.STUDENT_GROUP)
    for day_text in days:
        await message.answer(day_text)


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
async def show_replacements(message: Message, conn):
    from queue_bot.services.channel_import import confirmed_results, approval_keyboard, format_no_replacements
    results = [r for r in confirmed_results(conn) if r['date'] >= replacements.get_today_key()]
    markup = None
    if results and message.chat.type == 'private' and message.from_user.id == config.OWNER_ID:
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
    if token.startswith("ch"):
        body = ("\n\n────────────────\n\n".join(replacements.format_replacement(r) for r in reps)
                if reps else "Замін немає.")
        return f"✏️ <b>Виправлені заміни:</b>\n\n{body}", InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💾 Зберегти виправлення", callback_data=f"ch_sv:{token[2:]}")],
            [InlineKeyboardButton(text="✏️ Ще редагувати", callback_data=f"rec_ed:{token}"),
             InlineKeyboardButton(text="➕ Додати пару", callback_data=f"rec_add:{token}")],
        ])
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
        if token.startswith("ch"):
            markup.inline_keyboard.insert(1, [InlineKeyboardButton(text="🚫 Замін немає — зберегти", callback_data=f"ch_sv:{token[2:]}")])
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
    reps = _PENDING_RECOGNITIONS.get(token)
    if not reps:
        await callback.answer("Дані вже збережені або застаріли.", show_alert=True)
        return

    try:
        for item in reps:
            replacements.validate_publication_date(item.get("date"))
    except BotError as err:
        await callback.answer(str(err), show_alert=True)
        return

    target_date = reps[0]["date"]
    with transaction(conn):
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

    choices = schedule_choices(field, conn)
    buttons = [InlineKeyboardButton(text=value[:40], callback_data=f"rec_pk:{token}:{idx}:{field}:{i}")
               for i, value in enumerate(choices)]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="🧹 Очистити", callback_data=f"rec_pk:{token}:{idx}:{field}:-1"),
                 InlineKeyboardButton(text="↩️ Скасувати", callback_data=f"rec_it:{token}:{idx}")])
    cur_val = r.get(field) or "не вказано"
    await callback.message.edit_text(
        f"✏️ <b>Редагування поля: {f_title}</b> (заміна #{idx + 1})\n\n"
        f"Поточне значення: <b>{html.escape(str(cur_val))}</b>\n\n"
        + ("Оберіть кнопкою або напишіть своє значення в чат 👇" if choices else "Напишіть нове значення в чат 👇"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def schedule_choices(field: str, conn=None) -> list[str]:
    """Subjects, teachers or rooms from the saved timetable."""
    data = schedule.read_schedule(conn)
    items = [item for day in schedule.DAY_KEYS for week in schedule.WEEK_NAMES_UK
             for item in schedule.get_base_schedule_for_day(data, day, week)]
    if field in ("new_subject", "old_subject"):
        values = [x.get("subject") for x in items]
    elif field in ("teacher", "room"):
        key = "name" if field == "teacher" else "room"
        values = [t.get(key) for x in items for t in (x.get("teachers_raw") or [{key: x.get(field)}])]
    else:
        return []
    return sorted({str(v) for v in values if v})[:30]


@router.callback_query(F.data.startswith("rec_pk:"))
async def pick_field_value_handler(callback: CallbackQuery, state: FSMContext, conn):
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("Тільки для адміністраторів.", show_alert=True)
        return
    _, token, idx_str, field, choice = callback.data.split(":")
    reps = _PENDING_RECOGNITIONS.get(token)
    idx, choice = int(idx_str), int(choice)
    choices = schedule_choices(field, conn)
    if reps is None or idx >= len(reps) or choice >= len(choices) or field not in FIELD_NAMES_UK:
        await callback.answer("Дані застаріли.", show_alert=True)
        return
    await state.clear()
    reps[idx][field] = "" if choice < 0 else choices[choice]
    await callback.answer("Збережено")
    text, kb = render_item_edit_menu(token, idx, reps[idx])
    await callback.message.edit_text(text, reply_markup=kb)


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

    try:
        replacements.validate_publication_date(date_str)
    except BotError as err:
        await callback.answer(str(err), show_alert=True)
        return

    reps[idx]["date"] = date_str
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
    if reps:
        default_date = reps[0].get("date")
    elif token.startswith("ch"):
        from queue_bot.services.channel_import import get_state
        draft = get_state(conn, f"channel_draft:{config.REPLACEMENTS_CHANNEL}:{token[2:]}") or {}
        default_date = (draft.get("edit") or draft).get("date") or working_days.get_next_working_day_key(conn)
    else:
        default_date = working_days.get_next_working_day_key(conn)

    new_item = {
        "date": default_date,
        "group_name": config.STUDENT_GROUP,
        "lesson_number": next_pair,
        "new_subject": "Новий предмет",
        "teacher": "",
        "room": "",
        "old_subject": "",
    }
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

    reps = _PENDING_RECOGNITIONS.get(token)
    if not reps or not isinstance(idx, int) or not 0 <= idx < len(reps):
        await state.clear()
        await message.answer("⚠️ Дані замін застаріли або не знайдені.")
        return

    val = (message.text or "").strip()
    if val.lower() in {"скасувати", "відміна", "/cancel", "cancel"}:
        await state.clear()
        await message.answer("Редагування скасовано.")
        text, kb = render_item_edit_menu(token, idx, reps[idx])
        await message.answer(text, reply_markup=kb)
        return

    if field == "date":
        val = normalize_date_input(val)
        try:
            replacements.validate_publication_date(val)
        except BotError as err:
            await message.answer(f"⚠️ {err}")
            return
    elif val == "-":
        val = ""
    elif field in ("new_subject", "old_subject"):
        val = recognition.expand_subject(val)
    elif field == "teacher":
        val = recognition.expand_teacher(val)

    await state.clear()
    reps[idx][field] = val
    f_title = FIELD_NAMES_UK.get(field, field)
    await message.answer(f"✅ Поле <b>{f_title}</b> оновлено на: <b>{html.escape(str(val or '—'))}</b>")

    text, kb = render_item_edit_menu(token, idx, reps[idx])
    await message.answer(text, reply_markup=kb)


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


@router.callback_query(F.data.startswith('ch_yes:') | F.data.startswith('ch_no:'))
async def confirm_channel_import(callback: CallbackQuery, conn):
    from queue_bot.services.channel_import import get_state, set_state, format_no_replacements, approval_keyboard
    if callback.from_user.id != config.OWNER_ID or not admins.is_admin(conn, callback.from_user.id):
        await callback.answer('Тільки для власника бота.', show_alert=True)
        return
    action, post_id = callback.data.split(':', 1)
    key = f'channel_draft:{config.REPLACEMENTS_CHANNEL}:{post_id}'
    draft = get_state(conn, key)
    if not draft or (draft['status'] != 'pending' and not draft.get('edit')):
        await callback.answer('Результат уже оброблено або застарів.', show_alert=True)
        return
    if action == 'ch_no':
        if draft['status'] != 'confirmed':
            draft['status'] = 'rejected'
        draft.pop('edit', None)
        set_state(conn, key, draft)
        await callback.answer('Відхилено')
        await callback.message.edit_text('❌ Результат із каналу відхилено. Розклад не змінено.')
        return
    was_confirmed = draft['status'] == 'confirmed'
    previous_date = draft['date']
    edited = draft.pop('edit', None)
    if edited:
        draft.update(edited)
    try:
        replacements.validate_publication_date(draft.get('date'))
    except BotError as err:
        await callback.answer(str(err), show_alert=True)
        return
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
    """Channel drafts reuse the button editor of photo recognitions (phone-friendly, no separators)."""
    import copy
    from queue_bot.services.channel_import import get_state
    await state.clear()
    if callback.from_user.id != config.OWNER_ID or not admins.is_admin(conn, callback.from_user.id):
        await callback.answer('Тільки для власника бота.', show_alert=True)
        return
    post_id = callback.data.split(':', 1)[1]
    draft = get_state(conn, f'channel_draft:{config.REPLACEMENTS_CHANNEL}:{post_id}')
    if not draft:
        await callback.answer('Дані не знайдено.', show_alert=True)
        return
    token = f'ch{post_id}'
    _PENDING_RECOGNITIONS[token] = copy.deepcopy((draft.get('edit') or draft)['replacements'])
    await callback.answer()
    text, kb = render_corrections_menu(token, _PENDING_RECOGNITIONS[token])
    await callback.message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith('ch_sv:'))
async def save_channel_edit(callback: CallbackQuery, conn):
    from queue_bot.services.channel_import import get_state, set_state, approval_keyboard, format_no_replacements
    if callback.from_user.id != config.OWNER_ID or not admins.is_admin(conn, callback.from_user.id):
        await callback.answer('Тільки для власника бота.', show_alert=True)
        return
    post_id = callback.data.split(':', 1)[1]
    key = f'channel_draft:{config.REPLACEMENTS_CHANNEL}:{post_id}'
    draft = get_state(conn, key)
    reps = _PENDING_RECOGNITIONS.get(f'ch{post_id}')
    if not draft or reps is None:
        await callback.answer('Дані застаріли. Натисніть «✏️ Редагувати» ще раз.', show_alert=True)
        return
    # shortcut: one post is one day; the first pair's date wins if pairs were given different dates.
    day = reps[0]['date'] if reps else (draft.get('edit') or draft)['date']
    try:
        replacements.validate_publication_date(day)
    except BotError as err:
        await callback.answer(str(err), show_alert=True)
        return
    for item in reps:
        item.update(date=day, group_name=config.STUDENT_GROUP)
    draft['edit'] = dict(date=day, replacements=reps)
    set_state(conn, key, draft)
    _PENDING_RECOGNITIONS.pop(f'ch{post_id}', None)
    text = ('\n\n'.join(replacements.format_replacement(r) for r in reps) if reps else format_no_replacements(day))
    await callback.answer()
    await callback.message.edit_text('✏️ <b>Перевірте виправлення перед збереженням:</b>\n\n' + text,
                                     reply_markup=approval_keyboard(post_id))
