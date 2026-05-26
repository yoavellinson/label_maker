from pathlib import Path
from dataclasses import dataclass
from io import BytesIO
import math
import sys

from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from bidi.algorithm import get_display


W, H = 50 * mm, 100 * mm


@dataclass(frozen=True)
class LabelData:
    badge_line_1: str = "שני זנים"
    badge_line_2: str = "אתיופים"
    roast_level: str = "קלייה בהירה"
    specialty_coffee: bool = False
    cup_score: str = ""
    note_1: str = "פירותי"
    note_2: str = "פרחוני"
    note_3: str = "מתוק"
    specialty_variety: str = ""
    specialty_process: str = ""
    specialty_pill_1: str = "דובדבן שחור"
    specialty_pill_2: str = "נוגט"
    specialty_pill_3: str = "תאנים יבשות"
    specialty_pill_4: str = "תפוז"
    specialty_pill_5: str = "מתיקות"
    specialty_pill_6: str = "תה ירוק"
    highlighted_note: int = 2
    description: str = "תערובת אתיופית\nעם חמיצות עדינה\nוגוף קל ונקי"
    bottom_mode: str = "roast_date"
    roast_date_auto: bool = True
    roast_date: str = ""
    accent_color: str = "#7a2f1d"
    texture_bytes: bytes | None = None


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)

    return Path(__file__).resolve().parent


# ---------- Fonts ----------
FONT_DIR = app_base_dir() / "fonts/Heebo/static"

pdfmetrics.registerFont(
    TTFont("Heebo", str(FONT_DIR / "Heebo-Regular.ttf"))
)
pdfmetrics.registerFont(
    TTFont("HeeboBold", str(FONT_DIR / "Heebo-Bold.ttf"))
)
pdfmetrics.registerFont(
    TTFont("HeeboExtraBold", str(FONT_DIR / "Heebo-ExtraBold.ttf"))
)


def rtl(text: str) -> str:
    return get_display(text)


def draw_center(c, x, y, text, font="Heebo", size=9, is_rtl=False):
    c.setFont(font, size)
    c.drawCentredString(x, y, rtl(text) if is_rtl else text)


def draw_fit_center(c, x, y, text, max_width, font="Heebo", size=9, min_size=5, is_rtl=False):
    display_text = rtl(text) if is_rtl else text
    current_size = size

    while current_size > min_size and pdfmetrics.stringWidth(display_text, font, current_size) > max_width:
        current_size -= 0.25

    c.setFont(font, current_size)
    c.drawCentredString(x, y, display_text)


def fit_font_size(text, max_width, font="Heebo", size=9, min_size=5, is_rtl=False):
    display_text = rtl(text) if is_rtl else text
    current_size = size

    while current_size > min_size and pdfmetrics.stringWidth(display_text, font, current_size) > max_width:
        current_size -= 0.25

    return max(current_size, min_size)


def draw_star(c, x, y, radius):
    points = []
    inner_radius = radius * 0.42

    for index in range(10):
        angle = math.radians(90 + index * 36)
        point_radius = radius if index % 2 == 0 else inner_radius
        points.append((
            x + math.cos(angle) * point_radius,
            y + math.sin(angle) * point_radius,
        ))

    path = c.beginPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    path.close()
    c.drawPath(path, stroke=1, fill=0)


def draw_bean_icon(c, x, y, width, height, mirrored=False):
    c.saveState()
    c.translate(x, y)
    if mirrored:
        c.scale(-1, 1)

    body = c.beginPath()
    body.moveTo(0, height / 2)
    body.curveTo(width * 0.06, height * 0.95, width * 0.48, height * 1.18, width * 0.83, height * 0.86)
    body.curveTo(width * 1.14, height * 0.58, width * 0.96, height * 0.10, width * 0.52, height * 0.02)
    body.curveTo(width * 0.16, height * -0.05, width * -0.04, height * 0.18, 0, height / 2)
    body.close()
    c.drawPath(body, stroke=0, fill=1)

    groove = c.beginPath()
    groove.moveTo(width * 0.42, height * 0.94)
    groove.curveTo(width * 0.34, height * 0.70, width * 0.58, height * 0.58, width * 0.46, height * 0.36)
    groove.curveTo(width * 0.40, height * 0.24, width * 0.46, height * 0.11, width * 0.58, height * 0.04)
    c.setStrokeColor(white)
    c.setLineWidth(max(0.55, width * 0.16))
    c.drawPath(groove, stroke=1, fill=0)
    c.restoreState()


