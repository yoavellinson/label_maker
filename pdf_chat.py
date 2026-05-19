from pathlib import Path
from dataclasses import dataclass
from io import BytesIO

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
    note_1: str = "פירותי"
    note_2: str = "פרחוני"
    note_3: str = "מתוק"
    highlighted_note: int = 2
    description: str = "תערובת אתיופית\nעם חמיצות עדינה\nוגוף קל ונקי"
    bottom_mode: str = "roast_date"
    roast_date: str = ""
    accent_color: str = "#7a2f1d"
    texture_bytes: bytes | None = None


# ---------- Fonts ----------
FONT_DIR = Path("fonts/Heebo/static")

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

    c.setStrokeColor(accent)
    c.setLineWidth(1.3)
    c.circle(badge_x, cy, outer_r, stroke=1, fill=0)

    c.setFillColor(white)
    c.circle(badge_x, cy, inner_r, stroke=0, fill=1)

    # ---------- Badge text ----------
    c.setFillColor(black)
    badge_text_width = inner_r * 1.45
    if data.badge_line_2.strip():
        draw_fit_center(c, badge_x, cy + 6 * mm, data.badge_line_1, badge_text_width, "HeeboExtraBold", 14.4, 7.2, True)
        draw_fit_center(c, badge_x, cy + 0.1 * mm, data.badge_line_2, badge_text_width, "Heebo", 11.2, 6.2, True)
        draw_fit_center(c, badge_x, cy - 5.8 * mm, data.roast_level, badge_text_width, "Heebo", 8.8, 5.4, True)
    else:
        draw_fit_center(c, badge_x, cy + 3.8 * mm, data.badge_line_1, badge_text_width, "HeeboExtraBold", 16.4, 7.2, True)
        draw_fit_center(c, badge_x, cy - 5.5 * mm, data.roast_level, badge_text_width, "Heebo", 10.2, 5.4, True)

    # ---------- Pills ----------
    def pill(x, y, w, h, text, fill=False, font_size=7.2):
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
        c.setFont("HeeboBold", font_size)
        c.drawCentredString(x + w / 2, y + h / 2 - 2.2, rtl(text))

    py = H * 0.49
    pill_h = 5.2 * mm
    pill_gap = 1.3 * mm
    pill_pad = 2.4 * mm
    max_group_w = W - 5 * mm
    pill_font_size = 7.5
    notes = [data.note_1, data.note_2, data.note_3]

    while True:
        pill_widths = [
            max(9.5 * mm, pdfmetrics.stringWidth(rtl(note), "HeeboBold", pill_font_size) + pill_pad * 2)
            for note in notes
        ]
        group_w = sum(pill_widths) + pill_gap * 2

        if group_w <= max_group_w or pill_font_size <= 5.3:
            break

        pill_font_size -= 0.25

    x = (W - group_w) / 2
    for index, (note, pill_w) in enumerate(zip(notes, pill_widths), start=1):
        pill(x, py, pill_w, pill_h, note, data.highlighted_note == index, pill_font_size)
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
        draw_center(c, cx, 12 * mm, "הפתעה!", "HeeboBold", 13, True)
    else:
        draw_center(c, cx, 18 * mm, "תאריך קלייה", "HeeboBold", 9.5, True)
        if data.roast_date:
            draw_center(c, cx, 14 * mm, data.roast_date, "HeeboBold", 10, False)
        draw_center(c, cx, 9 * mm, "הקפה ארוז באריזת נשם וטוב לטחינה עד", "Heebo", 5.8, True)
        draw_center(c, cx, 6.2 * mm, "שנתיים מיום הקלייה", "Heebo", 5.8, True)

    c.showPage()
    c.save()


if __name__ == "__main__":
    create_label_pdf("coffee_label.pdf")
