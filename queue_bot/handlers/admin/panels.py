"""Admin panels handlers."""
from .common import (
    AdminPanelState,
    F,
    FSMContext,
    Message,
    StateFilter,
    admins,
    get_admin_queues_menu,
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
        note="⚡ <b>Режим старости/адміна:</b> тут можна додати студентів за списком, змінити порядок або завершити чергу.", state=state,
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
        "🧭 <b>Швидка навігація по розділах:</b>",
        "• <b>📋 Черги та студенти</b> — додати студентів у чергу, змінити порядок, позначити здачу, закрити/архівувати чергу.",
        "• <b>📅 Розклад і заміни</b> — додати заміни за фото, робоча субота, оновити розклад.",
        "• <b>🗳 Опитування</b> — створити голосування для групи (з таймером або без).",
        "• <b>📊 Звіти та статистика</b> — загальна успішність та вивантаження PDF-звітів.",
        "• <b>👥 Список групи</b> — база ПІБ студентів групи та блокування.",
    ])
    if owner_flag:
        info_lines.append("• <b>⚙️ Налаштування</b> — баг-репорти, оголошення на всю групу, техроботи, список адмінів.")
    info_lines.extend([
        "",
        "💡 <i>Щоб самому записатися в чергу або здати лабу як студент — натисніть <b>«👤 Режим студента»</b> внизу!</i>",
    ])
    await message.answer(
        "\n".join(info_lines),
        reply_markup=get_admin_reply_menu(is_owner=owner_flag),
    )



async def admin_category_queues(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AdminPanelState.in_queues_menu)
    await message.answer(
        "📋 <b>Керування чергами та студентами в чергах</b>\n\n"
        "• <b>👀 Активні черги</b> — відкрити будь-яку чергу (всередині можна додати студентів, змінити порядок, закрити чи завершити її)\n"
        "• <b>➕ Створити чергу</b> — відкрити нову чергу на пару або консультацію\n"
        "• <b>📝 Додати студента</b> — швидко вписати студента (або список) у чергу\n"
        "• <b>🔢 Змінити порядок</b> — посунути студента вище/нижче, задати номер або позначити «Здав»\n"
        "• <b>🔒 Відкрити/закрити</b> — тимчасово зупинити або відновити запис\n"
        "• <b>📚 Предмети та викладачі</b> — перегляд, закріплення викладачів та створення предметів\n"
        "• <b>📦 Історія</b> — переглянути завершені черги",
        reply_markup=get_admin_queues_menu(),
    )



async def admin_category_students(message: Message, state: FSMContext):
    await admin_category_queues(message, state)

