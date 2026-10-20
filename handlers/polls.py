import html
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Poll,
    PollAnswer,
    ReplyKeyboardMarkup,
)

import config
from handlers.common import (
    get_admin_polls_menu,
    get_admin_reply_menu,
    get_reply_menu,
    make_reply_keyboard,
)
from services import admins, polls, users
from services.errors import BotError, PermissionDenied

log = logging.getLogger(__name__)
router = Router()
UK_TZ = ZoneInfo(config.TIMEZONE)


class AdminPollState(StatesGroup):
    in_polls_menu = State()
    entering_question = State()
    entering_options = State()
    configuring_settings = State()
    selecting_timer = State()
    confirming_poll = State()
    entering_group_id = State()


def _require_admin_msg(conn, user_id: int):
    if not admins.is_admin(conn, user_id):
        raise PermissionDenied("У вас немає прав адміністратора.")


# ---------- Обробка подій Telegram Poll та PollAnswer ----------

@router.poll_answer()
async def on_telegram_poll_answer(poll_answer: PollAnswer, conn):
    """Спрацьовує, коли користувач голосує (або змінює голос) у неанонімному опитуванні."""
    voter = poll_answer.user
    if not voter:
        return
    full_name = " ".join(filter(None, [voter.last_name, voter.first_name])).strip() or voter.full_name or ""
    option_ids = list(poll_answer.option_ids or [])
    polls.record_poll_answer(
        conn,
        tg_poll_id=poll_answer.poll_id,
        voter_telegram_id=voter.id,
        voter_name=full_name,
        option_ids=option_ids,
    )


@router.poll()
async def on_telegram_poll_update(poll: Poll, conn):
    """Спрацьовує при оновленні стану опитування (включно з анонімними голосами)."""
    counts = [o.voter_count for o in (poll.options or [])]
    polls.record_poll_state_update(conn, tg_poll_id=poll.id, option_voter_counts=counts)


# ---------- Прив'язка групового чату ----------

@router.message(Command("link_group"), StateFilter("*"))
async def cmd_link_group(message: Message, conn):
    """Команда /link_group у груповому чаті автоматично прив'язує його для публікації опитувань."""
    if not admins.is_admin(conn, message.from_user.id):
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer(
            "ℹ️ Цю команду потрібно надіслати безпосередньо в груповому чаті групи ПІ-231, куди додано бота."
        )
        return
    polls.set_group_chat_id(conn, message.chat.id)
    admins.log_action(conn, message.from_user.id, "link_group_chat", str(message.chat.id))
    await message.answer(
        f"✅ <b>Груповий чат успішно прив'язано!</b>\n"
        f"ID чату: <code>{message.chat.id}</code>\n"
        f"Тепер адміністратори можуть публікувати опитування сюди."
    )


# ---------- Студентський розділ «🗳 Опитування» ----------

