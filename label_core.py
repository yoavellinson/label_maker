"""Label model, measured layout engine and PDF rendering for the grid labels.

Everything that decides where text goes lives here, so the on-screen preview
(a rasterised copy of the print PDF) and the printed PDF can never disagree.

Coordinates are millimetres from the sticker's top-left corner.
"""

from __future__ import annotations

import copy
from io import BytesIO
from pathlib import Path

from bidi.algorithm import get_display
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

try:
    import pypdfium2 as pdfium
except ImportError:  # pragma: no cover - packaged builds always include it
    pdfium = None


MM = 72 / 25.4
MASTER_PDF_NAME = "final_stickers.con_16.8.26 (1).pdf"

# The six production designs inside the master PDF.
PAGES = {
    4: {"label": "שוקולדי", "specialty": False, "color": "#7a4a3a"},
    5: {"label": "פירותי", "specialty": False, "color": "#ef6f8a"},
    6: {"label": "פורטה", "specialty": False, "color": "#1f3a2e"},
    10: {"label": "ספיישלטי שוקולדי", "specialty": True, "color": "#7a4a3a"},
    11: {"label": "ספיישלטי פירותי", "specialty": True, "color": "#ef6f8a"},
    12: {"label": "ספיישלטי פורטה", "specialty": True, "color": "#1f3a2e"},
}

SINGLE_BEANS = ["ערביקה", "רובוסטה"]
BLEND_ARABICA = ["30", "50", "70", "100"]

# Geometry measured from the production PDF (grid lines, strip text margin,
# "תאריך קלייה:" box) and from the master typography guide. Every value can be
# changed in designer mode and is stored in the database.
DEFAULT_LAYOUT = {
    "margin_left": 3.4,
    "text_right": 70.6,  # the vertical rule of the text column is at 72 mm
    "title": {"y": 78.3, "size": 49, "weight": 300, "kerning": 10, "min_size": 28},
    "rows": [
        {"y": 89.25, "size": 13.5, "weight": 400, "kerning": 30, "items": ["roast", "tastes"]},
        {"y": 96.0, "size": 13.5, "weight": 300, "kerning": 30, "items": ["kosher", "process", "bean"]},
    ],
    "row_min_size": 9,
    "separator": {"gap": 1.2, "weight": 300},
    "weight": {"x": 95.6, "y": 77.6, "size": 49, "weight": 300, "kerning": 0},
    "weight_fraction": {"x": 96.4, "y": 78.0, "size": 29, "weight": 300, "kerning": 0},
    "weight_unit": {"x": 92.1, "y": 82.0, "size": 13.5, "weight": 300, "kerning": 30},
    "date": {"x": 88.75, "y": 95.6, "size": 16, "weight": 400, "kerning": 30},
    "score": {"x": 87.4, "y": 66.5, "size": 22, "weight": 300, "kerning": 40},
}

DEFAULT_CALIBRATION = {
    "sticker_w": 100.0,
    "sticker_h": 100.0,
    "bg_x": -11.65,
    "bg_y": -11.65,
    "bg_scale": 1.0,
    "rotation": 0,
}

ROW_ITEM_LABELS = {
    "roast": "קלייה",
    "tastes": "טעמים",
    "bean": "סוג פולים",
    "process": "עיבוד",
    "kosher": "כשרות",
}

KOSHER_TEXT = "כשרות חתם סופר"
DB_VERSION = 4

ROW_GROUP_LABELS = ["השורה העליונה", "השורה התחתונה"]

EMPTY_PRODUCT = {
    "title": "",
    "tastes": "",
    "roast": "",
    "bean": "single:ערביקה",
    "process": "",
    "kosher": KOSHER_TEXT,
    "score": "",
    "page": 6,
}


_fonts_registered = False


