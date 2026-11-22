"""Private Telegram screens backed by the same grade records as the Mini App."""

import html
from datetime import date, datetime
from uuid import uuid4

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from queue_bot.handlers.common import journal_web_app, show
from queue_bot.services import admins, journal
from queue_bot.services.errors import BotError


class JournalFlow(StatesGroup):
    browsing = State()
    grade = State()
    due_date = State()


def _period_label(period):
    year, semester = period.split(":")
    return f"{year} · семестр {semester}"


def _avg(data, student_id, subject_key, number):
    average = data["averages"].get(str(student_id), {}).get(subject_key, {}).get(str(number), {}).get("average")
    return f"{average:.2f}" if average is not None else "—"


def _cell_index(data, student_id, subject_key):
    return {(r["kind"], r["number"], r["part"]): r for r in data["records"]
            if r["user_id"] == student_id and r["subject_key"] == subject_key}


def _grade_text(record):
    return f"{record['grade']:g}" if record else "—"


def _work_button(work, records, button, attestation):
    number = work["number"]
    grades = " / ".join(_grade_text(records.get(("lab", number, part))) for part in range(1, work["parts"] + 1))
    title = f"{work['title']} · {grades}"
    if work["parts"] == 1:
        return button(title, "cell", "lab", number, 1, attestation)
    return button(title, "parts", number, attestation)


async def _render(event, state, data, context, screen="subjects", *args, note=None):
    token = uuid4().hex[:10]
    actions = []
    rows = []
    target = context.get("student") or data["viewer_id"]
    student = next((row for row in data["students"] if row["id"] == target), None)
    subject = next((row for row in data["subjects"] if row["key"] == context.get("subject")), None)
    records = _cell_index(data, target, subject["key"]) if subject else {}
    lines = [f"📘 <b>Електронний журнал</b>\n{_period_label(data['period'])}"]
    flow = JournalFlow.browsing

    def button(label, action, *values):
        index = len(actions)
        actions.append([action, *values])
        return InlineKeyboardButton(text=label, callback_data=f"jr:{token}:{index}")

    def paged(items, page, action, *values, size=8):
        last = (len(items) - 1) // size
        page = max(0, min(page, last))
        for item in items[page * size:(page + 1) * size]:
            rows.append([item])
        navigation = []
        if page > 0:
            navigation.append(button("‹ Попередні", action, *values, page - 1))
        if page < last:
            navigation.append(button("Наступні ›", action, *values, page + 1))
        if navigation:
            rows.append(navigation)

    if screen == "subjects":
        lines.append(f"\n<b>{html.escape(student['name']) if student and data['group'] else 'Мої оцінки'}</b>\nОберіть предмет.")
        paged([button(s["name"], "subject", s["key"]) for s in data["subjects"]], args[0] if args else 0, "subjects")
        rows.append([button("Навчальний період", "periods")])
        if context["admin"]:
            rows.append([button("Журнал групи" if not data["group"] else "Інший студент", "students")])
            if data["group"]:
                rows.append([button("Мої оцінки", "personal")])
        if target_app := journal_web_app():
            rows.append([InlineKeyboardButton(text="Відкрити в Мініапці", web_app=target_app)])

    elif screen == "students":
        lines.append("\n<b>Журнал групи</b>\nОберіть студента.")
        paged([button(s["name"], "student", s["id"]) for s in data["students"]], args[0] if args else 0, "students")
        rows.append([button("‹ Мої оцінки", "personal")])

    elif screen == "periods":
        lines.append("\nОберіть навчальний період.")
        paged([button(_period_label(p), "period", p) for p in data["periods"]], args[0] if args else 0, "periods")
        rows.append([button("‹ До предметів", "subjects")])

    else:
        if not subject or not student:
            raise BotError("Оберіть предмет і студента ще раз.")
        lines.append(f"\n<b>{html.escape(subject['name'])}</b>")
        if data["group"]:
            lines.append(html.escape(student["name"]))
        if screen == "subject":
            for n in (1, 2):
                due = next((d["due_date"] for d in data["dates"] if d["subject_key"] == subject["key"] and d["number"] == n), None)
                when = f" · {date.fromisoformat(due):%d.%m.%Y}" if due else ""
                lines.append(f"Атестація {n}: <b>{_avg(data, target, subject['key'], n)}</b>{when}")
                rows.append([button(f"Роботи · атестація {n}", "works", n)])
                if data["can_set_dates"]:
                    rows[-1].append(button(f"Дата {n}", "due", n))
            configured = {("lab", work["number"], p) for work in subject["labs"] for p in range(1, work["parts"] + 1)}
            if records.keys() - configured:
                rows.append([button("Попередні записи", "works", 0)])
            rows.append([button("‹ До предметів", "subjects")])

        elif screen == "works":
            attestation, *page = args
            lines.append(f"\n<b>{f'Атестація {attestation}' if attestation else 'Попередні записи'}</b>")
            if attestation:
                entries = [_work_button(w, records, button, attestation) for w in subject["labs"] if w["attestation"] == attestation]
            else:
                configured = {("lab", w["number"], p) for w in subject["labs"] for p in range(1, w["parts"] + 1)}
                entries = [button(f"{'ЛР' if kind == 'lab' else 'Атестація'} {n} · оцінка {p}: {_grade_text(rec)}", "cell", kind, n, p, 0)
                           for (kind, n, p), rec in records.items() if (kind, n, p) not in configured]
            if not entries:
                lines.append("Робіт у цьому розділі немає.")
            paged(entries, page[0] if page else 0, "works", attestation)
            rows.append([button("‹ До предмета", "subject")])

        elif screen == "parts":
            number, attestation = args
            work = next((w for w in subject["labs"] if w["number"] == number), None)
            if not work:
                raise BotError("Робота більше не доступна. Відкрийте предмет знову.")
            lines.append(f"\n<b>{html.escape(work['title'])}</b>\nОберіть оцінку.")
            for part in range(1, work["parts"] + 1):
                label = f"Оцінка {part} · {_grade_text(records.get(('lab', number, part)))}"
                rows.append([button(label, "cell", "lab", number, part, attestation)])
            rows.append([button("‹ До робіт", "works", attestation)])

        elif screen == "cell":
            kind, number, part, attestation = args
            record = records.get((kind, number, part))
            work = next((w for w in subject["labs"] if kind == "lab" and w["number"] == number), None)
            if record and not record["can_edit"]:
                raise BotError("Цю оцінку може редагувати лише її власник або адміністратор.")
            if not record and (not work or part > work["parts"]):
                raise BotError("Такої клітинки в журналі немає.")
            title = work["title"] if work else f"{'ЛР' if kind == 'lab' else 'Атестація'} {number}"
            lines.append(f"\n<b>{html.escape(title)} · оцінка {part}</b>\nПоточна оцінка: {_grade_text(record)}")
            if record and record.get("note"):
                lines.append(f"Примітка: {html.escape(record['note'])}")
            lines.append("Оберіть бал або надішліть число від 0 до 5. Дробову оцінку можна ввести через кому.")
            context["draft"] = dict(period=data["period"], user_id=target, subject_key=subject["key"], kind=kind,
                                    number=number, part=part, note=record.get("note", "") if record else "")
            if record:
                context["draft"].update(record_id=record["id"], revision=record["revision"])
            context["return_to"] = ["parts", number, attestation] if work and work["parts"] > 1 else ["works", attestation]
            rows.extend([[button(str(n), "grade", n) for n in range(first, first + 3)] for first in (0, 3)])
            rows.append([button("Скасувати", *context["return_to"])])
            flow = JournalFlow.grade

        elif screen == "due":
            if not data["can_set_dates"]:
                raise BotError("Дату атестації може змінити лише власник бота.")
            number, = args
            lines.append(f"\n<b>Дата атестації {number}</b>\nНадішліть дату у форматі ДД.ММ.РРРР або «-», щоб прибрати її.")
            context["due_number"] = number
            rows.append([button("Скасувати", "subject")])
            flow = JournalFlow.due_date

    if note:
        lines.append(f"\n{note}")
    context.update(token=token, actions=actions)
    await state.set_state(flow)
    await state.update_data(journal=context)
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if hasattr(event, "message"):
        await show(event, "\n".join(lines), markup)
    else:
        await event.answer("\n".join(lines), reply_markup=markup)