@router.message(F.text.in_({"🗳 Опитування", "📊 Опитування"}), StateFilter("*"))
async def handle_polls_button(message: Message, state: FSMContext, conn):
    """Якщо користувач у стані адмін-панелі — відкриває адмінське меню опитувань,
    інакше — студентський перегляд активних та завершених опитувань."""
    current_state = await state.get_state()
    if current_state and current_state.startswith("Admin") and admins.is_admin(conn, message.from_user.id):
        await open_admin_polls_hub(message, state, conn)
        return

    await state.clear()
    active_list = polls.list_polls(conn, status="active", limit=5)
    closed_list = polls.list_polls(conn, status="closed", limit=5)

    if not active_list and not closed_list:
        kb = get_reply_menu(conn, message.from_user.id)
        await message.answer(
            "🗳 <b>Опитування групи ПІ-231</b>\n\n"
            "Наразі активних або завершених опитувань немає.",
            reply_markup=kb,
        )
        return

    lines = ["🗳 <b>Опитування групи ПІ-231</b>\n"]
    inline_rows = []

    if active_list:
        lines.append(f"🟢 <b>Активні голосування ({len(active_list)}):</b>")
        for p in active_list:
            res = polls.get_poll_results(conn, p["id"])
            lines.append(
                f"• <b>#{p['id']} {html.escape(p['question'])}</b> "
                f"(проголосувало: <b>{res['total_voters']}</b>)"
            )
            inline_rows.append([
                InlineKeyboardButton(
                    text=f"📊 Результати #{p['id']}",
                    callback_data=f"st_poll_res:{p['id']}",
                ),
                InlineKeyboardButton(
                    text=f"🗳 Бюлетень #{p['id']}",
                    callback_data=f"st_poll_get:{p['id']}",
                ),
            ])
        lines.append("")

    if closed_list:
        lines.append("🏁 <b>Нещодавно завершені:</b>")
        for p in closed_list[:3]:
            res = polls.get_poll_results(conn, p["id"])
            lines.append(
                f"• #{p['id']} {html.escape(p['question'])} "
                f"(голосів: <b>{res['total_voters']}</b>)"
            )
            inline_rows.append([
                InlineKeyboardButton(
                    text=f"🏁 Підсумки #{p['id']}: {p['question'][:25]}...",
                    callback_data=f"st_poll_res:{p['id']}",
                )
            ])

    lines.append("\nОберіть опитування на кнопках нижче для перегляду деталей 👇")
    markup = InlineKeyboardMarkup(inline_keyboard=inline_rows) if inline_rows else None
    await message.answer("\n".join(lines), reply_markup=markup)


@router.callback_query(F.data.startswith("st_poll_res:"))
async def student_view_poll_results(callback: CallbackQuery, conn):
    poll_id = int(callback.data.split(":")[1])
    text = polls.format_poll_results(conn, poll_id)
    poll = polls.get_poll(conn, poll_id)
    kb_rows = [
        [InlineKeyboardButton(text="🔄 Оновити дані", callback_data=f"st_poll_res:{poll_id}")]
    ]
    if poll and poll["status"] == "active":
        kb_rows.append([
            InlineKeyboardButton(text="🗳 Надіслати мені бюлетень", callback_data=f"st_poll_get:{poll_id}")
        ])
    await callback.answer()
    try:
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.callback_query(F.data.startswith("st_poll_get:"))
async def student_request_poll_ballot(callback: CallbackQuery, conn):
    poll_id = int(callback.data.split(":")[1])
    poll = polls.get_poll(conn, poll_id)
    if not poll or poll["status"] != "active":
        await callback.answer("Це опитування вже завершено.", show_alert=True)
        return
    ok = await polls.send_poll_to_single_user(callback.bot, conn, poll_id, callback.from_user.id)
    if ok:
        await callback.answer("Бюлетень надіслано нижче 👇")
    else:
        await callback.answer("Не вдалося надіслати опитування.", show_alert=True)


# ---------- Адмін-панель: Керування опитуваннями ----------

async def open_admin_polls_hub(message: Message, state: FSMContext, conn):
    _require_admin_msg(conn, message.from_user.id)
    await state.clear()
    await state.set_state(AdminPollState.in_polls_menu)

    active_cnt = len(polls.list_polls(conn, status="active", limit=50))
    closed_cnt = len(polls.list_polls(conn, status="closed", limit=50))
    grp_id = polls.get_group_chat_id(conn)
    grp_status = f"<code>{grp_id}</code>" if grp_id else "не прив'язано"

    await message.answer(
        "🗳 <b>Керування опитуваннями та голосуваннями</b>\n\n"
        f"• 🟢 Активних опитувань: <b>{active_cnt}</b>\n"
        f"• 🏁 Завершених в архіві: <b>{closed_cnt}</b>\n"
        f"• 👥 Груповий чат: {grp_status}\n\n"
        "Оберіть дію на клавіатурі внизу 👇",
        reply_markup=get_admin_polls_menu(),
    )