def draw_arc_text(c, x, y, text, radius, start_angle, end_angle, font="Heebo", size=8, clockwise=False):
    if not text:
        return

    chars = list(text)
    if len(chars) == 1:
        angles = [(start_angle + end_angle) / 2]
    else:
        step = (end_angle - start_angle) / (len(chars) - 1)
        angles = [start_angle + step * index for index in range(len(chars))]

    c.saveState()
    c.setFont(font, size)
    for char, angle in zip(chars, angles):
        theta = math.radians(angle)
        char_x = x + math.cos(theta) * radius
        char_y = y + math.sin(theta) * radius
        tangent = angle - 90 if clockwise else angle + 90
        c.saveState()
        c.translate(char_x, char_y)
        c.rotate(tangent)
        c.drawCentredString(0, -size * 0.32, char)
        c.restoreState()
    c.restoreState()


def wrap_text(text, max_width, font, size, is_rtl=False):
    lines = []

    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()

        if not words:
            lines.append("")
            continue

        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            display_candidate = rtl(candidate) if is_rtl else candidate

            if pdfmetrics.stringWidth(display_candidate, font, size) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word

        lines.append(current)

    return lines


def draw_fit_text_block(c, x, center_y, text, max_width, max_height, font="Heebo", size=9, min_size=4.8, is_rtl=False):
    current_size = size

    while current_size >= min_size:
        lines = wrap_text(text, max_width, font, current_size, is_rtl)
        line_height = current_size * 1.28
        too_tall = len(lines) * line_height > max_height
        too_wide = any(
            pdfmetrics.stringWidth(rtl(line) if is_rtl else line, font, current_size) > max_width
            for line in lines
        )

        if not too_tall and not too_wide:
            break

        current_size -= 0.25

    lines = wrap_text(text, max_width, font, max(current_size, min_size), is_rtl)
    current_size = max(current_size, min_size)
    line_height = current_size * 1.28
    total_height = len(lines) * line_height
    y = center_y + total_height / 2 - line_height * 0.82

    c.setFont(font, current_size)
    for line in lines:
        c.drawCentredString(x, y, rtl(line) if is_rtl else line)
        y -= line_height


def safe_hex_color(value: str, fallback="#7a2f1d"):
    if len(value) == 7 and value.startswith("#"):
        try:
            return HexColor(value)
        except ValueError:
            pass

    return HexColor(fallback)


