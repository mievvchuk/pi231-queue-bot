"""Inline laboratory multiselect shared by signup and completion."""
import html
import secrets

from queue_bot.handlers.common import make_keyboard, make_reply_keyboard
from queue_bot.services import lab_catalog


async def start_picker(message, state, session, *, prefix, allowed, selected):
    nonce = secrets.token_hex(6)
    await state.update_data(picker_nonce=nonce, picker_allowed=sorted(allowed), picker_selected=sorted(selected))
    data = await state.get_data()
    await message.answer('Оберіть лабораторні на кнопках нижче або введіть номери через кому.',
                         reply_markup=make_reply_keyboard([['🔙 Назад до черги']]))
    text, markup = picker_content(session, data, prefix)
    await message.answer(text, reply_markup=markup)


def picker_content(session, data, prefix):
    allowed = set(data['picker_allowed'])
    selected = data.get('picker_selected', [])
    nonce = data['picker_nonce']
    completing = prefix == 'labdone'
    heading = '✅ Які лабораторні здали?' if completing else '🧪 Оберіть лабораторні для здачі'
    lines = [f'<b>{heading}</b>', '']
    buttons = []
    for lab in lab_catalog.labs_for_session(session):
        number = lab['number']
        if number not in allowed:
            continue
        title = f" — {html.escape(lab['title'])}" if lab.get('title') else ''
        lines.append(f"№{number}{title} · до {lab['max_minutes']} хв")
        mark = '☑' if number in selected else '☐'
        buttons.append((f"{mark} №{number} · {lab['max_minutes']} хв", f'{prefix}:{nonce}:t:{number}'))
    lines.extend(['', 'Обрано: ' + (lab_catalog.format_numbers(selected) if selected else 'ще нічого')])
    if selected:
        lines.append(f'Сумарний ліміт: <b>{lab_catalog.selection_limit(session, selected)} хв</b>')
    lines.append('Натискайте номери, потім підтвердьте. Або введіть, наприклад: 1, 2.')
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.extend([
        [('Обрати всі', f'{prefix}:{nonce}:all'), ('Скинути', f'{prefix}:{nonce}:clear')],
        [('✅ Підтвердити' if completing else '✅ Далі', f'{prefix}:{nonce}:ok')],
        [('🔙 До черги', f'{prefix}:{nonce}:back')],
    ])
    # Message.answer takes text positionally and markup by keyword.
    return '\n'.join(lines), make_keyboard(rows)


async def process_picker(callback, state, expected_state, prefix):
    data = await state.get_data()
    parts = (callback.data or '').split(':')
    if (await state.get_state() != expected_state or len(parts) < 3 or parts[0] != prefix or
            parts[1] != data.get('picker_nonce')):
        await callback.answer('Цей вибір уже неактуальний. Відкрийте дію ще раз.', show_alert=True)
        return None
    action = parts[2]
    selected = set(data.get('picker_selected', []))
    allowed = set(data['picker_allowed'])
    if action == 't' and len(parts) == 4 and parts[3].isdigit():
        number = int(parts[3])
        if number not in allowed:
            await callback.answer('Цієї лабораторної немає серед доступних.', show_alert=True)
            return None
        selected.symmetric_difference_update({number})
    elif action == 'all' and len(parts) == 3:
        selected = allowed
    elif action == 'clear' and len(parts) == 3:
        selected = set()
    elif action == 'back' and len(parts) == 3:
        return 'back', data
    elif action == 'ok' and len(parts) == 3:
        if not selected:
            await callback.answer('Оберіть хоча б одну лабораторну.', show_alert=True)
            return None
        return 'ok', data
    else:
        await callback.answer('Невідома дія.', show_alert=True)
        return None
    await state.update_data(picker_selected=sorted(selected))
    return 'render', await state.get_data()


async def picker_back(callback, state, conn, subjects):
    from queue_bot.handlers.student import open_queue_screen
    sid = (await state.get_data()).get('session_id')
    await state.clear()
    if sid:
        await state.update_data(session_id=sid)
        await open_queue_screen(callback.message, conn, subjects, sid, telegram_id=callback.from_user.id)