@router.message(F.text == "➕ Створити опитування", StateFilter("*"))
async def admin_start_create_poll(message: Message, state: FSMContext, conn):
    _require_admin_msg(conn, message.from_user.id)
    await state.clear()
    await state.set_state(AdminPollState.entering_question)
    await state.update_data(
        options=[],
        is_anonymous=True,
        allows_multiple=False,
        target="private",
        timer_minutes=None,
    )
    await message.answer(
        "📝 <b>Крок 1 з 4: Запитання опитування</b>\n\n"
        "Введіть текст запитання для студентів (до 300 символів):\n"
        "<i>Наприклад: «Чи зручно провести консультацію з КПЗ у четвер о 16:00?»</i>",
        reply_markup=make_reply_keyboard([["🔙 Скасувати створення"]]),
    )


@router.message(F.text == "🔙 Скасувати створення", StateFilter("*"))
async def admin_cancel_poll_wizard(message: Message, state: FSMContext, conn):
    _require_admin_msg(conn, message.from_user.id)
    await open_admin_polls_hub(message, state, conn)


@router.message(AdminPollState.entering_question, F.text)
async def admin_receive_poll_question(message: Message, state: FSMContext, conn):
    _require_admin_msg(conn, message.from_user.id)
    if message.text in {"🔙 Скасувати створення", "🔙 Назад", "↩️ Скасувати"}:
        await open_admin_polls_hub(message, state, conn)
        return

    q_text = message.text.strip()
    if len(q_text) < 3:
        await message.answer("⚠️ Запитання занадто коротке. Введіть змістовне запитання:")
        return
    if len(q_text) > 300:
        await message.answer("⚠️ Запитання перевищує 300 символів. Скоротіть текст:")
        return

    await state.update_data(question=q_text, options=[])
    await state.set_state(AdminPollState.entering_options)
    await message.answer(
        f"✅ Запитання збережено:\n<b>«{html.escape(q_text)}»</b>\n\n"
        "🔢 <b>Крок 2 з 4: Варіанти відповідей (від 2 до 10)</b>\n\n"
        "Надішліть варіанти відповідей:\n"
        "• Можна надіслати <b>всі одразу</b> (кожен варіант з нового рядка);\n"
        "• Або надсилати <b>по одному</b> повідомленню.",
        reply_markup=make_reply_keyboard([
            ["⚡ Шаблон: Так / Ні / Утримаюсь"],
            ["🔙 Скасувати створення"],
        ]),
    )


def _get_options_step_keyboard(options_count: int) -> ReplyKeyboardMarkup:
    rows = []
    if options_count >= 2:
        rows.append(["✅ Далі до налаштувань"])
    if options_count == 0:
        rows.append(["⚡ Шаблон: Так / Ні / Утримаюсь"])
    if options_count > 0:
        rows.append(["🗑 Очистити варіанти"])
    rows.append(["🔙 Скасувати створення"])
    return make_reply_keyboard(rows)


@router.message(AdminPollState.entering_options, F.text)
async def admin_receive_poll_options(message: Message, state: FSMContext, conn):
    _require_admin_msg(conn, message.from_user.id)
    txt = message.text.strip()

    if txt in {"🔙 Скасувати створення", "↩️ Скасувати"}:
        await open_admin_polls_hub(message, state, conn)
        return

    data = await state.get_data()
    current_options: list[str] = list(data.get("options", []))

    if txt == "🗑 Очистити варіанти":
        await state.update_data(options=[])
        await message.answer(
            "🗑 Список варіантів очищено. Надішліть нові варіанти (кожен з нового рядка або по одному):",
            reply_markup=_get_options_step_keyboard(0),
        )
        return

    if txt == "✅ Далі до налаштувань":
        if len(current_options) < 2:
            await message.answer("⚠️ Додайте щонайменше 2 варіанти відповіді!")
            return
        await state.set_state(AdminPollState.configuring_settings)
        await _render_poll_settings_step(message, state, conn)
        return

    if txt == "⚡ Шаблон: Так / Ні / Утримаюсь":
        new_lines = ["✅ Так", "❌ Ні", "🤔 Утримаюсь"]
    else:
        new_lines = [line.strip() for line in txt.split("\n") if line.strip()]

    for line in new_lines:
        if len(current_options) >= 10:
            await message.answer("⚠️ Досягнуто ліміту в 10 варіантів.")
            break
        if len(line) > 100:
            await message.answer(f"⚠️ Варіант «{html.escape(line[:30])}...» довший за 100 символів і пропущений.")
            continue
        current_options.append(line)

    await state.update_data(options=current_options)

    opts_formatted = "\n".join(f"<b>{i}.</b> {html.escape(o)}" for i, o in enumerate(current_options, 1))
    hint = (
        "\n\nНатисніть <b>«✅ Далі до налаштувань»</b> або надішліть ще варіанти 👇"
        if len(current_options) >= 2
        else "\n\nДодайте ще хоча б один варіант відповіді 👇"
    )
    await message.answer(
        f"📋 <b>Поточні варіанти ({len(current_options)}/10):</b>\n{opts_formatted}{hint}",
        reply_markup=_get_options_step_keyboard(len(current_options)),
    )