def create_label_pdf(path="coffee_label.pdf", data: LabelData | None = None):
    data = data or LabelData()
    c = canvas.Canvas(path, pagesize=(W, H))

    cx = W / 2

    accent = safe_hex_color(data.accent_color)
    # ---------- Background ----------
    top_y = H * 0.52
    top_h = H - top_y

    if data.texture_bytes:
        c.drawImage(
            ImageReader(BytesIO(data.texture_bytes)),
            0,
            top_y,
            width=W,
            height=top_h,
            preserveAspectRatio=False,
            mask="auto",
        )
    else:
        c.setFillColor(accent)
        c.rect(0, top_y, W, top_h, fill=1, stroke=0)

    # ---------- Main badge ----------
    badge_x = cx
    cy = H * 0.75
    outer_r = 19.5 * mm
    inner_r = 17.7 * mm

    c.setStrokeColor(white if data.specialty_coffee else accent)
    c.setLineWidth(1.3)
    c.circle(badge_x, cy, outer_r, stroke=1, fill=0)

    c.setFillColor(HexColor("#211714") if data.specialty_coffee else white)
    if data.specialty_coffee:
        c.setStrokeColor(white)
        c.setLineWidth(1.1)
        c.circle(badge_x, cy, inner_r, stroke=1, fill=1)
    else:
        c.circle(badge_x, cy, inner_r, stroke=0, fill=1)

    # ---------- Badge text ----------
    badge_text_width = inner_r * 1.45
    if data.specialty_coffee:
        c.setFillColor(white)
        draw_arc_text(c, badge_x, cy, "specialty", 11.7 * mm, 130, 50, "Heebo", 9.1, True)
        center_size = min(
            fit_font_size(data.badge_line_1, badge_text_width, "HeeboExtraBold", 14.2, 7.2, True),
            fit_font_size(data.badge_line_2, badge_text_width, "HeeboExtraBold", 14.2, 7.2, True),
        )
        c.setFont("HeeboExtraBold", center_size)
        c.drawCentredString(badge_x, cy + 2.5 * mm, rtl(data.badge_line_1))
        c.drawCentredString(badge_x, cy - 4.3 * mm, rtl(data.badge_line_2))
        draw_arc_text(c, badge_x, cy, "Coffee", 12 * mm, 235, 305, "Heebo", 8.1, False)

        if data.cup_score.strip():
            score_w = 36 * mm
            score_h = 6 * mm
            score_x = cx - score_w / 2
            score_y = 50.7 * mm
            c.setFillColor(white)
            c.setStrokeColor(HexColor("#211714"))
            c.setLineWidth(0.7)
            c.roundRect(score_x, score_y, score_w, score_h, 1.8 * mm, stroke=1, fill=1)
            c.saveState()
            c.setStrokeColor(HexColor("#211714"))
            c.setLineWidth(0.35)
            c.setDash(0.55 * mm, 0.35 * mm)
            inset = 0.75 * mm
            c.roundRect(
                score_x + inset,
                score_y + inset,
                score_w - inset * 2,
                score_h - inset * 2,
                1.25 * mm,
                stroke=1,
                fill=0,
            )
            c.restoreState()
            c.setFillColor(HexColor("#211714"))
            c.setFont("Heebo", 8.5)
            c.drawCentredString(cx, score_y + 1.55 * mm, f"Cup Score {data.cup_score.strip()}")
            c.setStrokeColor(HexColor("#211714"))
            c.setLineWidth(0.55)
            draw_star(c, score_x + 4.2 * mm, score_y + score_h / 2, 2.2 * mm)
            draw_star(c, score_x + score_w - 4.2 * mm, score_y + score_h / 2, 2.2 * mm)
    elif data.badge_line_2.strip():
        c.setFillColor(black)
        draw_fit_center(c, badge_x, cy + 6 * mm, data.badge_line_1, badge_text_width, "HeeboExtraBold", 14.4, 7.2, True)
        draw_fit_center(c, badge_x, cy + 0.1 * mm, data.badge_line_2, badge_text_width, "Heebo", 11.2, 6.2, True)
        draw_fit_center(c, badge_x, cy - 5.8 * mm, data.roast_level, badge_text_width, "Heebo", 8.8, 5.4, True)
    else:
        c.setFillColor(black)
        draw_fit_center(c, badge_x, cy + 3.8 * mm, data.badge_line_1, badge_text_width, "HeeboExtraBold", 16.4, 7.2, True)
        draw_fit_center(c, badge_x, cy - 5.5 * mm, data.roast_level, badge_text_width, "Heebo", 10.2, 5.4, True)

    # ---------- Pills ----------
    pill_pad = 3.2 * mm

    def pill_font_size(text, w, h, max_size=11.2, min_size=6.1, font="HeeboExtraBold"):
        current_size = max_size
        display_text = rtl(text)

        while current_size > min_size:
            text_w = pdfmetrics.stringWidth(display_text, font, current_size)
            if text_w <= w - pill_pad * 1.35 and current_size <= h * 0.56:
                break

            current_size -= 0.25

        return max(current_size, min_size)

    def pill(x, y, w, h, text, fill=False, font="HeeboExtraBold", max_size=11.2):
        font_size = pill_font_size(text, w, h, max_size=max_size, font=font)
        c.setStrokeColor(accent)
        c.setLineWidth(0.8)

        if fill:
            c.setFillColor(accent)
            text_color = white
        else:
            c.setFillColor(white)
            text_color = accent

        c.roundRect(x, y, w, h, h / 2, stroke=1, fill=1)

        c.setFillColor(text_color)
        c.setFont(font, font_size)
        c.drawCentredString(x + w / 2, y + h / 2 - font_size * 0.34, rtl(text))

    if data.specialty_coffee:
        c.setFillColor(black)
        draw_fit_center(c, cx, 45.6 * mm, f"זן: {data.specialty_variety}".strip(), W - 10 * mm, "Heebo", 10.2, 6.2, True)
        draw_fit_center(c, cx, 41.1 * mm, f"עיבוד: {data.specialty_process}".strip(), W - 10 * mm, "Heebo", 10.2, 6.2, True)

        specialty_pills = [
            data.specialty_pill_1,
            data.specialty_pill_2,
            data.specialty_pill_3,
            data.specialty_pill_4,
            data.specialty_pill_5,
            data.specialty_pill_6,
        ]
        specialty_rows = [specialty_pills[index:index + 2] for index in range(0, 6, 2)]
        row_w = W - 6 * mm
        gap = 3.5 * mm
        pill_h = 5.8 * mm
        row_y = 32.3 * mm

        for row in specialty_rows:
            weights = [
                max(1, pdfmetrics.stringWidth(rtl(text), "Heebo", 9.6) + pill_pad)
                for text in row
            ]
            available_w = row_w - gap
            min_w = 12 * mm
            first_w = max(min_w, available_w * weights[0] / sum(weights))
            second_w = available_w - first_w
            if second_w < min_w:
                second_w = min_w
                first_w = available_w - second_w

            x = (W - row_w) / 2
            pill(x, row_y, second_w, pill_h, row[1], False, "Heebo", 9.6)
            pill(x + second_w + gap, row_y, first_w, pill_h, row[0], False, "Heebo", 9.6)
            row_y -= 7.3 * mm
    else:
        py = 46.5 * mm
        pill_h = 6.6 * mm
        pill_gap = 1.5 * mm
        max_group_w = W - 5 * mm
        row_pill_font_size = 11.2
        notes = [data.note_1, data.note_2, data.note_3]

        while True:
            pill_widths = [
                max(11 * mm, pdfmetrics.stringWidth(rtl(note), "HeeboExtraBold", row_pill_font_size) + pill_pad * 2)
                for note in notes
            ]
            group_w = sum(pill_widths) + pill_gap * 2

            if group_w <= max_group_w or row_pill_font_size <= 6.1:
                break

            row_pill_font_size -= 0.25

        x = (W - group_w) / 2
        for index, (note, pill_w) in enumerate(zip(notes, pill_widths), start=1):
            pill(x, py, pill_w, pill_h, note, data.highlighted_note == index)
            x += pill_w + pill_gap

        # ---------- Description ----------
        c.setFillColor(black)
        draw_fit_text_block(
            c,
            cx,
            H * 0.34,
            data.description,
            W - 8 * mm,
            22 * mm,
            "Heebo",
            9.2,
            4.7,
            True,
        )

    # ---------- Bottom text ----------
    if data.bottom_mode == "surprise":
        draw_fit_center(c, cx, 12 * mm, "הפתעה!", W - 10 * mm, "HeeboExtraBold", 24, 10, True)
    else:
        c.setFillColor(black)
        date_title_y = 12.6 * mm if data.specialty_coffee else 18 * mm
        date_y = 9.1 * mm if data.specialty_coffee else 14 * mm
        draw_center(c, cx, date_title_y, "תאריך קלייה", "HeeboBold", 9.5, True)
        if data.roast_date:
            draw_center(c, cx, date_y, data.roast_date, "HeeboBold", 10, False)
        if data.specialty_coffee:
            draw_center(c, cx, 5.4 * mm, "הקפה ארוז באריזת נשם וטוב לטחינה עד", "Heebo", 5.4, True)
            draw_center(c, cx, 3.0 * mm, "שנתיים מיום הקלייה", "Heebo", 5.4, True)
        else:
            draw_center(c, cx, 9 * mm, "הקפה ארוז באריזת נשם וטוב לטחינה עד", "Heebo", 5.8, True)
            draw_center(c, cx, 6.2 * mm, "שנתיים מיום הקלייה", "Heebo", 5.8, True)

    c.showPage()
    c.save()


if __name__ == "__main__":
    create_label_pdf("coffee_label.pdf")
