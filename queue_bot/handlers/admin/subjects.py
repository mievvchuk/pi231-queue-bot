"""Admin subjects handlers."""
from .common import (
    AdminSubjectsState,
    BotError,
    F,
    FSMContext,
    Message,
    StateFilter,
    admins,
    config,
    get_admin_subjects_menu,
    html,
    make_reply_keyboard,
    subjects_module,
)


async def admin_view_subjects_menu(message: Message, state: FSMContext, conn, subjects):
    await state.clear()
    await state.set_state(AdminSubjectsState.in_menu)
    is_owner = admins.is_owner(conn, message.from_user.id)

    lines = ["📚 <b>Список дисциплін та закріплених викладачів:</b>\n"]
    for idx, s in enumerate(subjects, 1):
        teachers_str = ", ".join(t["name"] for t in s.get("teachers", [])) or "— немає викладачів —"
        lines.append(f"{idx}. <b>{html.escape(s['name'])}</b>\n   👨‍🏫 {html.escape(teachers_str)}")

    lines.append("")
    if is_owner:
        lines.append("<i>Вам як власнику бота також доступні редагування та видалення.</i>")
    else:
        lines.append("<i>Як адміністратор ви можете закріплювати викладачів або додавати нові предмети.</i>")

    await message.answer("\n".join(lines), reply_markup=get_admin_subjects_menu(is_owner=is_owner))



async def admin_add_teacher_start(message: Message, state: FSMContext, subjects):
    await state.set_state(AdminSubjectsState.select_subject_for_teacher)
    rows = [[f"📚 {s['name']}"] for s in subjects]
    rows.append(["🔙 До предметів"])
    await message.answer(
        "Оберіть предмет, до якого потрібно прикріпити викладача 👇",
        reply_markup=make_reply_keyboard(rows),
    )



async def admin_add_teacher_subject_picked(message: Message, state: FSMContext, subjects):
    sub_name = message.text.removeprefix("📚 ").strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == sub_name.lower()), None)
    if not matched_sub:
        await message.answer("Оберіть предмет на кнопках внизу:")
        return

    await state.set_state(AdminSubjectsState.entering_teacher_name)
    await state.update_data(sub_id=matched_sub["id"], sub_name=matched_sub["name"])

    all_teachers = subjects_module.list_all_teachers(subjects)
    curr_ids = {t["id"] for t in matched_sub.get("teachers", [])}
    other_teachers = [t["name"] for t in all_teachers if t["id"] not in curr_ids]

    rows = []
    for i in range(0, len(other_teachers), 2):
        rows.append(other_teachers[i:i+2])
    rows.append(["🔙 До предметів"])

    await message.answer(
        f"👨‍🏫 <b>Прикріплення викладача до предмета «{html.escape(matched_sub['name'])}»</b>\n\n"
        "Оберіть викладача на кнопках нижче або <b>введіть ПІБ текстом</b> (наприклад: <code>Барабаш Д.С.</code>) 👇",
        reply_markup=make_reply_keyboard(rows),
    )



async def admin_add_teacher_name_received(message: Message, state: FSMContext, conn, subjects):
    if message.text in {"🔙 До предметів", "🔙 Скасувати", "↩️ Скасувати"}:
        await admin_view_subjects_menu(message, state, conn, subjects)
        return

    teacher_name = message.text.strip()
    data = await state.get_data()
    sub_id = data.get("sub_id")
    sub_name = data.get("sub_name", "Предмет")

    try:
        teacher = subjects_module.add_teacher_to_subject(
            conn, subjects, sub_id, teacher_name, path=config.SUBJECTS_PATH
        )
        admins.log_action(conn, message.from_user.id, "add_teacher_to_subject", f"{sub_name}: {teacher['name']}")
        await message.answer(
            f"✅ <b>Викладача «{html.escape(teacher['name'])}» успішно прикріплено до предмета «{html.escape(sub_name)}»!</b>\n"
            "Він уже доступний усім студентам для створення черг."
        )
    except BotError as e:
        await message.answer(f"⚠️ {e}\nСпробуйте ще раз або натисніть «🔙 До предметів»:")
        return

    await admin_view_subjects_menu(message, state, conn, subjects)