def _view(conn, uid, subjects, context, *, group=False):
    context["admin"] = admins.is_admin(conn, uid)
    mode = group or context.get("student") is not None
    return journal.view(conn, uid, subjects, group=mode, period=context.get("period"))


async def open_journal(message, state, conn, subjects):
    context = {}
    data = _view(conn, message.from_user.id, subjects, context)
    await state.clear()
    await _render(message, state, data, context)


async def journal_action(callback, state, conn, subjects):
    context = (await state.get_data()).get("journal")
    parts = callback.data.split(":")
    if not context or len(parts) != 3 or parts[1] != context["token"] or not parts[2].isdigit():
        raise BotError("Ця кнопка застаріла. Відкрийте «Електронний журнал» знову.")
    index = int(parts[2])
    if not 0 <= index < len(context["actions"]):
        raise BotError("Ця кнопка застаріла.")
    action, *args = context["actions"][index]
    uid = callback.from_user.id
    if action == "grade":
        await _save_grade(callback, state, conn, subjects, context, args[0])
        return
    if action == "student":
        context["student"] = args[0]
    elif action == "personal":
        context.pop("student", None)
    elif action == "period":
        context["period"] = args[0]
    elif action == "subject" and args:
        context["subject"] = args[0]
        args = []
    data = _view(conn, uid, subjects, context, group=action == "students")
    context["period"] = data["period"]
    if action in ("student", "personal", "period"):
        await _render(callback, state, data, context)
    else:
        await _render(callback, state, data, context, action, *args)


async def _save_grade(event, state, conn, subjects, context, value):
    uid = event.from_user.id
    journal.submit(conn, uid, subjects, {**context["draft"], "grade": value}, admin_mode=context.get("student") is not None)
    data = _view(conn, uid, subjects, context)
    await _render(event, state, data, context, *context["return_to"], note="✅ Оцінку збережено.")


async def journal_grade(message, state, conn, subjects):
    context = (await state.get_data())["journal"]
    try:
        value = float(message.text.strip().replace(",", "."))
    except ValueError:
        raise BotError("Введіть число від 0 до 5.") from None
    await _save_grade(message, state, conn, subjects, context, value)


async def journal_due_date(message, state, conn, subjects):
    context = (await state.get_data())["journal"]
    try:
        due = None if message.text.strip() == "-" else datetime.strptime(message.text.strip(), "%d.%m.%Y").date().isoformat()
    except ValueError:
        raise BotError("Вкажіть дату у форматі ДД.ММ.РРРР або «-».") from None
    journal.set_date(conn, message.from_user.id, subjects, {
        "period": context["period"], "subject_key": context["subject"],
        "number": context["due_number"], "due_date": due,
    })
    data = _view(conn, message.from_user.id, subjects, context)
    await _render(message, state, data, context, "subject", note="✅ Дату оновлено.")