def _get_settings_keyboard(is_anon: bool, allows_mult: bool, target: str) -> ReplyKeyboardMarkup:
    anon_btn = "🕵️ Режим: Анонімне (змінити)" if is_anon else "👀 Режим: Публічне (змінити)"
    mult_btn = "🔢 Вибір: Кілька варіантів (змінити)" if allows_mult else "1️⃣ Вибір: Один варіант (змінити)"
    targ_btn = "📩 Куди: У приватні студентам (змінити)" if target == "private" else "👥 Куди: У груповий чат (змінити)"
    return make_reply_keyboard([
        [anon_btn],
        [mult_btn],
        [targ_btn],
        ["➡️ Далі: таймер закриття"],
        ["🔙 Скасувати створення"],
    ])


async def _render_poll_settings_step(message: Message, state: FSMContext, conn):
    data = await state.get_data()
    is_anon = bool(data.get("is_anonymous", True))
    allows_mult = bool(data.get("allows_multiple", False))
    target = data.get("target", "private")
    grp_id = polls.get_group_chat_id(conn)

    anon_desc = "🕵️ <b>Анонімне</b> (імена не показуються)" if is_anon else "👀 <b>Публічне</b> (видно, хто за що проголосував)"
    mult_desc = "🔢 <b>Кілька варіантів</b>" if allows_mult else "1️⃣ <b>Один варіант</b>"
    targ_desc = (
        "📩 <b>Кожному студенту в приватні</b>"
        if target == "private"
        else f"👥 <b>У груповий чат ПІ-231</b> ({'налаштовано' if grp_id else '⚠️ ID чату ще не задано!'})"
    )

    await message.answer(
        "⚙️ <b>Крок 3 з 4: Налаштування опитування</b>\n\n"
        f"• Тип голосування: {anon_desc}\n"
        f"• Кількість відповідей: {mult_desc}\n"
        f"• Спосіб доставки: {targ_desc}\n\n"
        "Натискайте на кнопки нижче, щоб перемкнути параметри, або переходьте далі 👇",
        reply_markup=_get_settings_keyboard(is_anon, allows_mult, target),
    )