async def admin_add_new_subject_start(message: Message, state: FSMContext):
    await state.set_state(AdminSubjectsState.entering_new_subject_name)
    await message.answer(
        "➕ <b>Створення нового предмета</b>\n\n"
        "Введіть повну назву предмета (наприклад: <code>Програмування мовою Rust</code>):",
        reply_markup=make_reply_keyboard([["🔙 До предметів"]]),
    )



async def admin_add_new_subject_name_received(message: Message, state: FSMContext, conn, subjects):
    if message.text in {"🔙 До предметів", "🔙 Скасувати", "↩️ Скасувати"}:
        await admin_view_subjects_menu(message, state, conn, subjects)
        return

    sub_name = message.text.strip()
    if len(sub_name) < 2 or len(sub_name) > 80:
        await message.answer("Назва предмета має містити від 2 до 80 символів. Спробуйте ще раз:")
        return

    await state.set_state(AdminSubjectsState.entering_new_subject_teacher)
    await state.update_data(new_sub_name=sub_name)

    all_teachers = subjects_module.list_all_teachers(subjects)
    rows = []
    for i in range(0, len(all_teachers), 2):
        rows.append([t["name"] for t in all_teachers[i:i+2]])
    rows.append(["🔙 До предметів"])

    await message.answer(
        f"Тепер вкажіть першого викладача для предмета «<b>{html.escape(sub_name)}</b>».\n\n"
        "Оберіть на кнопках або <b>введіть ПІБ текстом</b> (наприклад: <code>Когут В.С.</code>) 👇",
        reply_markup=make_reply_keyboard(rows),
    )



async def admin_add_new_subject_teacher_received(message: Message, state: FSMContext, conn, subjects):
    if message.text in {"🔙 До предметів", "🔙 Скасувати", "↩️ Скасувати"}:
        await admin_view_subjects_menu(message, state, conn, subjects)
        return

    teacher_name = message.text.strip()
    data = await state.get_data()
    sub_name = data.get("new_sub_name")

    try:
        new_sub = subjects_module.add_new_subject(
            conn, subjects, sub_name, teacher_name, path=config.SUBJECTS_PATH
        )
        admins.log_action(conn, message.from_user.id, "add_new_subject", f"{new_sub['name']}: {teacher_name}")
        await message.answer(
            f"🎉 <b>Предмет «{html.escape(new_sub['name'])}» успішно створено!</b>\n"
            f"Викладач: <b>{html.escape(teacher_name)}</b>.\n"
            "Тепер студенти та адміни можуть відкривати черги на цей предмет."
        )
    except BotError as e:
        await message.answer(f"⚠️ {e}\nСпробуйте ще раз або натисніть «🔙 До предметів»:")
        return

    await admin_view_subjects_menu(message, state, conn, subjects)



async def admin_rename_subject_start(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    await state.set_state(AdminSubjectsState.select_subject_for_rename)
    rows = [[f"✏️ {s['name']}"] for s in subjects]
    rows.append(["🔙 До предметів"])
    await message.answer("Оберіть предмет для перейменування 👇", reply_markup=make_reply_keyboard(rows))



async def admin_rename_subject_picked(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    sub_name = message.text.removeprefix("✏️ ").strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == sub_name.lower()), None)
    if not matched_sub:
        await message.answer("Оберіть предмет на кнопках внизу:")
        return

    await state.set_state(AdminSubjectsState.entering_subject_new_name)
    await state.update_data(sub_id=matched_sub["id"], old_name=matched_sub["name"])
    await message.answer(
        f"Введіть нову назву для предмета «<b>{html.escape(matched_sub['name'])}</b>»:",
        reply_markup=make_reply_keyboard([["🔙 До предметів"]]),
    )



