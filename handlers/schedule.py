import html
import io
import logging
import uuid

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from handlers.common import (
    get_reply_menu,
    get_schedule_reply_menu,
    make_keyboard,
    make_reply_keyboard,
)
from services import admins, recognition, replacements, schedule
from services.errors import BotError

log = logging.getLogger(__name__)
router = Router()

# Тимчасове збереження розпізнаних замін для підтвердження
_PENDING_RECOGNITIONS: dict[str, list[dict]] = {}


def get_schedule_data() -> dict:
    return schedule.load_schedule_data(config.SCHEDULE_PATH)


# ---------- Розклад: Сьогодні / Завтра / Тиждень ----------

@router.message(F.text == "📅 Розклад", StateFilter("*"))
async def open_schedule_menu(message: Message, state: FSMContext, conn):
    await state.clear()
    sch_data = get_schedule_data()
    today_dt = schedule.get_current_date()
    today_text = schedule.format_schedule_for_date(conn, sch_data, today_dt, config.STUDENT_GROUP)

    await message.answer(
        f"📅 <b>Розклад занять групи {html.escape(config.STUDENT_GROUP)}</b>\n\n"
        f"{today_text}\n\n"
        "Оберіть потрібний період або дію в меню нижче 👇",
        reply_markup=get_schedule_reply_menu(),
    )


@router.message(F.text == "📅 Сьогодні", StateFilter("*"))
@router.message(Command("today"), StateFilter("*"))
async def show_today_schedule(message: Message, conn):
    sch_data = get_schedule_data()
    today_dt = schedule.get_current_date()
    text = schedule.format_schedule_for_date(conn, sch_data, today_dt, config.STUDENT_GROUP)
    await message.answer(text)


@router.message(F.text == "➡️ Завтра", StateFilter("*"))
@router.message(Command("tomorrow"), StateFilter("*"))
async def show_tomorrow_schedule(message: Message, conn):
    sch_data = get_schedule_data()
    tomorrow_dt = schedule.get_tomorrow_date()
    text = schedule.format_schedule_for_date(conn, sch_data, tomorrow_dt, config.STUDENT_GROUP)
    await message.answer(text)


@router.message(F.text == "🗓 Весь тиждень", StateFilter("*"))
@router.message(Command("week"), StateFilter("*"))
async def show_week_schedule(message: Message, conn):
    sch_data = get_schedule_data()
    today_dt = schedule.get_current_date()
    days = schedule.format_week_schedule(conn, sch_data, today_dt, config.STUDENT_GROUP)
    for day_text in days:
        await message.answer(day_text)


@router.message(Command("schedule"), StateFilter("*"))
async def show_base_schedule(message: Message):
    sch_data = get_schedule_data()
    lines = [f"📌 <b>Базовий розклад групи {html.escape(config.STUDENT_GROUP)}:</b>\n"]
    for day_key in schedule.DAY_KEYS[:5]:  # Пн-Пт
        day_uk = schedule.DAY_NAMES_UK.get(day_key, day_key.capitalize())
        content = sch_data.get(day_key, [])
        lines.append(f"<b>{day_uk}:</b>")
        if isinstance(content, list):
            if not content:
                lines.append("<i>Занять немає</i>\n")
            else:
                for l in content:
                    lines.append(f"• {l.get('number', '?')}. {l.get('subject', '')} ({l.get('startTime', '')}–{l.get('endTime', '')})")
                lines.append("")
        elif isinstance(content, dict):
            reg = content.get("regular") or []
            num = content.get("numerator") or []
            den = content.get("denominator") or []
            if reg:
                for l in reg:
                    lines.append(f"• {l.get('number', '?')}. {l.get('subject', '')} ({l.get('startTime', '')}–{l.get('endTime', '')})")
            if num:
                lines.append("<i>(Чисельник)</i>")
                for l in num:
                    lines.append(f"• {l.get('number', '?')}. {l.get('subject', '')}")
            if den:
                lines.append("<i>(Знаменник)</i>")
                for l in den:
                    lines.append(f"• {l.get('number', '?')}. {l.get('subject', '')}")
            lines.append("")
    await message.answer("\n".join(lines))


