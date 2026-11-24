"""Admin panels handlers."""
from .common import (
    AdminPanelState,
    F,
    FSMContext,
    Message,
    StateFilter,
    admins,
    get_admin_reply_menu,
    queues,
)


async def admin_quick_manage_from_student_view(message: Message, state: FSMContext, conn, subjects):
    from .presentation import open_admin_queue_manager
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        await admin_panel_open(message, state, conn=conn)
        return
    await state.clear()
    await state.update_data(session_id=session_id)
    await open_admin_queue_manager(
        message,
        conn,
        subjects,
        session_id,
        note="⚡ <b>Режим старости/адміна:</b> тут можна додати студентів за списком, змінити порядок або позначити здані роботи. Здача працює автоматично за розкладом.", state=state,
    )



async def admin_switch_to_student_queue_view(message: Message, state: FSMContext, conn, subjects):
    from queue_bot.handlers.student import open_queue_screen
    data = await state.get_data()
    session_id = data.get("session_id")
    if not session_id:
        await admin_panel_open(message, state, conn=conn)
        return
    await state.clear()
    await state.update_data(session_id=session_id)
    await open_queue_screen(
        message,
        conn,
        subjects,
        session_id,
        note="👤 <b>Режим студента:</b> тут ви можете самі записатися в чергу, відмітити «Здав» або обмінятися місцем.",
    )



async def admin_panel_open(message: Message, state: FSMContext, conn=None):
    await state.clear()
    await state.set_state(AdminPanelState.in_main_menu)
    owner_flag = admins.is_owner(conn, message.from_user.id) if conn is not None else admins.is_owner(None, message.from_user.id)
    info_lines = ["⚡ <b>Адмін-панель старости (ПІ-231)</b>"]
    if conn is not None:
        try:
            from queue_bot.services import polls
            from queue_bot.services.maintenance import is_maintenance_active
            act_q = len(queues.list_sessions(conn, ("open", "closed")))
            act_p = len(polls.list_polls(conn, status="active", limit=50))
            maint_str = "🛠 Техроботи" if is_maintenance_active(conn) else "🟢 Працює"
            info_lines.append(
                f"<i>Черг: <b>{act_q}</b> • Опитувань: <b>{act_p}</b> • Статус: <b>{maint_str}</b></i>"
            )
        except Exception:
            pass
    info_lines.extend([
        "",
        "<b>Черги та студенти</b> — записи, порядок і результати здачі.",
        "<b>Розклад і заміни</b> — пари та зміни розкладу.",
        "<b>Ще для адміна</b> — опитування, список групи та перехід у режим студента.",
    ])
    if owner_flag:
        info_lines.append("Налаштування власника також доступні в «Ще для адміна».")
    await message.answer(
        "\n".join(info_lines),
        reply_markup=get_admin_reply_menu(is_owner=owner_flag),
    )


async def admin_more_panel(message: Message, state: FSMContext, conn):
    from queue_bot.handlers.common import get_admin_more_menu
    admins.require_admin(conn, message.from_user.id)
    await state.clear()
    await state.set_state(AdminPanelState.in_main_menu)
    await state.update_data(ui_mode='admin')
    await message.answer('Ще для адміна\nОпитування, список групи та інші можливості.',
                         reply_markup=get_admin_more_menu(is_owner=admins.is_owner(conn, message.from_user.id)))


async def admin_reports_removed(message: Message, state: FSMContext, conn):
    admins.require_admin(conn, message.from_user.id)
    await message.answer('Розділ звітів і статистики прибрано. Оберіть дію в меню адміністратора.')
    await admin_more_panel(message, state, conn)



async def admin_category_queues(message: Message, state: FSMContext, conn, subjects):
    from .queues import admin_pick_active_queues
    await admin_pick_active_queues(message, state, conn, subjects)


async def permanent_queue_notice(message: Message, state: FSMContext, conn):
    admins.require_admin(conn, message.from_user.id)
    await message.answer('Щоб прибрати чергу з активного списку, відкрийте її картку й натисніть «Видалити чергу».')
    await admin_panel_open(message, state, conn)

