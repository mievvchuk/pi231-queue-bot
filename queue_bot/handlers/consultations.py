"""Administrator controls for consultation attendance runs."""
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.state import State, StatesGroup

from queue_bot.handlers.common import make_reply_keyboard
from queue_bot.services import attendance, consultations
from queue_bot.services.admins import require_admin
from queue_bot.services.errors import BotError
from queue_bot.services.schedule import UK_TZ

router = Router(name='consultations')
router.message.filter(F.chat.type == 'private')
BACK = '🔙 Назад до черги'


class AdminConsultation(StatesGroup):
    method = State()
    time = State()
    duration = State()
    custom_duration = State()
    confirm = State()
    stop = State()


async def selected(message, state, conn):
    require_admin(conn, message.from_user.id)
    sid = (await state.get_data()).get('session_id')
    consultations._consultation(conn, sid)
    return sid


async def manager(message, state, conn, subjects, note=''):
    sid = await selected(message, state, conn)
    await state.clear()
    from queue_bot.handlers.admin.presentation import open_admin_queue_manager
    await open_admin_queue_manager(message, conn, subjects, sid, note=note, state=state)


@router.message(StateFilter(AdminConsultation), F.text == BACK)
async def back(message, state, conn, subjects):
    await manager(message, state, conn, subjects)


@router.message(StateFilter('*'), F.text.in_({'▶️ Запуск консультації', '✏️ Змінити запуск консультації'}))
async def choose_method(message, state, conn):
    await selected(message, state, conn)
    await state.set_state(AdminConsultation.method)
    await message.answer('Коли почати консультацію? Час указується за Києвом.', reply_markup=make_reply_keyboard([
        ['▶️ Почати зараз'], ['⏳ Через кілька хвилин', '🕒 Сьогодні о певній годині'],
        ['📅 Указати дату й час'], [BACK]]))


async def duration(message, state):
    await state.set_state(AdminConsultation.duration)
    await message.answer('Оберіть тривалість консультації:', reply_markup=make_reply_keyboard([
        ['30 хв', '60 хв', '90 хв', '120 хв'], ['✏️ Інша тривалість'], [BACK]]))


@router.message(AdminConsultation.method, F.text)
async def method_selected(message, state, conn):
    await selected(message, state, conn)
    if message.text == '▶️ Почати зараз':
        await state.update_data(starts_at=None)
        await duration(message, state)
        return
    choices = {
        '⏳ Через кілька хвилин': ('relative', 'Через скільки хвилин почати? Введіть число від 1 до 10080.'),
        '🕒 Сьогодні о певній годині': ('today', 'Введіть час сьогодні у форматі ГГ:ХХ, наприклад 18:30.'),
        '📅 Указати дату й час': ('date', 'Введіть дату й час: ДД.ММ.РРРР ГГ:ХХ, наприклад '
            + (datetime.now(UK_TZ) + timedelta(days=1)).strftime('%d.%m.%Y') + ' 18:30.'),
    }
    if message.text not in choices:
        raise BotError('Оберіть спосіб запуску на кнопках.')
    mode, prompt = choices[message.text]
    await state.update_data(time_mode=mode)
    await state.set_state(AdminConsultation.time)
    await message.answer(prompt + '\nЧас — за Києвом.', reply_markup=make_reply_keyboard([[BACK]]))


def parse_time(mode, text, now):
    local = datetime.fromtimestamp(now, UK_TZ)
    try:
        if mode == 'relative':
            delay = int(text)
            if not 1 <= delay <= 10080:
                raise ValueError
            return now + delay * 60
        if mode == 'today':
            parsed = datetime.strptime(text.strip(), '%H:%M').time()
            moment = datetime.combine(local.date(), parsed, UK_TZ)
        else:
            moment = datetime.strptime(text.strip(), '%d.%m.%Y %H:%M').replace(tzinfo=UK_TZ)
        result = int(moment.timestamp())
        if result <= now or datetime.fromtimestamp(result, UK_TZ).replace(tzinfo=None) != moment.replace(tzinfo=None):
            raise ValueError
        return result
    except (ValueError, OverflowError):
        raise BotError('Некоректний або минулий час. Укажіть майбутній початок у вибраному форматі.')


