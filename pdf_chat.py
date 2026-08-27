from pathlib import Path
from dataclasses import dataclass
from io import BytesIO
import sys

from bidi.algorithm import get_display
from reportlab.lib.colors import HexColor, black
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


PAGE_SIZE = 349.465
W, H = PAGE_SIZE, PAGE_SIZE


@dataclass(frozen=True)
class LabelData:
    badge_line_1: str = "אתיופיה דג׳ימה"
    badge_line_2: str = "100% ערביקה"
    roast_level: str = "קלייה בינונית כהה"
    specialty_coffee: bool = False
    cup_score: str = ""
    note_1: str = "חזק"
    note_2: str = "אש"
    note_3: str = "מעושן"
    specialty_variety: str = ""
    specialty_process: str = "חצי שטוף"
    specialty_pill_1: str = ""
    specialty_pill_2: str = ""
    specialty_pill_3: str = ""
    specialty_pill_4: str = ""
    specialty_pill_5: str = ""
    specialty_pill_6: str = ""
    highlighted_note: int = 2
    description: str = ""
    bottom_mode: str = "roast_date"
    roast_date_auto: bool = True
    roast_date: str = ""
    accent_color: str = "#7a2f1d"
    texture_bytes: bytes | None = None
    grid_background: str = "classic"
    weight: str = "1"


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)

    return Path(__file__).resolve().parent


FONT_DIR = app_base_dir() / "fonts"

pdfmetrics.registerFont(TTFont("KarantinaLight", str(FONT_DIR / "Karantina/static/Karantina-Light.ttf")))
pdfmetrics.registerFont(TTFont("Karantina", str(FONT_DIR / "Karantina/static/Karantina-Regular.ttf")))
pdfmetrics.registerFont(TTFont("KarantinaBold", str(FONT_DIR / "Karantina/static/Karantina-Bold.ttf")))


BACKGROUND_STYLES = {
    "classic": {
        "label": "קלאסי",
        "image": "grid/backgrounds/classic.png",
        "bar": "#1d3327",
        "body": "#efd39d",
    },
    "fruity": {
        "label": "פירותי",
        "image": "grid/backgrounds/fruity.png",
        "bar": "#f03f7c",
        "body": "#efd39d",
    },
    "chocolate": {
        "label": "שוקולדי",
        "image": "grid/backgrounds/chocolate.png",
        "bar": "#7f4a39",
        "body": "#efd39d",
    },
}


def rtl(text: str) -> str:
    return get_display(text or "")


def fit_size(text, font, start, max_width, min_size=8, is_rtl=True):
    display_text = rtl(text) if is_rtl else text
    size = start
    while size > min_size and pdfmetrics.stringWidth(display_text, font, size) > max_width:
        size -= 0.5
    return max(size, min_size)


def draw_fit(c, x, y, text, font, size, max_width, min_size=8, is_rtl=True, align="center"):
    display_text = rtl(text) if is_rtl else text
    size = fit_size(text, font, size, max_width, min_size, is_rtl)
    c.setFont(font, size)
    if align == "left":
        c.drawString(x, y, display_text)
    elif align == "right":
        c.drawRightString(x, y, display_text)
    else:
        c.drawCentredString(x, y, display_text)


def background_style(name: str):
    return BACKGROUND_STYLES.get(name) or BACKGROUND_STYLES["classic"]


def draw_background(c, data: LabelData):
    style = background_style(data.grid_background)
    image_path = app_base_dir() / style["image"]
    if image_path.exists():
        c.drawImage(ImageReader(str(image_path)), 0, 0, width=W, height=H, preserveAspectRatio=False, mask="auto")

    c.setFillColor(HexColor(style["bar"]))
    c.rect(33, 119.5, 283.5, 23.5, stroke=0, fill=1)

    c.setFillColor(HexColor(style["body"]))
    c.rect(33, 33, 283.5, 86.5, stroke=0, fill=1)

    c.setStrokeColor(black)
    c.setLineWidth(0.55)
    c.line(33, 73.5, 316.5, 73.5)
    c.line(33, 60.7, 316.5, 60.7)
    c.line(233.5, 33, 233.5, 119.5)
    c.line(271.5, 33, 271.5, 119.5)

    c.setStrokeColor(HexColor(style["bar"]))
    c.setLineWidth(2.0)
    c.circle(252.5, 96.5, 10.5, stroke=1, fill=0)
    c.setLineWidth(0.75)
    c.circle(265, 96.5, 3.2, stroke=1, fill=0)


def tasting_line(data: LabelData) -> str:
    parts = [data.note_1, data.note_2, data.note_3]
    notes = ", ".join(part for part in parts if part.strip())
    return " | ".join(part for part in [notes, data.roast_level] if part.strip())


def process_line(data: LabelData) -> str:
    process = data.specialty_process or "חצי שטוף"
    return f"עיבוד: {process} | כשר בהשגחת הרבנות חתם סופר"


def create_label_pdf(path="coffee_label.pdf", data: LabelData | None = None, include_background=True):
    data = data or LabelData()
    c = canvas.Canvas(path, pagesize=(W, H))
    style = background_style(data.grid_background)

    if include_background:
        draw_background(c, data)

    c.setFillColor(black)
    draw_fit(c, 49, 126.5, style["label"], "Karantina", 13.5, 92, 9, True, "left")

    title = data.badge_line_1 or data.badge_line_2
    title_is_latin = all(ord(char) < 128 for char in title)
    draw_fit(c, 36, 87.5, title, "KarantinaLight", 49, 194, 24, not title_is_latin, "left")

    draw_fit(c, 222, 63.5, tasting_line(data), "Karantina", 13.5, 178, 8, True, "right")
    draw_fit(c, 222, 49.0, process_line(data), "KarantinaLight", 13.5, 178, 8, True, "right")

    if data.specialty_coffee and data.cup_score.strip():
        c.setFillColor(HexColor("#ef3524"))
        c.rect(212, 119.5, 104.5, 23.5, stroke=0, fill=1)
        c.setFillColor(HexColor("#fff4d2"))
        draw_fit(
            c,
            264,
            127.2,
            f"SPECIALTY COFFEE {data.cup_score.strip()}",
            "KarantinaLight",
            19.2,
            94,
            10,
            False,
            "center",
        )

    c.setFillColor(black)
    draw_fit(c, 309.5, 89, data.weight or "1", "KarantinaLight", 49, 30, 18, False, "right")
    c.setFont("KarantinaLight", 13.5)
    c.drawString(286.5, 78.5, rtl('ק״ג'))

    draw_fit(c, 308, 62.5, "תאריך קלייה:", "Karantina", 13.5, 68, 8, True, "right")
    if data.roast_date:
        draw_fit(c, 308, 48.5, data.roast_date, "KarantinaLight", 18, 68, 9, False, "right")

    c.showPage()
    c.save()