def register_fonts(font_dir: Path) -> None:
    global _fonts_registered
    if _fonts_registered:
        return
    static = font_dir / "Karantina" / "static"
    pdfmetrics.registerFont(TTFont("KarantinaLight", str(static / "Karantina-Light.ttf")))
    pdfmetrics.registerFont(TTFont("Karantina", str(static / "Karantina-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("KarantinaBold", str(static / "Karantina-Bold.ttf")))
    _fonts_registered = True


def font_for_weight(weight) -> str:
    try:
        value = int(weight)
    except (TypeError, ValueError):
        value = 400
    if value >= 700:
        return "KarantinaBold"
    if value <= 300:
        return "KarantinaLight"
    return "Karantina"


def merged_layout(stored: dict | None) -> dict:
    """Defaults overlaid with whatever the designer saved (missing keys fall back)."""
    layout = copy.deepcopy(DEFAULT_LAYOUT)
    for key, value in (stored or {}).items():
        if isinstance(value, dict) and isinstance(layout.get(key), dict):
            layout[key].update(value)
        else:
            layout[key] = copy.deepcopy(value)
    return layout


def merged_calibration(stored: dict | None) -> dict:
    calibration = dict(DEFAULT_CALIBRATION)
    calibration.update(stored or {})
    return calibration


# --- text content -----------------------------------------------------------

def bean_text(code: str) -> str:
    kind, _, value = str(code or "").partition(":")
    if kind == "blend" and value:
        return f"תערובת {value}% ערביקה"
    if kind == "single" and value:
        return f"חד זני - {value}"
    return ""


def pretty_weight(value: str) -> str:
    compact = str(value or "").replace(" ", "").replace("ק״ג", "").replace("קג", "")
    if compact in {"0.25", ".25", "1/4", "¼"}:
        return "¼"
    if compact in {"0.5", ".5", "1/2", "½"}:
        return "½"
    if compact in {"", "1", "1.0"}:
        return "1"
    return compact


def row_segments(product: dict) -> dict[str, str]:
    process = str(product.get("process") or "").strip()
    return {
        "roast": str(product.get("roast") or "").strip(),
        "tastes": str(product.get("tastes") or "").strip(),
        "bean": bean_text(product.get("bean", "")),
        "process": f"עיבוד: {process}" if process else "",
        "kosher": str(product.get("kosher") or "").strip(),
    }


# --- measurement and composition -------------------------------------------

def text_width_mm(text: str, font: str, size: float, kerning: float) -> float:
    display = get_display(text)
    if not display:
        return 0.0
    width = pdfmetrics.stringWidth(display, font, size)
    width += (len(display) - 1) * size * kerning / 1000
    return width / MM


def make_field(text: str, left: float, y: float, size: float, weight, kerning: float, role: str) -> dict:
    font = font_for_weight(weight)
    return {
        "role": role,
        "text": text,
        "x": left,
        "y": y,
        "size": size,
        "font": font,
        "kerning": kerning,
        "width": text_width_mm(text, font, size, kerning),
    }


def centered_field(text: str, spec: dict, role: str) -> dict:
    font = font_for_weight(spec["weight"])
    width = text_width_mm(text, font, spec["size"], spec["kerning"])
    return make_field(text, spec["x"] - width / 2, spec["y"], spec["size"], spec["weight"], spec["kerning"], role)


def compose(product: dict, daily: dict, layout: dict) -> list[dict]:
    """Turn product content + today's values into positioned text fields."""
    fields: list[dict] = []
    page = int(product.get("page") or 6)
    specialty = PAGES.get(page, {}).get("specialty", False)
    left = float(layout["margin_left"])
    max_width = float(layout["text_right"]) - left

    title = str(product.get("title") or "").strip()
    if title:
        spec = layout["title"]
        font = font_for_weight(spec["weight"])
        size = float(spec["size"])
        width = text_width_mm(title, font, size, spec["kerning"])
        if width > max_width:
            size = max(float(spec.get("min_size", 20)), size * max_width / width)
        field = make_field(title, left, spec["y"], size, spec["weight"], spec["kerning"], "title")
        field["shrunk"] = size < float(spec["size"])
        field["group"] = "שם הקפה"
        fields.append(field)

    segments = row_segments(product)
    for index, row in enumerate(layout["rows"]):
        texts = [(item, segments.get(item, "")) for item in row.get("items", [])]
        texts = [(item, text) for item, text in texts if text]
        if not texts:
            continue
        group = ROW_GROUP_LABELS[index] if index < len(ROW_GROUP_LABELS) else f"שורה {index + 1}"
        for field in compose_row(texts, row, layout, left, max_width):
            field["group"] = group
            fields.append(field)

    if not specialty:
        # The specialty designs have "1 ק״ג" preprinted.
        weight = pretty_weight(daily.get("weight", "1"))
        spec = layout["weight_fraction"] if weight in {"½", "¼"} else layout["weight"]
        fields.append(centered_field(weight, spec, "weight"))
        fields.append(centered_field("ק״ג", layout["weight_unit"], "weight_unit"))

    date = str(daily.get("date") or "").strip()
    if date:
        fields.append(centered_field(date, layout["date"], "date"))

    score = str(product.get("score") or "").strip()
    if specialty and score:
        spec = layout["score"]
        fields.append(make_field(score, spec["x"], spec["y"], spec["size"], spec["weight"], spec["kerning"], "score"))

    return fields


def compose_row(texts: list[tuple[str, str]], row: dict, layout: dict, left: float, max_width: float) -> list[dict]:
    sep = layout["separator"]
    gap = float(sep["gap"])
    font = font_for_weight(row["weight"])
    sep_font = font_for_weight(sep.get("weight", row["weight"]))
    kerning = float(row["kerning"])

    # Text and pipe widths scale with the font size; the gaps around pipes do not.
    size = float(row["size"])
    fixed = (len(texts) - 1) * 2 * gap
    scalable = sum(text_width_mm(text, font, size, kerning) for _, text in texts)
    scalable += (len(texts) - 1) * text_width_mm("|", sep_font, size, 0)
    if fixed + scalable > max_width:
        fitted = size * max(max_width - fixed, 0) / scalable * 0.999
        size = max(float(layout.get("row_min_size", 9)), fitted)

    fields = []
    x = left
    for index, (item, text) in enumerate(texts):
        if index:
            x += gap
            pipe = make_field("|", x, row["y"], size, sep.get("weight", row["weight"]), 0, "separator")
            fields.append(pipe)
            x += pipe["width"] + gap
        field = make_field(text, x, row["y"], size, row["weight"], kerning, item)
        field["shrunk"] = size < float(row["size"])
        fields.append(field)
        x += field["width"]
    return fields


def shrunk_groups(fields: list[dict]) -> list[str]:
    return sorted({f["group"] for f in fields if f.get("shrunk")})


def overflowing(fields: list[dict], layout: dict) -> list[str]:
    """Groups that still cross the text column after shrinking to the minimum size."""
    limit = float(layout["text_right"]) + 0.05
    return sorted({f["group"] for f in fields if f.get("group") and f["x"] + f["width"] > limit})


# --- drawing ----------------------------------------------------------------

def draw_fields(c: canvas.Canvas, fields: list[dict], sticker_h_mm: float, color: str = "#111111") -> None:
    c.setFillColor(HexColor(color))
    height_pt = sticker_h_mm * MM
    for field in fields:
        display = get_display(field["text"])
        if not display:
            continue
        size = field["size"]
        font = field["font"]
        ascent = pdfmetrics.getAscent(font, size)
        descent = pdfmetrics.getDescent(font, size)
        baseline = height_pt - field["y"] * MM - (ascent + descent) / 2
        text = c.beginText(field["x"] * MM, baseline)
        text.setFont(font, size)
        text.setCharSpace(size * field["kerning"] / 1000)
        text.textLine(display)
        c.drawText(text)


def label_pdf(fields: list[dict], calibration: dict, copies: int = 1, background_png: bytes | None = None) -> bytes:
    width = calibration["sticker_w"] * MM
    height = calibration["sticker_h"] * MM
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))
    c.setTitle("label")
    rotation = int(calibration.get("rotation") or 0) % 360
    for _ in range(max(1, min(int(copies or 1), 500))):
        if rotation:
            c.setPageRotation(rotation)
        if background_png:
            from reportlab.lib.utils import ImageReader

            c.drawImage(ImageReader(BytesIO(background_png)), 0, 0, width=width, height=height)
        draw_fields(c, fields, calibration["sticker_h"])
        c.showPage()
    c.save()
    return buffer.getvalue()


def pdf_to_png(pdf_bytes: bytes, px_per_mm: float, transparent: bool = True) -> bytes:
    if pdfium is None:
        raise RuntimeError("pypdfium2 is not installed.")
    document = pdfium.PdfDocument(pdf_bytes)
    try:
        page = document[0]
        try:
            bitmap = page.render(
                scale=px_per_mm / MM,
                fill_color=(255, 255, 255, 0) if transparent else (255, 255, 255, 255),
            )
            image = bitmap.to_pil()
            out = BytesIO()
            image.save(out, format="PNG")
            return out.getvalue()
        finally:
            page.close()
    finally:
        document.close()


def background_png(master_pdf: Path, page: int, calibration: dict, px_per_mm: float) -> bytes:
    """Render one design page cropped to the sticker using the calibration offset."""
    if pdfium is None:
        raise RuntimeError("pypdfium2 is not installed.")
    from PIL import Image

    document = pdfium.PdfDocument(str(master_pdf))
    try:
        if page < 1 or page > len(document):
            raise RuntimeError(f"Page {page} is not in the master PDF.")
        pdf_page = document[page - 1]
        try:
            scale = float(calibration.get("bg_scale") or 1)
            rendered = pdf_page.render(scale=px_per_mm * scale / MM).to_pil().convert("RGB")
        finally:
            pdf_page.close()
    finally:
        document.close()

    w = round(calibration["sticker_w"] * px_per_mm)
    h = round(calibration["sticker_h"] * px_per_mm)
    canvas_img = Image.new("RGB", (w, h), "white")
    canvas_img.paste(rendered, (round(calibration["bg_x"] * px_per_mm), round(calibration["bg_y"] * px_per_mm)))
    out = BytesIO()
    canvas_img.save(out, format="PNG")
    return out.getvalue()


# --- migration from the v2 preset database --------------------------------

def _classify(item: dict) -> str:
    text = str(item.get("text") or "").strip()
    if not text or text == "|":
        return ""
    if text.startswith("חד זני") or text.startswith("תערובת"):
        return "bean"
    if text.startswith("עיבוד"):
        return "process"
    role = item.get("role") or ""
    if role in {"roast_date", "weight_value", "weight_unit", "separator", "origin"}:
        return ""
    if "כשר" in text:
        return "kosher"
    return {"title": "title", "tastes": "tastes", "roast_level": "roast", "cup_score": "score"}.get(role, "")


def _parse_bean(text: str) -> str:
    for value in BLEND_ARABICA:
        if text.startswith("תערובת") and f"{value}%" in text:
            return f"blend:{value}"
    if "רובוסטה" in text:
        return "single:רובוסטה"
    return "single:ערביקה"


def product_from_preset(record: dict) -> dict:
    editor = (record.get("layout") or {}).get("editor_state") or {}
    product = dict(EMPTY_PRODUCT)
    page = editor.get("originalPdfPage") or editor.get("page") or 6
    product["page"] = int(page) if int(page) in PAGES else 6
    for item in editor.get("texts", []):
        kind = _classify(item)
        text = str(item.get("text") or "").strip()
        if not kind or product.get(f"_seen_{kind}"):
            continue
        product[f"_seen_{kind}"] = True
        if kind == "bean":
            product["bean"] = _parse_bean(text)
        elif kind == "process":
            product["process"] = text.replace("עיבוד:", "").strip()
        else:
            product[kind] = text
    for key in [k for k in product if k.startswith("_seen_")]:
        del product[key]
    return product


def normalize_kosher(text: str) -> str:
    """All Hatam Sofer wordings print as the standard KOSHER_TEXT."""
    return KOSHER_TEXT if "חתם סופר" in str(text or "") else str(text or "").strip()


def upgrade_db(db: dict) -> bool:
    """Bring a stored v3 database up to DB_VERSION. Returns True if it changed."""
    if int(db.get("version") or 3) >= DB_VERSION:
        return False
    for product in db.get("products", []):
        product["kosher"] = normalize_kosher(product.get("kosher"))
    rows = (db.get("layout") or {}).get("rows")
    if isinstance(rows, list) and len(rows) > 1 and rows[1].get("items") == ["bean", "process", "kosher"]:
        rows[1]["items"] = ["kosher", "process", "bean"]
    db["version"] = DB_VERSION
    return True


def migrate_presets(presets_db: dict) -> dict:
    """Build a v3 database from the v2 named-preset database."""
    products = []
    offsets = []
    for record in presets_db.get("layouts", []):
        product = product_from_preset(record)
        product["kosher"] = normalize_kosher(product["kosher"])
        product["id"] = record.get("id")
        product["updated_at"] = record.get("updated_at", "")
        if product["title"]:
            products.append(product)
        background = ((record.get("layout") or {}).get("editor_state") or {}).get("background") or {}
        if "x" in background and "y" in background:
            offsets.append((round(float(background["x"]), 2), round(float(background["y"]), 2)))
    calibration = dict(DEFAULT_CALIBRATION)
    if offsets:
        x, y = max(set(offsets), key=offsets.count)
        calibration["bg_x"], calibration["bg_y"] = x, y
    return {
        "version": DB_VERSION,
        "layout": {},
        "calibration": calibration,
        "products": products,
    }