@router.message(AdminConsultation.time, F.text)
async def time_entered(message, state, conn):
    await selected(message, state, conn)
    form = await state.get_data()
    starts_at = parse_time(form['time_mode'], message.text, attendance.now_seconds())
    await state.update_data(starts_at=starts_at)
    await duration(message, state)


async def confirmation(message, state, minutes):
    if not 10 <= minutes <= 240:
        raise BotError('Тривалість консультації — від 10 до 240 хвилин.')
    await state.update_data(minutes=minutes)
    form = await state.get_data()
    when = datetime.fromtimestamp(form['starts_at'], UK_TZ).strftime('%d.%m.%Y о %H:%M') if form['starts_at'] else 'зараз'
    await state.set_state(AdminConsultation.confirm)
    await message.answer(
        f'Початок: {when} (Київ).\nТривалість: {minutes} хв.\n\n'
        'На початку студенти в цій черзі отримають запит присутності. Далі бот викликатиме готових за порядком черги.',
        reply_markup=make_reply_keyboard([['✅ Підтвердити запуск'], [BACK]]))


@router.message(AdminConsultation.duration, F.text)
async def duration_selected(message, state, conn):
    await selected(message, state, conn)
    if message.text == '✏️ Інша тривалість':
        await state.set_state(AdminConsultation.custom_duration)
        await message.answer('Скільки хвилин триватиме консультація? Від 10 до 240.', reply_markup=make_reply_keyboard([[BACK]]))
        return
    if message.text not in {'30 хв', '60 хв', '90 хв', '120 хв'}:
        raise BotError('Оберіть тривалість на кнопках.')
    await confirmation(message, state, int(message.text.split()[0]))


@router.message(AdminConsultation.custom_duration, F.text)
async def custom_duration(message, state, conn):
    await selected(message, state, conn)
    if not message.text.strip().isdigit():
        raise BotError('Введіть кількість хвилин числом від 10 до 240.')
    await confirmation(message, state, int(message.text))


@router.message(AdminConsultation.confirm, F.text == '✅ Підтвердити запуск')
async def confirm_start(message, state, conn, subjects):
    sid = await selected(message, state, conn)
    form = await state.get_data()
    consultations.configure(conn, message.from_user.id, sid, minutes=form['minutes'], starts_at=form['starts_at'])
    await manager(message, state, conn, subjects, '✅ Запуск консультації заплановано.' if form['starts_at'] else '✅ Консультацію розпочато.')


@router.message(StateFilter('*'), F.text == '❌ Скасувати запуск консультації')
async def cancel_plan(message, state, conn, subjects):
    sid = await selected(message, state, conn)
    consultations.cancel(conn, message.from_user.id, sid)
    await manager(message, state, conn, subjects, 'Запланований запуск скасовано.')


@router.message(StateFilter('*'), F.text == '⏹ Зупинити консультацію')
async def ask_stop(message, state, conn):
    await selected(message, state, conn)
    await state.set_state(AdminConsultation.stop)
    await message.answer('Зупинити виклики студентів? Місця та нездані роботи залишаться в черзі.',
        reply_markup=make_reply_keyboard([['⏹ Підтвердити зупинку'], [BACK]]))


@router.message(AdminConsultation.stop, F.text == '⏹ Підтвердити зупинку')
async def confirm_stop(message, state, conn, subjects):
    sid = await selected(message, state, conn)
    consultations.cancel(conn, message.from_user.id, sid, stop=True)
    await manager(message, state, conn, subjects, 'Консультацію зупинено. Черга збережена.')