async def admin_rename_subject_name_received(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    if message.text in {"🔙 До предметів", "🔙 Скасувати", "↩️ Скасувати"}:
        await admin_view_subjects_menu(message, state, conn, subjects)
        return

    new_name = message.text.strip()
    data = await state.get_data()
    sub_id = data.get("sub_id")
    old_name = data.get("old_name")

    try:
        subjects_module.rename_subject(conn, subjects, sub_id, new_name, path=config.SUBJECTS_PATH)
        admins.log_action(conn, message.from_user.id, "rename_subject", f"{old_name} -> {new_name}")
        await message.answer(f"✅ Предмет перейменовано на «<b>{html.escape(new_name)}</b>»!")
    except BotError as e:
        await message.answer(f"⚠️ {e}\nСпробуйте ще раз:")
        return

    await admin_view_subjects_menu(message, state, conn, subjects)



async def admin_remove_teacher_start(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    await state.set_state(AdminSubjectsState.select_subject_for_remove_teacher)
    rows = [[f"📚 {s['name']}"] for s in subjects if len(s.get("teachers", [])) > 1]
    if not rows:
        await message.answer(
            "Усі предмети мають лише по одному викладачу (відкріпити не можна).",
            reply_markup=get_admin_subjects_menu(is_owner=True),
        )
        return
    rows.append(["🔙 До предметів"])
    await message.answer("Оберіть предмет, від якого потрібно відкріпити викладача 👇", reply_markup=make_reply_keyboard(rows))



async def admin_remove_teacher_subject_picked(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    sub_name = message.text.removeprefix("📚 ").strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == sub_name.lower()), None)
    if not matched_sub:
        await message.answer("Оберіть предмет на кнопках внизу:")
        return

    await state.set_state(AdminSubjectsState.select_teacher_to_remove)
    await state.update_data(sub_id=matched_sub["id"], sub_name=matched_sub["name"])
    rows = [[f"❌ {t['name']}"] for t in matched_sub["teachers"]]
    rows.append(["🔙 До предметів"])
    await message.answer(
        f"Оберіть викладача для відкріплення від предмета «{html.escape(matched_sub['name'])}» 👇",
        reply_markup=make_reply_keyboard(rows),
    )



async def admin_remove_teacher_confirmed(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    t_name = message.text.removeprefix("❌ ").strip()
    data = await state.get_data()
    sub_id = data.get("sub_id")
    sub_name = data.get("sub_name")

    matched_sub = subjects_module.find_subject(subjects, sub_id)
    if not matched_sub:
        await admin_view_subjects_menu(message, state, conn, subjects)
        return

    matched_t = next((t for t in matched_sub["teachers"] if t["name"].lower() == t_name.lower()), None)
    if not matched_t:
        await message.answer("Викладача не знайдено.")
        return

    try:
        subjects_module.remove_teacher_from_subject(conn, subjects, sub_id, matched_t["id"], path=config.SUBJECTS_PATH)
        admins.log_action(conn, message.from_user.id, "remove_teacher_from_subject", f"{sub_name}: {t_name}")
        await message.answer(f"✅ Викладача «{html.escape(t_name)}» успішно відкріплено від предмета «{html.escape(sub_name)}».")
    except BotError as e:
        await message.answer(f"⚠️ {e}")

    await admin_view_subjects_menu(message, state, conn, subjects)



async def admin_delete_subject_start(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    await state.set_state(AdminSubjectsState.select_subject_to_delete)
    rows = [[f"🗑 {s['name']}"] for s in subjects]
    rows.append(["🔙 До предметів"])
    await message.answer("Оберіть предмет, який потрібно видалити зі списку 👇", reply_markup=make_reply_keyboard(rows))



async def admin_delete_subject_picked(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    sub_name = message.text.removeprefix("🗑 ").strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == sub_name.lower()), None)
    if not matched_sub:
        await message.answer("Оберіть предмет на кнопках внизу:")
        return

    await state.set_state(AdminSubjectsState.confirming_delete_subject)
    await state.update_data(del_sub_id=matched_sub["id"], del_sub_name=matched_sub["name"])
    rows = [["⚠️ Підтвердити видалення"], ["🔙 До предметів"]]
    await message.answer(
        f"⚠️ <b>Ви впевнені, що хочете видалити предмет «{html.escape(matched_sub['name'])}»?</b>\n\n"
        "Студенти більше не зможуть відкривати на нього черги.",
        reply_markup=make_reply_keyboard(rows),
    )



async def admin_delete_subject_confirmed(message: Message, state: FSMContext, conn, subjects):
    admins.require_owner(conn, message.from_user.id)
    data = await state.get_data()
    sub_id = data.get("del_sub_id")
    sub_name = data.get("del_sub_name")

    try:
        subjects_module.delete_subject(conn, subjects, sub_id, path=config.SUBJECTS_PATH)
        admins.log_action(conn, message.from_user.id, "delete_subject", f"{sub_name}")
        await message.answer(f"✅ Предмет «{html.escape(sub_name)}» успішно видалено.")
    except BotError as e:
        await message.answer(f"⚠️ {e}")

    await admin_view_subjects_menu(message, state, conn, subjects)

