import io
import math
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from queue_bot import config
# Сучасна темна палітра дашборду (Slate / Indigo / Emerald / Amber / Rose)
BG_COLOR = (15, 23, 42)          # #0f172a
CARD_BG = (30, 41, 59)           # #1e293b
CARD_BORDER = (51, 65, 85)       # #334155
TEXT_PRIMARY = (248, 250, 252)   # #f8fafc
TEXT_SECONDARY = (148, 163, 184) # #94a3b8
GRID_COLOR = (51, 65, 85)        # #334155

PALETTE = [
    (56, 189, 248),   # Sky Blue #38bdf8
    (16, 185, 129),   # Emerald #10b981
    (245, 158, 11),   # Amber #f59e0b
    (168, 85, 247),   # Purple #a855f7
    (244, 63, 94),    # Rose #f43f5e
    (99, 102, 241),   # Indigo #6366f1
    (20, 184, 166),   # Teal #14b8a6
    (236, 72, 153),   # Pink #ec4899
    (132, 204, 22),   # Lime #84cc16
    (251, 146, 60),   # Orange #fb923c
]

GOLD = (250, 204, 21)
SILVER = (203, 213, 225)
BRONZE = (217, 119, 6)

_REGULAR_FONTS = [
    "segoeui.ttf",
    "arial.ttf",
    "calibri.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]

_BOLD_FONTS = [
    "segoeuib.ttf",
    "arialbd.ttf",
    "calibrib.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = _BOLD_FONTS if bold else _REGULAR_FONTS
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    for path in _REGULAR_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _truncate(text: str, max_len: int) -> str:
    text = str(text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _short_subject_name(name: str) -> str:
    mapping = {
        "Конструювання програмного забезпечення": "КПЗ",
        "Людино-машинна взаємодія": "ЛМВ",
        "Машинне навчання": "Машинне навч.",
        "Курсова робота з КПЗ": "Курсова КПЗ",
        " Комп'ютерні мережі": "Комп. мережі",
        "Комп'ютерні мережі": "Комп. мережі",
        "Архітектура комп'ютерів": "Архіт. комп.",
        "Бази даних": "Бази даних",
        "Ймовірнісні процеси": "Ймовірн. проц.",
    }
    for k, v in mapping.items():
        if k.lower() in name.lower():
            return v
    return _truncate(name, 15)


def _now_label() -> str:
    try:
        return datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return datetime.now().strftime("%d.%m.%Y %H:%M")


def _draw_card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int = 24):
    draw.rounded_rectangle(box, radius=radius, fill=CARD_BG, outline=CARD_BORDER, width=2)


def _draw_donut(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    outer_r: int,
    inner_r: int,
    segments: list[tuple[str, int, tuple[int, int, int]]],
    center_title: str,
    center_subtitle: str,
):
    """Малює кільцеву діаграму (Donut Chart) із центральним підписом."""
    total = sum(max(0, val) for _, val, _ in segments)
    bbox = (cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r)

    if total <= 0:
        draw.ellipse(bbox, fill=(51, 65, 85))
    else:
        start_angle = -90.0
        non_zero = [(label, val, col) for label, val, col in segments if val > 0]
        if len(non_zero) == 1:
            draw.ellipse(bbox, fill=non_zero[0][2])
        else:
            for _, val, color in non_zero:
                sweep = (val / total) * 360.0
                end_angle = start_angle + sweep
                draw.pieslice(bbox, start=start_angle, end=end_angle, fill=color, outline=CARD_BG, width=4)
                start_angle = end_angle

    inner_box = (cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r)
    draw.ellipse(inner_box, fill=CARD_BG)

    f_big = _get_font(42, bold=True)
    f_sub = _get_font(22, bold=False)
    draw.text((cx, cy - 12), center_title, font=f_big, fill=TEXT_PRIMARY, anchor="mm")
    draw.text((cx, cy + 26), center_subtitle, font=f_sub, fill=TEXT_SECONDARY, anchor="mm")


def _finalize_image(img: Image.Image, target_w: int, target_h: int) -> bytes:
    """Зменшує 2x-полотно з антиаліасингом LANCZOS та експортує у PNG."""
    smooth = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    smooth.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ==============================================================================
# 1. ДІАГРАМА ТИПІВ ЧЕРГ ТА СТАТУСІВ (КІЛЬЦЕВІ / КРУГОВІ ДІАГРАМИ - DONUT CHARTS)
# ==============================================================================

def generate_donut_status_chart_png(stats: dict) -> bytes:
    W, H = 1800, 1000
    img = Image.new("RGB", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    f_title = _get_font(46, bold=True)
    f_sub = _get_font(26, bold=False)
    f_card_title = _get_font(34, bold=True)
    f_legend = _get_font(26, bold=False)
    f_legend_bold = _get_font(26, bold=True)

    # Заголовок
    draw.text((60, 45), "Кільцеві діаграми: Категорії черг та Статуси здач", font=f_title, fill=TEXT_PRIMARY)
    draw.text((60, 105), f"Група {config.STUDENT_GROUP}  •  Станом на {_now_label()}", font=f_sub, fill=TEXT_SECONDARY)

    # Ліва картка: Типи черг
    _draw_card(draw, (60, 165, 870, 940))
    draw.text((105, 205), "1. Розподіл проведених черг за типом", font=f_card_title, fill=TEXT_PRIMARY)

    q_segments = [
        ("Звичайні пари", stats.get("lessons_count", 0), PALETTE[0]),
        ("Консультації", stats.get("consults_count", 0), PALETTE[1]),
        ("Курсова робота", stats.get("cw_count", 0), PALETTE[3]),
    ]
    total_q = stats.get("total_sessions", 0)
    _draw_donut(
        draw,
        cx=465,
        cy=485,
        outer_r=195,
        inner_r=115,
        segments=q_segments,
        center_title=str(total_q),
        center_subtitle="усього черг",
    )

    ly = 735
    for label, count, color in q_segments:
        pct = round((count / total_q) * 100) if total_q > 0 else 0
        draw.rounded_rectangle((120, ly + 4, 150, ly + 34), radius=8, fill=color)
        draw.text((170, ly), label, font=f_legend, fill=TEXT_PRIMARY)
        draw.text((810, ly), f"{count} ({pct}%)", font=f_legend_bold, fill=TEXT_PRIMARY, anchor="ra")
        ly += 55

    # Права картка: Статуси записів у чергах
    _draw_card(draw, (930, 165, 1740, 940))
    draw.text((975, 205), "2. Результати записів студентів у чергах", font=f_card_title, fill=TEXT_PRIMARY)

    s_segments = [
        ("Успішно здано", stats.get("total_passed", 0), PALETTE[1]),
        ("Не здано / перенесено", stats.get("total_removed", 0), PALETTE[4]),
        ("Вийшли з черги", stats.get("total_left", 0), PALETTE[2]),
        ("Зараз активні в черзі", stats.get("total_active", 0), PALETTE[0]),
    ]
    total_entries = sum(x[1] for x in s_segments)
    pass_rate = stats.get("pass_rate", 0)
    _draw_donut(
        draw,
        cx=1335,
        cy=485,
        outer_r=195,
        inner_r=115,
        segments=s_segments,
        center_title=f"{pass_rate}%",
        center_subtitle="успішність",
    )

    ry = 715
    for label, count, color in s_segments:
        pct = round((count / total_entries) * 100) if total_entries > 0 else 0
        draw.rounded_rectangle((990, ry + 4, 1020, ry + 34), radius=8, fill=color)
        draw.text((1040, ry), label, font=f_legend, fill=TEXT_PRIMARY)
        draw.text((1680, ry), f"{count} ({pct}%)", font=f_legend_bold, fill=TEXT_PRIMARY, anchor="ra")
        ry += 50

    return _finalize_image(img, 900, 500)


# ==============================================================================
# 2. СТОВПЧИКОВА ДІАГРАМА ПО ПРЕДМЕТАХ (VERTICAL GROUPED COLUMN CHART)
# ==============================================================================

def generate_subjects_column_chart_png(stats: dict) -> bytes:
    W, H = 1800, 1050
    img = Image.new("RGB", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    f_title = _get_font(46, bold=True)
    f_sub = _get_font(26, bold=False)
    f_axis = _get_font(24, bold=False)
    f_val = _get_font(26, bold=True)
    f_lbl = _get_font(24, bold=True)

    draw.text((60, 45), "Стовпчикова діаграма: Здачі та заняття за предметами", font=f_title, fill=TEXT_PRIMARY)
    draw.text((60, 105), f"Група {config.STUDENT_GROUP}  •  Порівняння активності по дисциплінах", font=f_sub, fill=TEXT_SECONDARY)

    _draw_card(draw, (60, 165, 1740, 990))

    # Легенда зверху картки
    draw.rounded_rectangle((1120, 200, 1152, 232), radius=6, fill=PALETTE[1])
    draw.text((1166, 200), "Успішних здач робіт", font=f_axis, fill=TEXT_PRIMARY)
    draw.rounded_rectangle((1460, 200, 1492, 232), radius=6, fill=PALETTE[0])
    draw.text((1506, 200), "Проведено черг", font=f_axis, fill=TEXT_PRIMARY)

    subjects = stats.get("subjects", [])[:8]
    chart_left = 150
    chart_right = 1680
    chart_top = 290
    chart_bottom = 840
    chart_h = chart_bottom - chart_top
    chart_w = chart_right - chart_left

    max_val = max(
        [max(s.get("passed", 0), s.get("sessions", 0)) for s in subjects] + [5]
    )
    # Округлюємо верхню межу сітки до зручного числа
    grid_steps = 5
    step_val = max(1, math.ceil(max_val / grid_steps))
    y_max = step_val * grid_steps

    # Горизонтальні лінії сітки
    for i in range(grid_steps + 1):
        val = i * step_val
        y = chart_bottom - int((val / y_max) * chart_h)
        draw.line([(chart_left, y), (chart_right, y)], fill=GRID_COLOR, width=2)
        draw.text((chart_left - 20, y), str(val), font=f_axis, fill=TEXT_SECONDARY, anchor="rm")

    if not subjects:
        draw.text(((chart_left + chart_right) // 2, (chart_top + chart_bottom) // 2), "Немає даних по предметах", font=f_title, fill=TEXT_SECONDARY, anchor="mm")
        return _finalize_image(img, 900, 525)

    n = len(subjects)
    slot_w = chart_w / n
    bar_w = min(64, int(slot_w * 0.32))
    gap = 10

    for idx, s in enumerate(subjects):
        cx = int(chart_left + (idx + 0.5) * slot_w)
        passed = s.get("passed", 0)
        sessions_cnt = s.get("sessions", 0)

        # Стовпчик 1: Здано
        h1 = int((passed / y_max) * chart_h) if y_max > 0 else 0
        h1 = max(6 if passed > 0 else 0, h1)
        x1_left = cx - bar_w - gap // 2
        x1_right = cx - gap // 2
        if h1 > 0:
            draw.rounded_rectangle(
                (x1_left, chart_bottom - h1, x1_right, chart_bottom),
                radius=10,
                fill=PALETTE[1],
            )
        draw.text(((x1_left + x1_right) // 2, chart_bottom - h1 - 18), str(passed), font=f_val, fill=PALETTE[1], anchor="mb")

        # Стовпчик 2: Проведено черг
        h2 = int((sessions_cnt / y_max) * chart_h) if y_max > 0 else 0
        h2 = max(6 if sessions_cnt > 0 else 0, h2)
        x2_left = cx + gap // 2
        x2_right = cx + gap // 2 + bar_w
        if h2 > 0:
            draw.rounded_rectangle(
                (x2_left, chart_bottom - h2, x2_right, chart_bottom),
                radius=10,
                fill=PALETTE[0],
            )
        draw.text(((x2_left + x2_right) // 2, chart_bottom - h2 - 18), str(sessions_cnt), font=f_val, fill=PALETTE[0], anchor="mb")

        # Підпис предмета знизу
        short_lbl = _short_subject_name(s.get("name", ""))
        draw.text((cx, chart_bottom + 35), short_lbl, font=f_lbl, fill=TEXT_PRIMARY, anchor="mt")

    return _finalize_image(img, 900, 525)


# ==============================================================================
# 3. ГОРИЗОНТАЛЬНА РЕЙТИНГОВА ДІАГРАМА СТУДЕНТІВ (HORIZONTAL LEADERBOARD CHART)
# ==============================================================================

def generate_top_students_bar_chart_png(stats: dict) -> bytes:
    W, H = 1800, 1120
    img = Image.new("RGB", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    f_title = _get_font(46, bold=True)
    f_sub = _get_font(26, bold=False)
    f_rank = _get_font(26, bold=True)
    f_name = _get_font(27, bold=False)
    f_val = _get_font(27, bold=True)

    draw.text((60, 45), "Горизонтальна діаграма: Рейтинг активності студентів", font=f_title, fill=TEXT_PRIMARY)
    draw.text((60, 105), f"Група {config.STUDENT_GROUP}  •  Топ студентів за загальною кількістю зданих робіт", font=f_sub, fill=TEXT_SECONDARY)

    _draw_card(draw, (60, 165, 1740, 1060))

    top_list = [s for s in stats.get("top_students", []) if s.get("passed", 0) > 0][:12]
    if not top_list:
        top_list = stats.get("top_students", [])[:10]

    if not top_list:
        draw.text((900, 600), "Ще немає зареєстрованих здач у чергах", font=f_title, fill=TEXT_SECONDARY, anchor="mm")
        return _finalize_image(img, 900, 560)

    max_passed = max([s.get("passed", 0) for s in top_list] + [1])
    start_y = 215
    row_h = 68
    bar_left = 540
    bar_max_w = 1020

    for idx, st in enumerate(top_list, 1):
        y = start_y + (idx - 1) * row_h
        passed = st.get("passed", 0)

        if idx == 1:
            bar_color = GOLD
        elif idx == 2:
            bar_color = SILVER
        elif idx == 3:
            bar_color = BRONZE
        else:
            bar_color = PALETTE[0]

        # Бейдж місця
        draw.rounded_rectangle((100, y + 6, 162, y + 50), radius=12, fill=(51, 65, 85))
        draw.text((131, y + 28), f"#{idx}", font=f_rank, fill=bar_color if idx <= 3 else TEXT_PRIMARY, anchor="mm")

        # Ім'я студента
        name_str = _truncate(st.get("name", "Студент"), 24)
        draw.text((185, y + 28), name_str, font=f_name, fill=TEXT_PRIMARY, anchor="lm")

        # Фонова смуга
        draw.rounded_rectangle((bar_left, y + 10, bar_left + bar_max_w, y + 46), radius=14, fill=(15, 23, 42))

        # Заповнена смуга
        fill_w = int((passed / max_passed) * bar_max_w) if max_passed > 0 else 0
        if passed > 0:
            fill_w = max(28, fill_w)
            draw.rounded_rectangle((bar_left, y + 10, bar_left + fill_w, y + 46), radius=14, fill=bar_color)

        # Значення праворуч
        draw.text((bar_left + bar_max_w + 30, y + 28), f"{passed} здач", font=f_val, fill=TEXT_PRIMARY, anchor="lm")

    return _finalize_image(img, 900, 560)


# ==============================================================================
# 4. ЗВЕДЕНИЙ МУЛЬТИ-ДАШБОРД (4 ВІДЖЕТИ НА ОДНОМУ ЕКРАНІ)
# ==============================================================================

def generate_overview_dashboard_png(stats: dict) -> bytes:
    W, H = 1800, 1200
    img = Image.new("RGB", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    f_title = _get_font(46, bold=True)
    f_sub = _get_font(25, bold=False)
    f_kpi_val = _get_font(52, bold=True)
    f_kpi_lbl = _get_font(24, bold=False)
    f_sec_title = _get_font(32, bold=True)
    f_small = _get_font(23, bold=False)
    f_small_b = _get_font(23, bold=True)

    draw.text((60, 40), f"Аналітичний дашборд групи {config.STUDENT_GROUP}", font=f_title, fill=TEXT_PRIMARY)
    draw.text((60, 100), f"Зведена статистика черг, предметів та успішності  •  {_now_label()}", font=f_sub, fill=TEXT_SECONDARY)

    # Верхній ряд: 4 KPI картки
    kpis = [
        ("Проведено черг", str(stats.get("total_sessions", 0)), PALETTE[0]),
        ("Успішних здач", str(stats.get("total_passed", 0)), PALETTE[1]),
        ("Конверсія здач", f"{stats.get('pass_rate', 0)}%", PALETTE[2]),
        ("Студентів у базі", str(stats.get("registered_users", 0) + stats.get("placeholder_users", 0)), PALETTE[3]),
    ]
    card_w = 396
    gap = 32
    for i, (lbl, val, col) in enumerate(kpis):
        x1 = 60 + i * (card_w + gap)
        x2 = x1 + card_w
        _draw_card(draw, (x1, 155, x2, 315))
        draw.rounded_rectangle((x1, 155, x1 + 12, 315), radius=6, fill=col)
        draw.text((x1 + 40, 210), val, font=f_kpi_val, fill=col, anchor="lm")
        draw.text((x1 + 40, 272), lbl, font=f_kpi_lbl, fill=TEXT_SECONDARY, anchor="lm")

    # Ліва нижня панель: Кільцева діаграма типів черг + статусів
    _draw_card(draw, (60, 350, 740, 1140))
    draw.text((100, 385), "Структура черг та здач", font=f_sec_title, fill=TEXT_PRIMARY)

    q_segs = [
        ("Пари", stats.get("lessons_count", 0), PALETTE[0]),
        ("Консультації", stats.get("consults_count", 0), PALETTE[1]),
        ("Курсова", stats.get("cw_count", 0), PALETTE[3]),
    ]
    _draw_donut(
        draw,
        cx=245,
        cy=600,
        outer_r=135,
        inner_r=78,
        segments=q_segs,
        center_title=str(stats.get("total_sessions", 0)),
        center_subtitle="черг",
    )
    ly = 510
    for lbl, cnt, col in q_segs:
        draw.rounded_rectangle((415, ly, 440, ly + 25), radius=6, fill=col)
        draw.text((455, ly - 2), f"{lbl}: {cnt}", font=f_small_b, fill=TEXT_PRIMARY)
        ly += 52

    s_segs = [
        ("Здано", stats.get("total_passed", 0), PALETTE[1]),
        ("Перездача", stats.get("total_removed", 0), PALETTE[4]),
        ("Вихід", stats.get("total_left", 0), PALETTE[2]),
    ]
    _draw_donut(
        draw,
        cx=245,
        cy=945,
        outer_r=135,
        inner_r=78,
        segments=s_segs,
        center_title=f"{stats.get('pass_rate', 0)}%",
        center_subtitle="здач",
    )
    ly2 = 855
    for lbl, cnt, col in s_segs:
        draw.rounded_rectangle((415, ly2, 440, ly2 + 25), radius=6, fill=col)
        draw.text((455, ly2 - 2), f"{lbl}: {cnt}", font=f_small_b, fill=TEXT_PRIMARY)
        ly2 += 52

    # Права верхня панель: Стовпчикова діаграма по предметах
    _draw_card(draw, (780, 350, 1740, 735))
    draw.text((820, 385), "Здачі за предметами (Стовпчикова)", font=f_sec_title, fill=TEXT_PRIMARY)

    subs = stats.get("subjects", [])[:6]
    max_sub_p = max([s.get("passed", 0) for s in subs] + [1])
    if subs:
        s_left = 830
        s_right = 1700
        s_bottom = 665
        s_max_h = 190
        slot = (s_right - s_left) / len(subs)
        for i, s in enumerate(subs):
            cx = int(s_left + (i + 0.5) * slot)
            p_cnt = s.get("passed", 0)
            bh = int((p_cnt / max_sub_p) * s_max_h) if max_sub_p > 0 else 0
            bh = max(6 if p_cnt > 0 else 0, bh)
            col = PALETTE[i % len(PALETTE)]
            if bh > 0:
                draw.rounded_rectangle((cx - 32, s_bottom - bh, cx + 32, s_bottom), radius=8, fill=col)
            draw.text((cx, s_bottom - bh - 12), str(p_cnt), font=f_small_b, fill=TEXT_PRIMARY, anchor="mb")
            draw.text((cx, s_bottom + 14), _short_subject_name(s.get("name", "")), font=f_small, fill=TEXT_SECONDARY, anchor="mt")

    # Права нижня панель: Топ-5 студентів (Горизонтальні смуги)
    _draw_card(draw, (780, 765, 1740, 1140))
    draw.text((820, 800), "Топ-5 активних студентів (Рейтингова)", font=f_sec_title, fill=TEXT_PRIMARY)

    top5 = [s for s in stats.get("top_students", []) if s.get("passed", 0) > 0][:5]
    if not top5:
        draw.text((1260, 960), "Дані з'являться після перших здач", font=f_small, fill=TEXT_SECONDARY, anchor="mm")
    else:
        max_st = max([s.get("passed", 0) for s in top5] + [1])
        ty = 865
        for idx, st in enumerate(top5, 1):
            p_val = st.get("passed", 0)
            col = GOLD if idx == 1 else (SILVER if idx == 2 else (BRONZE if idx == 3 else PALETTE[0]))
            draw.text((820, ty + 15), f"#{idx} {_truncate(st.get('name', ''), 20)}", font=f_small_b, fill=TEXT_PRIMARY, anchor="lm")
            bx = 1210
            bw_max = 390
            draw.rounded_rectangle((bx, ty + 2, bx + bw_max, ty + 30), radius=10, fill=(15, 23, 42))
            fw = max(20, int((p_val / max_st) * bw_max))
            draw.rounded_rectangle((bx, ty + 2, bx + fw, ty + 30), radius=10, fill=col)
            draw.text((bx + bw_max + 20, ty + 15), str(p_val), font=f_small_b, fill=TEXT_PRIMARY, anchor="lm")
            ty += 52

    return _finalize_image(img, 900, 600)


# ==============================================================================
# 5. ДІАГРАМА ПО КОНКРЕТНОМУ ПРЕДМЕТУ (DONUT + HORIZONTAL STUDENT BARS)
# ==============================================================================

def generate_subject_report_chart_png(subject_name: str, teachers_str: str, report: dict) -> bytes:
    W, H = 1800, 1000
    img = Image.new("RGB", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    f_title = _get_font(44, bold=True)
    f_sub = _get_font(25, bold=False)
    f_sec = _get_font(32, bold=True)
    f_text = _get_font(25, bold=False)
    f_bold = _get_font(26, bold=True)

    draw.text((60, 42), f"Звіт з дисципліни: {_truncate(subject_name, 42)}", font=f_title, fill=TEXT_PRIMARY)
    draw.text((60, 102), f"Викладачі: {_truncate(teachers_str, 65)}  •  {_now_label()}", font=f_sub, fill=TEXT_SECONDARY)

    # Ліва картка: Кільцева діаграма успішності по предмету
    _draw_card(draw, (60, 165, 720, 940))
    draw.text((100, 205), "Статистика здач предмету", font=f_sec, fill=TEXT_PRIMARY)

    students = report.get("students", [])
    total_passed = report.get("total_passed", 0)
    total_not_passed = sum(s.get("not_passed_count", 0) for s in students)
    in_queue_now = sum(1 for s in students if s.get("current_position"))

    segs = [
        ("Здано успішно", total_passed, PALETTE[1]),
        ("Спроб (не здано)", total_not_passed, PALETTE[4]),
        ("Зараз у черзі", in_queue_now, PALETTE[0]),
    ]
    denom = total_passed + total_not_passed
    rate = round((total_passed / denom) * 100) if denom > 0 else (100 if total_passed > 0 else 0)

    _draw_donut(
        draw,
        cx=390,
        cy=485,
        outer_r=180,
        inner_r=105,
        segments=segs,
        center_title=f"{rate}%",
        center_subtitle="успішність",
    )

    ly = 710
    draw.text((105, ly), f"Проведено черг (занять): {report.get('sessions_count', 0)}", font=f_bold, fill=PALETTE[0])
    ly += 48
    for lbl, val, col in segs:
        draw.rounded_rectangle((105, ly + 4, 133, ly + 32), radius=6, fill=col)
        draw.text((150, ly), lbl, font=f_text, fill=TEXT_PRIMARY)
        draw.text((675, ly), str(val), font=f_bold, fill=TEXT_PRIMARY, anchor="ra")
        ly += 48

    # Права картка: Рейтинг студентів з предмета
    _draw_card(draw, (760, 165, 1740, 940))
    draw.text((805, 205), "Лідери здач з цього предмета", font=f_sec, fill=TEXT_PRIMARY)

    top_st = [s for s in students if s.get("passed_count", 0) > 0 or s.get("not_passed_count", 0) > 0][:10]
    if not top_st:
        draw.text((1250, 550), "Записів по предмету ще немає", font=f_sec, fill=TEXT_SECONDARY, anchor="mm")
    else:
        max_p = max([s.get("passed_count", 0) for s in top_st] + [1])
        sy = 280
        for idx, s in enumerate(top_st, 1):
            p_cnt = s.get("passed_count", 0)
            np_cnt = s.get("not_passed_count", 0)
            col = PALETTE[1] if idx > 3 else (GOLD if idx == 1 else (SILVER if idx == 2 else BRONZE))

            draw.text((805, sy + 18), f"{idx}. {_truncate(s.get('name', ''), 22)}", font=f_text, fill=TEXT_PRIMARY, anchor="lm")
            bx = 1220
            bw_max = 360
            draw.rounded_rectangle((bx, sy + 2, bx + bw_max, sy + 34), radius=10, fill=(15, 23, 42))
            if p_cnt > 0:
                fw = max(24, int((p_cnt / max_p) * bw_max))
                draw.rounded_rectangle((bx, sy + 2, bx + fw, sy + 34), radius=10, fill=col)
            extra = f" (спроб: {np_cnt})" if np_cnt > 0 else ""
            draw.text((bx + bw_max + 20, sy + 18), f"{p_cnt}{extra}", font=f_bold, fill=TEXT_PRIMARY, anchor="lm")
            sy += 62

    return _finalize_image(img, 900, 500)


# ==============================================================================
# 6. ДІАГРАМА РЕЗУЛЬТАТІВ ОПИТУВАННЯ (PIE / DONUT + HORIZONTAL BARS)
# ==============================================================================

def generate_poll_results_chart_png(poll_data: dict) -> bytes:
    W, H = 1800, 1000
    img = Image.new("RGB", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    poll = poll_data["poll"]
    options = poll_data["options"]
    total_voters = poll_data["total_voters"]

    f_title = _get_font(42, bold=True)
    f_sub = _get_font(25, bold=False)
    f_sec = _get_font(32, bold=True)
    f_opt = _get_font(25, bold=False)
    f_val = _get_font(26, bold=True)

    q_title = _truncate(poll["question"], 55)
    status_str = "Завершено" if poll["status"] == "closed" else "Активне опитування"
    anon_str = "Анонімне" if bool(poll["is_anonymous"]) else "Публічне"

    draw.text((60, 42), f"Опитування #{poll['id']}: {q_title}", font=f_title, fill=TEXT_PRIMARY)
    draw.text((60, 102), f"{status_str}  •  {anon_str}  •  Проголосувало: {total_voters}  •  {_now_label()}", font=f_sub, fill=TEXT_SECONDARY)

    # Ліва картка: Кругова / Кільцева діаграма часток голосів
    _draw_card(draw, (60, 165, 720, 940))
    draw.text((100, 205), "Частка голосів (Кругова)", font=f_sec, fill=TEXT_PRIMARY)

    segs = [
        (_truncate(opt["text"], 18), opt["count"], PALETTE[i % len(PALETTE)])
        for i, opt in enumerate(options)
    ]
    _draw_donut(
        draw,
        cx=390,
        cy=520,
        outer_r=200,
        inner_r=105,
        segments=segs,
        center_title=str(total_voters),
        center_subtitle="голосів",
    )

    # Права картка: Смугова діаграма варіантів
    _draw_card(draw, (760, 165, 1740, 940))
    draw.text((805, 205), "Розподіл за варіантами відповіді", font=f_sec, fill=TEXT_PRIMARY)

    oy = 280
    row_step = min(72, int(620 / max(1, len(options))))
    for i, opt in enumerate(options[:10]):
        col = PALETTE[i % len(PALETTE)]
        pct = opt["percent"]
        cnt = opt["count"]

        draw.rounded_rectangle((805, oy + 6, 833, oy + 34), radius=6, fill=col)
        draw.text((850, oy + 20), f"{i + 1}. {_truncate(opt['text'], 24)}", font=f_opt, fill=TEXT_PRIMARY, anchor="lm")

        bx = 1280
        bw_max = 310
        draw.rounded_rectangle((bx, oy + 4, bx + bw_max, oy + 36), radius=10, fill=(15, 23, 42))
        if pct > 0:
            fw = max(18, int((pct / 100) * bw_max))
            draw.rounded_rectangle((bx, oy + 4, bx + fw, oy + 36), radius=10, fill=col)

        draw.text((bx + bw_max + 20, oy + 20), f"{pct}% ({cnt})", font=f_val, fill=TEXT_PRIMARY, anchor="lm")
        oy += row_step

    return _finalize_image(img, 900, 500)