# ---------- Заміни ----------

@router.message(F.text.in_({"🔄 Заміни", "🔄 Активні заміни"}), StateFilter("*"))
@router.message(Command("replacements"), StateFilter("*"))
async def show_replacements(message: Message, conn):
    items = replacements.get_replacements(conn, group_name=config.STUDENT_GROUP)
    if not items:
        await message.answer(
            f"🔄 <b>Активні заміни для групи {html.escape(config.STUDENT_GROUP)}:</b>\n\n"
            "Наразі немає збережених замін пар.\n"
            "<i>(Щоб додати заміну, надішліть фотографію таблиці замін)</i>"
        )
        return

    text_parts = [f"🔄 <b>Активні заміни для групи {html.escape(config.STUDENT_GROUP)}:</b>\n"]
    for it in items:
        text_parts.append(replacements.format_replacement(it))
        text_parts.append("────────────────")

    await message.answer("\n\n".join(text_parts))


@router.message(Command("clear_replacements"), StateFilter("*"))
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


# ---------- Обробка фотографій таблиць замін ----------

@router.message(F.photo)
async def handle_photo_replacement(message: Message, conn):
    photo = message.photo[-1]
    bot = message.bot
    status_msg = await message.answer("⏳ Отримано фото. Аналізую таблицю замін...")

    try:
        file = await bot.get_file(photo.file_id)
        img_stream = io.BytesIO()
        await bot.download_file(file.file_path, destination=img_stream)
        img_bytes = img_stream.getvalue()

        result = await recognition.recognize_replacements_from_image(img_bytes, group_name=config.STUDENT_GROUP)
        reps = result["replacements"]
        token = str(uuid.uuid4())[:8]
        _PENDING_RECOGNITIONS[token] = reps

        cards = [replacements.format_replacement(r) for r in reps]
        cards_text = "\n\n────────────────\n\n".join(cards)
        conf_pct = int(result["confidence"] * 100)

        preview_text = (
            f"🔍 <b>Розпізнано заміни для групи {html.escape(config.STUDENT_GROUP)}!</b>\n"
            f"Точність: ~{conf_pct}%\n\n"
            f"{cards_text}\n\n"
            "Зберегти ці заміни в розклад?"
        )

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Підтвердити заміни", callback_data=f"rec_cf:{token}"),
                    InlineKeyboardButton(text="❌ Відхилити", callback_data=f"rec_rj:{token}"),
                ]
            ]
        )
        await status_msg.edit_text(preview_text, reply_markup=markup)

    except BotError as err:
        await status_msg.edit_text(f"⚠️ {html.escape(str(err))}")
    except Exception as e:
        log.exception(f"Error handling replacement photo: {e}")
        await status_msg.edit_text(f"⚠️ <b>Помилка розпізнавання:</b> {html.escape(str(e))}")


@router.callback_query(F.data.startswith("rec_rj:"))
async def reject_recognition(callback: CallbackQuery):
    token = callback.data.split(":", 1)[1]
    _PENDING_RECOGNITIONS.pop(token, None)
    await callback.answer("Відхилено.")
    await callback.message.edit_text("❌ Заміни не збережено.")


@router.callback_query(F.data.startswith("rec_cf:"))
async def confirm_recognition(callback: CallbackQuery, conn):
    token = callback.data.split(":", 1)[1]
    reps = _PENDING_RECOGNITIONS.pop(token, None)
    if not reps:
        await callback.answer("Дані вже збережені або застаріли.", show_alert=True)
        return

    saved_count = 0
    for r in reps:
        if replacements.save_replacement(conn, r):
            saved_count += 1

    admins.log_action(conn, callback.from_user.id, "save_replacements", f"count={saved_count}")
    await callback.answer("Заміни збережено!")
    await callback.message.edit_text(
        f"✅ <b>Успішно збережено замін: {saved_count}!</b>\n"
        "Вони вже враховані в розкладі занять."
    )
