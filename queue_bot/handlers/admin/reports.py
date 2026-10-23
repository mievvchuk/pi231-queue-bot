"""Admin reports handlers."""
from .common import (
    AdminPanelState,
    BufferedInputFile,
    F,
    FSMContext,
    Message,
    StateFilter,
    get_admin_reports_menu,
    get_paginated_reply_keyboard,
    html,
    log,
    make_reply_keyboard,
    paginate,
    queues,
    re,
)


async def admin_category_reports(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AdminPanelState.in_reports_menu)
    await message.answer(
        "📊 <b>Звіти, графічні діаграми та аналітика ПІ-231</b>\n\n"
        "• <b>📈 Загальна статистика</b> — зведений візуальний мульти-дашборд (4 графіки на одному полотні) + текстовий звіт.\n"
        "• <b>🍩 Діаграма: Типи та статуси</b> — кільцеві діаграми (Donut Charts) категорій черг та результатів здач.\n"
        "• <b>📊 Діаграма: Предмети</b> — вертикальна стовпчикова діаграма (Column Chart) порівняння дисциплін.\n"
        "• <b>🏆 Діаграма: Топ студентів</b> — горизонтальна рейтингова діаграма (Leaderboard Bar Chart) лідерів здач.\n"
        "• <b>📚 Звіти за предметами</b> — комбінована діаграма та CSV-таблиця по конкретній дисципліні.\n"
        "• <b>📥 Експорт зведеної таблиці (CSV)</b> — єдиний файл Excel/CSV з матрицею всіх студентів та предметів.\n\n"
        "Оберіть потрібний тип звіту або діаграми нижче 👇",
        reply_markup=get_admin_reports_menu(),
    )



async def admin_global_statistics_view(message: Message, conn, subjects):
    from queue_bot.services import charts

    stats = queues.get_global_statistics(conn, subjects)
    lines = [
        "📈 <b>Загальна статистика черг ПІ-231</b>\n",
        f"📁 <b>Проведено черг усього:</b> {stats['total_sessions']} "
        f"(активних: {stats['active_sessions']}, в архіві: {stats['archived_sessions']})",
        f"   ├ 📖 Звичайні пари: <b>{stats['lessons_count']}</b>",
        f"   ├ 💬 Консультації: <b>{stats['consults_count']}</b>",
        f"   └ 📑 Курсова робота: <b>{stats['cw_count']}</b>\n",
        f"✅ <b>Успішних здач робіт:</b> {stats['total_passed']}",
        f"🔄 <b>Не здано / перенесено:</b> {stats['total_removed']}",
        f"🚪 <b>Виходів із черги:</b> {stats['total_left']}",
        f"📊 <b>Конверсія здач:</b> {stats['pass_rate']}%\n",
        f"👥 <b>Студентів у базі:</b> {stats['registered_users'] + stats['placeholder_users']} "
        f"(прив'язали TG: {stats['registered_users']})\n",
    ]

    if stats["subjects"]:
        lines.append("📚 <b>Активність за предметами:</b>")
        for s in stats["subjects"]:
            lines.append(
                f"• <b>{html.escape(s['name'])}</b> — здач: <b>{s['passed']}</b> (черг: {s['sessions']})"
            )
        lines.append("")

    active_top = [st for st in stats["top_students"] if st["passed"] > 0]
    if active_top:
        lines.append("🏆 <b>Топ-10 студентів за кількістю здач:</b>")
        for idx, st in enumerate(active_top[:10], 1):
            medal = "🥇" if idx == 1 else ("🥈" if idx == 2 else ("🥉" if idx == 3 else f"{idx}."))
            lines.append(f"{medal} {html.escape(st['name'])} — <b>{st['passed']}</b> ✅")

    try:
        png_bytes = charts.generate_overview_dashboard_png(stats)
        chart_file = BufferedInputFile(png_bytes, filename="pi231_overview_dashboard.png")
        await message.answer_photo(
            chart_file,
            caption="📈 <b>Зведений аналітичний дашборд групи ПІ-231</b>",
        )
    except Exception as e:
        log.warning(f"Could not render overview dashboard chart: {e}")

    await message.answer("\n".join(lines), reply_markup=get_admin_reports_menu())



async def admin_chart_donut_status(message: Message, conn, subjects):
    from queue_bot.services import charts

    stats = queues.get_global_statistics(conn, subjects)
    png_bytes = charts.generate_donut_status_chart_png(stats)
    chart_file = BufferedInputFile(png_bytes, filename="pi231_donut_status.png")
    await message.answer_photo(
        chart_file,
        caption=(
            "🍩 <b>Кільцеві діаграми: Категорії черг та Статуси здач</b>\n"
            f"• Усього проведено черг: <b>{stats['total_sessions']}</b>\n"
            f"• Загальна конверсія успішних здач: <b>{stats['pass_rate']}%</b> ({stats['total_passed']} здано)"
        ),
        reply_markup=get_admin_reports_menu(),
    )



async def admin_chart_subjects_columns(message: Message, conn, subjects):
    from queue_bot.services import charts

    stats = queues.get_global_statistics(conn, subjects)
    png_bytes = charts.generate_subjects_column_chart_png(stats)
    chart_file = BufferedInputFile(png_bytes, filename="pi231_subjects_columns.png")
    await message.answer_photo(
        chart_file,
        caption=(
            "📊 <b>Стовпчикова діаграма активності за предметами</b>\n"
            "Порівняння кількості успішних здач робіт (зелений) та проведених черг (блакитний) по кожній дисципліні."
        ),
        reply_markup=get_admin_reports_menu(),
    )



