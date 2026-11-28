"""Add laboratory titles and multiple works to a selected subject."""
import html

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.state import State, StatesGroup

from queue_bot.handlers.common import make_reply_keyboard
from queue_bot.services import lab_catalog, lab_definitions
from queue_bot.services.subjects import find_subject
from queue_bot.services.errors import BotError

router = Router(name='admin_labs')


class AdminLabs(StatesGroup):
    subject = State()
    viewing = State()
    batch = State()
    confirm = State()


@router.message(StateFilter('*'), F.text == '🧪 Лабораторні')
@router.message(StateFilter(AdminLabs), F.text == '🔙 Обрати інший предмет')
async def choose_subject(message, state, subjects):
    await state.clear()
    await state.set_state(AdminLabs.subject)
    rows = [[f'🧪 {subject["name"]}'] for subject in subjects]
    await message.answer('Оберіть предмет для керування лабораторними:',
        reply_markup=make_reply_keyboard(rows+[['🔙 До предметів']]))


async def current_subject(state, subjects):
    subject = find_subject(subjects, (await state.get_data()).get('labs_subject_id'))
    if not subject:
        raise BotError('Предмет не знайдено. Оберіть його ще раз через «🧪 Лабораторні».')
    return subject


async def show_subject(message, state, conn, subjects, note=''):
    subject = await current_subject(state, subjects)
    await state.set_state(AdminLabs.viewing)
    labs = lab_catalog.labs_for_session({'subject_id':subject['id']}, conn=conn)
    lines = [note, f'🧪 <b>{html.escape(subject["name"])}</b>', '']
    lines += [f'№{lab["number"]} — {html.escape(lab.get("title") or "Назву ще не вказано")} · до {lab["max_minutes"]} хв' for lab in labs]
    if not labs:
        lines.append('Лабораторних ще немає. Додайте одну роботу або список.')
    await message.answer('\n'.join(lines).strip(), reply_markup=make_reply_keyboard([
        ['➕ Додати / змінити лабораторні'], ['🔙 Обрати інший предмет', '🔙 До предметів']]))


@router.message(AdminLabs.subject, F.text.startswith('🧪 '))
async def subject_selected(message, state, conn, subjects):
    name = message.text.removeprefix('🧪 ').strip()
    subject = next((s for s in subjects if s['name'] == name), None)
    if not subject:
        raise BotError('Оберіть предмет на кнопках.')
    await state.update_data(labs_subject_id=subject['id'])
    await show_subject(message, state, conn, subjects)


@router.message(StateFilter(AdminLabs), F.text == '🔙 До лабораторних')
async def back(message, state, conn, subjects):
    await state.update_data(labs_batch=None)
    await show_subject(message, state, conn, subjects)


@router.message(StateFilter(AdminLabs.viewing, AdminLabs.confirm), F.text.in_({'➕ Додати / змінити лабораторні', '✏️ Виправити список'}))
async def enter_batch(message, state, subjects):
    subject = await current_subject(state, subjects)
    await state.set_state(AdminLabs.batch)
    await message.answer(
        f'Лабораторні для <b>{html.escape(subject["name"])}</b>\n\n'
        'Надішліть одну або кілька робіт, кожну з нового рядка:\n'
        '<pre>1. Тема першої лабораторної\n2. Тема другої лабораторної</pre>\n'
        'За бажанням додайте ліміт часу в хвилинах:\n'
        '<pre>3 | Назва лабораторної | 30</pre>\n'
        'Для наявного номера оновиться назва, а час збережеться, якщо його не вказувати. '
        'Новій роботі без указаного часу буде задано 20 хв. До 20 робіт на предмет. '
        'Перед збереженням покажу список для перевірки.',
        reply_markup=make_reply_keyboard([['🔙 До лабораторних']]))


@router.message(AdminLabs.batch, F.text)
async def preview_batch(message, state, conn, subjects):
    subject = await current_subject(state, subjects)
    batch = lab_definitions.parse_batch(message.text)
    labs = lab_definitions.prepare(conn, subject['id'], batch)
    await state.update_data(labs_batch=message.text)
    await state.set_state(AdminLabs.confirm)
    lines = [f'Зберегти для <b>{html.escape(subject["name"])}</b>?', '']
    lines += [f'№{lab["number"]} — {html.escape(lab["title"])} · до {lab["max_minutes"]} хв' for lab in labs]
    lines += ['', 'Інші лабораторні цього предмета залишаться у списку.']
    await message.answer('\n'.join(lines), reply_markup=make_reply_keyboard([
        ['✅ Зберегти лабораторні'], ['✏️ Виправити список', '🔙 До лабораторних']]))


@router.message(AdminLabs.confirm, F.text == '✅ Зберегти лабораторні')
async def confirm_batch(message, state, conn, subjects):
    subject = await current_subject(state, subjects)
    text = (await state.get_data()).get('labs_batch')
    if not text:
        raise BotError('Список застарів. Додайте лабораторні ще раз.')
    labs = lab_definitions.save_batch(conn, message.from_user.id, subjects, subject['id'], text)
    await state.update_data(labs_batch=None)
    await show_subject(message, state, conn, subjects, f'✅ Збережено лабораторних: {len(labs)}.\n')