@router.message(AdminPollState.configuring_settings, F.text)
async def admin_handle_poll_settings(message: Message, state: FSMContext, conn):
    _require_admin_msg(conn, message.from_user.id)
    txt = message.text.strip()

    if txt == "🔙 Скасувати створення":
        await open_admin_polls_hub(message, state, conn)
        return

    data = await state.get_data()
    is_anon = bool(data.get("is_anonymous", True))
    allows_mult = bool(data.get("allows_multiple", False))
    target = data.get("target", "private")

    if txt.startswith("🕵️ Режим:") or txt.startswith("👀 Режим:"):
        is_anon = not is_anon
        await state.update_data(is_anonymous=is_anon)
        await _render_poll_settings_step(message, state, conn)
        return

    if txt.startswith("🔢 Вибір:") or txt.startswith("1️⃣ Вибір:"):
        allows_mult = not allows_mult
        await state.update_data(allows_multiple=allows_mult)
        await _render_poll_settings_step(message, state, conn)
        return

    if txt.startswith("📩 Куди:") or txt.startswith("👥 Куди:"):
        target = "group" if target == "private" else "private"
        await state.update_data(target=target)
        await _render_poll_settings_step(message, state, conn)
        return

    if txt == "➡️ Далі: таймер закриття":
        if target == "group" and not polls.get_group_chat_id(conn):
            await message.answer(
                "⚠️ <b>Увага:</b> ID групового чату ще не прив'язано!\n"
                "Оберіть доставку «📩 У приватні студентам» або спочатку прив'яжіть групу в меню «🔗 Груповий чат ПІ-231»."
            )
            return
        await state.set_state(AdminPollState.selecting_timer)
        await message.answer(
            "⏱ <b>Крок 4 з 4: Таймер автоматичного закриття</b>\n\n"
            "Через скільки часу опитування має автоматично закритися і надіслати фінальні результати?\n"
            "Оберіть готовий варіант на кнопках або введіть час у хвилинах (наприклад: <code>45</code>):",
            reply_markup=make_reply_keyboard([
                ["⏱ 30 хвилин", "⏱ 1 година"],
                ["⏱ 3 години", "⏱ 6 годин"],
                ["⏱ 12 годин", "⏱ 24 години"],
                ["♾ Без таймера (закрити вручну)"],
                ["🔙 Скасувати створення"],
            ]),
        )
        return

    await _render_poll_settings_step(message, state, conn)


TIMER_PRESETS = {
    "⏱ 30 хвилин": 30,
    "⏱ 1 година": 60,
    "⏱ 3 години": 180,
    "⏱ 6 годин": 360,
    "⏱ 12 годин": 720,
    "⏱ 24 години": 1440,
    "♾ Без таймера (закрити вручну)": None,
}


@router.message(AdminPollState.selecting_timer, F.text)
async def admin_receive_poll_timer(message: Message, state: FSMContext, conn):
    _require_admin_msg(conn, message.from_user.id)
    txt = message.text.strip()

    if txt == "🔙 Скасувати створення":
        await open_admin_polls_hub(message, state, conn)
        return

    minutes: int | None = None
    if txt in TIMER_PRESETS:
        minutes = TIMER_PRESETS[txt]
    elif txt.isdigit() and 1 <= int(txt) <= 10080:
        minutes = int(txt)
    else:
        await message.answer(
            "⚠️ Оберіть варіант таймера на кнопках або введіть число хвилин (від 1 до 10080):"
        )
        return

    await state.update_data(timer_minutes=minutes)
    await state.set_state(AdminPollState.confirming_poll)

    data = await state.get_data()
    q_text = data["question"]
    options = data["options"]
    is_anon = bool(data.get("is_anonymous", True))
    allows_mult = bool(data.get("allows_multiple", False))
    target = data.get("target", "private")

    anon_str = "🕵️ Анонімне" if is_anon else "👀 Публічне (видно імена)"
    mult_str = "🔢 Кілька варіантів" if allows_mult else "1️⃣ Один варіант"
    targ_str = "📩 У приватні всім студентам" if target == "private" else "👥 У груповий чат ПІ-231"
    if minutes:
        closes_dt = datetime.now(UK_TZ) + timedelta(minutes=minutes)
        timer_str = f"⏱ Через {minutes} хв (до {closes_dt.strftime('%d.%m %H:%M')})"
    else:
        timer_str = "♾ Без обмеження часу (ручне закриття)"

    opts_str = "\n".join(f"  {i}. {html.escape(o)}" for i, o in enumerate(options, 1))

    await message.answer(
        "👀 <b>Попередній перегляд опитування</b>\n\n"
        f"❓ <b>{html.escape(q_text)}</b>\n\n"
        f"<b>Варіанти відповідей:</b>\n{opts_str}\n\n"
        f"• Режим: <b>{anon_str}</b>\n"
        f"• Вибір: <b>{mult_str}</b>\n"
        f"• Куди: <b>{targ_str}</b>\n"
        f"• Таймер: <b>{timer_str}</b>\n\n"
        "Опублікувати та розіслати опитування зараз?",
        reply_markup=make_reply_keyboard([
            ["🚀 Опублікувати опитування"],
            ["🔙 Скасувати створення"],
        ]),
    )