async def admin_chart_top_students(message: Message, conn, subjects):
    from queue_bot.services import charts

    stats = queues.get_global_statistics(conn, subjects)
    png_bytes = charts.generate_top_students_bar_chart_png(stats)
    chart_file = BufferedInputFile(png_bytes, filename="pi231_top_students.png")
    await message.answer_photo(
        chart_file,
        caption=(
            "🏆 <b>Горизонтальна рейтингова діаграма активності студентів</b>\n"
            "Лідери групи ПІ-231 за загальною кількістю успішно зданих робіт у чергах."
        ),
        reply_markup=get_admin_reports_menu(),
    )



async def admin_export_global_csv(message: Message, conn, subjects):
    csv_bytes = queues.export_global_statistics_csv(conn, subjects)
    file = BufferedInputFile(csv_bytes, filename="pi231_full_statistics.csv")
    await message.answer_document(
        file,
        caption=(
            "📊 <b>Зведена таблиця успішності групи ПІ-231</b>\n"
            "Містить дані по кожному студенту та всіх предметах семестру (формат CSV для Excel / Google Таблиць)."
        ),
        reply_markup=get_admin_reports_menu(),
    )



async def admin_pick_report_subject(message: Message, state: FSMContext, subjects):
    await state.clear()
    await state.set_state(AdminPanelState.select_subject_for_report)
    await state.update_data(page=1)
    page_subjects, cur_page, total_pages = paginate(subjects, 1, per_page=8)
    rows = [[f"📊 {s['name']}"] for s in page_subjects]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад")
    await message.answer(
        "📊 <b>Звіти по предметах</b>\n\nОберіть предмет для формування звіту та файлу таблиці 👇",
        reply_markup=markup,
    )



async def admin_paginate_report_subjects(message: Message, state: FSMContext, subjects):
    data = await state.get_data()
    page = data.get("page", 1)
    page = (page - 1) if message.text == "⬅️" else (page + 1)
    await state.update_data(page=page)
    page_subjects, cur_page, total_pages = paginate(subjects, page, per_page=8)
    rows = [[f"📊 {s['name']}"] for s in page_subjects]
    markup = get_paginated_reply_keyboard(rows, cur_page, total_pages, back_text="🔙 Назад")
    await message.answer("Оберіть предмет зі списку нижче 👇", reply_markup=markup)



async def admin_generate_subject_report(message: Message, state: FSMContext, conn, subjects):
    raw_name = message.text.removeprefix("📊").strip()
    matched_sub = next((s for s in subjects if s["name"].lower() == raw_name.lower()), None)
    if not matched_sub:
        await message.answer("⚠️ Предмет не знайдено. Оберіть зі списку на кнопках внизу 👇")
        return

    report = queues.get_subject_report(conn, matched_sub["id"])
    teachers = ", ".join(t["name"] for t in matched_sub.get("teachers", [])) or "—"

    lines = [
        f"📊 <b>Звіт по предмету: {html.escape(matched_sub['name'])}</b>",
        f"👨‍🏫 Викладачі: {html.escape(teachers)}",
        f"📁 Проведено занять: <b>{report['sessions_count']}</b>",
        f"✅ Всього здач: <b>{report['total_passed']}</b>",
    ]

    if report["active_session"]:
        lines.append("⚡ Триває активна черга.")

    lines.append("")
    lines.append("🏆 <b>Рейтинг здач студентів:</b>")
    if not report["students"]:
        lines.append("Записів немає.")
    else:
        for idx, s in enumerate(report["students"][:15], 1):
            pos_info = f" (в черзі: #{s['current_position']})" if s["current_position"] else ""
            lines.append(f"{idx}. {html.escape(s['name'])} — <b>{s['passed_count']}</b> ✅{pos_info}")
        if len(report["students"]) > 15:
            lines.append(f"... та ще {len(report['students']) - 15} студентів у таблиці нижче.")

    text_msg = "\n".join(lines)

    csv_rows = [
        "Прізвище та ім'я;Здано робіт;Спроб (не здав);Місце в поточній черзі;Дати здач"
    ]
    for s in report["students"]:
        dates_str = ", ".join(s["dates"]) if s["dates"] else "-"
        cur_pos = str(s["current_position"]) if s["current_position"] else "-"
        csv_rows.append(f"{s['name']};{s['passed_count']};{s['not_passed_count']};{cur_pos};{dates_str}")

    csv_data = "\r\n".join(csv_rows).encode("utf-8-sig")
    safe_name = re.sub(r"[^\w\s-]", "", matched_sub["name"]).replace(" ", "_")
    file = BufferedInputFile(csv_data, filename=f"report_{safe_name}.csv")

    menu_rows = [
        ["📊 Інший предмет"],
        ["🔙 Назад"],
    ]

    try:
        from queue_bot.services import charts
        chart_bytes = charts.generate_subject_report_chart_png(matched_sub["name"], teachers, report)
        chart_photo = BufferedInputFile(chart_bytes, filename=f"chart_{safe_name}.png")
        await message.answer_photo(
            chart_photo,
            caption=f"📊 <b>Діаграма успішності: {html.escape(matched_sub['name'])}</b>",
        )
    except Exception as e:
        log.warning(f"Could not generate subject chart: {e}")

    await message.answer(text_msg)
    await message.answer_document(
        file,
        caption=f"📄 Таблиця: {html.escape(matched_sub['name'])}",
        reply_markup=make_reply_keyboard(menu_rows),
    )