@router.message(AdminPollState.confirming_poll, F.text)
async def admin_confirm_and_publish_poll(message: Message, state: FSMContext, conn):
    _require_admin_msg(conn, message.from_user.id)
    if message.text == "🔙 Скасувати створення":
        await open_admin_polls_hub(message, state, conn)
        return
    if message.text != "🚀 Опублікувати опитування":
        await message.answer("Натисніть «🚀 Опублікувати опитування» або «🔙 Скасувати створення» 👇")
        return

    data = await state.get_data()
    minutes = data.get("timer_minutes")
    closes_at = None
    if minutes:
        closes_at = (datetime.now(UK_TZ) + timedelta(minutes=minutes)).isoformat()

    poll_id = polls.create_poll(
        conn,
        actor_id=message.from_user.id,
        question=data["question"],
        options=data["options"],
        is_anonymous=bool(data.get("is_anonymous", True)),
        allows_multiple=bool(data.get("allows_multiple", False)),
        target=data.get("target", "private"),
        closes_at=closes_at,
    )

    await message.answer("⏳ Публікую та надсилаю опитування...")
    try:
        succ, err = await polls.publish_poll(message.bot, conn, poll_id)
    except BotError as e:
        await state.clear()
        await message.answer(f"⚠️ {e}", reply_markup=get_admin_polls_menu())
        return

    await state.clear()
    await state.set_state(AdminPollState.in_polls_menu)
    await message.answer(
        f"✅ <b>Опитування #{poll_id} успішно запущено!</b>\n\n"
        f"• Доставлено: <b>{succ}</b> (помилок: {err})\n"
        "Ви можете стежити за голосуванням у реальному часі в розділі «📡 Активні опитування».",
        reply_markup=get_admin_polls_menu(),
    )


# ---------- Перегляд активних опитувань та керування ними ----------

def _admin_poll_control_kb(poll_id: int, is_active: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔄 Оновити результати (Live)", callback_data=f"adm_poll_ref:{poll_id}")]
    ]
    if is_active:
        rows.append([
            InlineKeyboardButton(
                text="🛑 Завершити достроково та підбити підсумки",
                callback_data=f"adm_poll_stop:{poll_id}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == "📡 Активні опитування", StateFilter("*"))
async def admin_view_active_polls(message: Message, state: FSMContext, conn):
    _require_admin_msg(conn, message.from_user.id)
    await polls.check_expired_polls(message.bot, conn)
    active = polls.list_polls(conn, status="active", limit=10)
    if not active:
        await message.answer(
            "📡 Зараз немає активних опитувань.",
            reply_markup=get_admin_polls_menu(),
        )
        return

    for p in active:
        text = polls.format_poll_results(conn, p["id"])
        await message.answer(text, reply_markup=_admin_poll_control_kb(p["id"], True))


@router.callback_query(F.data.startswith("adm_poll_ref:"))
async def admin_refresh_poll_live(callback: CallbackQuery, conn):
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("Тільки для адміністраторів.", show_alert=True)
        return
    poll_id = int(callback.data.split(":")[1])
    poll = polls.get_poll(conn, poll_id)
    if not poll:
        await callback.answer("Опитування не знайдено.", show_alert=True)
        return
    text = polls.format_poll_results(conn, poll_id)
    is_active = poll["status"] == "active"
    await callback.answer("Дані оновлено!")
    try:
        await callback.message.edit_text(text, reply_markup=_admin_poll_control_kb(poll_id, is_active))
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm_poll_stop:"))
async def admin_stop_poll_early(callback: CallbackQuery, conn):
    if not admins.is_admin(conn, callback.from_user.id):
        await callback.answer("Тільки для адміністраторів.", show_alert=True)
        return
    poll_id = int(callback.data.split(":")[1])
    poll = polls.get_poll(conn, poll_id)
    if not poll:
        await callback.answer("Опитування не знайдено.", show_alert=True)
        return
    if poll["status"] == "closed":
        await callback.answer("Опитування вже завершено.", show_alert=True)
        return

    await callback.answer("Завершую опитування та надсилаю підсумки...")
    summary = await polls.close_poll_and_summarize(callback.bot, conn, poll_id)
    admins.log_action(conn, callback.from_user.id, "close_poll", str(poll_id))
    try:
        await callback.message.edit_text(
            summary + "\n\n✅ <i>Опитування завершено адміністратором, підсумки розіслано!</i>",
            reply_markup=_admin_poll_control_kb(poll_id, False),
        )
    except Exception:
        await callback.message.answer(summary)


@router.message(F.text == "📦 Історія опитувань", StateFilter("*"))
async def admin_polls_history(message: Message, conn):
    _require_admin_msg(conn, message.from_user.id)
    closed = polls.list_polls(conn, status="closed", limit=10)
    if not closed:
        await message.answer(
            "📦 Історія завершених опитувань порожня.",
            reply_markup=get_admin_polls_menu(),
        )
        return

    lines = ["📦 <b>Останні завершені опитування:</b>\n"]
    kb_rows = []
    for p in closed:
        res = polls.get_poll_results(conn, p["id"])
        lines.append(
            f"• <b>#{p['id']} {html.escape(p['question'])}</b> — "
            f"проголосувало: <b>{res['total_voters']}</b>"
        )
        kb_rows.append([
            InlineKeyboardButton(
                text=f"📊 Деталі опитування #{p['id']}",
                callback_data=f"adm_poll_ref:{p['id']}",
            )
        ])

    await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )


# ---------- Налаштування групового чату ----------

@router.message(F.text == "🔗 Груповий чат ПІ-231", StateFilter("*"))
async def admin_group_chat_settings(message: Message, state: FSMContext, conn):
    _require_admin_msg(conn, message.from_user.id)
    grp_id = polls.get_group_chat_id(conn)
    status = f"<code>{grp_id}</code>" if grp_id else "<b>не налаштовано</b>"
    await state.set_state(AdminPollState.entering_group_id)
    await message.answer(
        "🔗 <b>Налаштування групового чату ПІ-231</b>\n\n"
        f"Поточний ID групового чату: {status}\n\n"
        "<b>Як прив'язати групу:</b>\n"
        "1. Додайте бота до чату групи ПІ-231 та напишіть там команду <code>/link_group</code>.\n"
        "2. Або введіть числовий ID групового чату (зазвичай починається з <code>-100...</code>) прямо сюди:",
        reply_markup=make_reply_keyboard([["🔙 Назад до опитувань"]]),
    )


@router.message(AdminPollState.entering_group_id, F.text)
async def admin_save_group_chat_id(message: Message, state: FSMContext, conn):
    _require_admin_msg(conn, message.from_user.id)
    txt = message.text.strip()
    if txt in {"🔙 Назад до опитувань", "🔙 Назад", "🔙 В адмін-панель"}:
        await open_admin_polls_hub(message, state, conn)
        return

    if not re.match(r"^-?\d+$", txt):
        await message.answer("⚠️ ID чату має бути числом (наприклад: <code>-1001234567890</code>). Спробуйте ще раз:")
        return

    chat_id = int(txt)
    polls.set_group_chat_id(conn, chat_id)
    admins.log_action(conn, message.from_user.id, "set_group_chat_id", str(chat_id))
    await message.answer(f"✅ ID групового чату <code>{chat_id}</code> збережено!")
    await open_admin_polls_hub(message, state, conn)
