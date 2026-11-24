"""Full-print 50x100 mm portrait label in the v2 brand style.

Unlike the preprinted 100x100 stickers, everything here is printed by the app
(colour printer): the design's illustration cropped from the master PDF, the
coloured strip, the cream body, grid rules, cup icon and all text.

The body is a flow layout: every text block takes exactly the height its ink
needs plus a uniform padding, and the illustration absorbs whatever height is
left, so there is no dead space whatever the content length.

Coordinates are millimetres from the label's top-left corner.
"""

from __future__ import annotations

import copy
from io import BytesIO
from pathlib import Path

from bidi.algorithm import get_display
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

import label_core as core

MM = core.MM
ART_PX_PER_MM = 12  # ~300 dpi
ART_SOURCE_H = 62.2  # illustration height above the strip on the 100x100 design

# Ink extents of Karantina Hebrew around the drawing centre line, in em
# (measured): letters reach 0.27 em above it and 0.36 em below.
INK_ABOVE = 0.27
INK_BELOW = 0.36

# Colours sampled from the production PDF.
DESIGN_COLORS = {4: "#7d4939", 5: "#ff647e", 6: "#223529"}
BODY_COLOR = "#f7d4a6"
SPECIALTY_BODY_COLOR = "#dde4cd"
SPECIALTY_ORANGE = "#ff4716"
CREAM = "#fef1d9"
INK = "#111111"
RULE = "#231f20"

# Horizontal centre of the illustration's subject on each design (sticker mm).
ART_FOCUS_X = {4: 72.0, 5: 50.0, 6: 70.0}

DEFAULT_FULL_LAYOUT = {
    "margin": 2.8,
    "pad": 1.7,
    "art_min": 26.0,
    "art_max": 56.0,
    "art_zoom": 0.78,
    "strip_h": 7.0,
    "strip_text": {"size": 18, "weight": 700, "kerning": 20},
    "specialty": {"size": 12.5, "weight": 300, "kerning": 40},
    "title": {"size": 36, "weight": 300, "kerning": 10, "min_size": 25, "two_line_size": 28, "line_gap": 1.4},
    "rows": [
        {"size": 12.5, "weight": 400, "kerning": 30, "items": ["roast", "tastes"]},
        {"size": 12.5, "weight": 300, "kerning": 30, "items": ["kosher", "process", "bean"]},
    ],
    "row_gap": 1.5,
    "separator": {"gap": 1.0, "weight": 300},
    "description": {"size": 11, "min_size": 7.5, "weight": 400, "kerning": 10, "line_gap": 0.75},
    "band_h": 12.5,
    "date_cell_right": 27.0,
    "cup_cell_right": 38.0,
    "date_label": {"size": 12, "weight": 400, "kerning": 30},
    "date": {"size": 15, "weight": 400, "kerning": 30},
    "surprise": {"size": 30, "weight": 700, "kerning": 10},
    "weight": {"size": 34, "weight": 300},
    "weight_fraction_size": 22,
    "weight_unit": {"size": 10, "weight": 300},
}

DEFAULT_FULL_CALIBRATION = {"sticker_w": 50.0, "sticker_h": 100.0, "rotation": 0}


def merged_layout(stored: dict | None) -> dict:
    layout = copy.deepcopy(DEFAULT_FULL_LAYOUT)
    for key, value in (stored or {}).items():
        if key not in layout:
            continue  # settings from older layout versions
        if isinstance(value, dict) and isinstance(layout.get(key), dict):
            layout[key].update(value)
        else:
            layout[key] = copy.deepcopy(value)
    return layout


def merged_calibration(stored: dict | None) -> dict:
    calibration = dict(DEFAULT_FULL_CALIBRATION)
    calibration.update(stored or {})
    return calibration


def base_design(page: int) -> int:
    return page - 6 if page >= 10 else page


def em(size: float) -> float:
    return size / MM


def ink_h(size: float) -> float:
    return (INK_ABOVE + INK_BELOW) * em(size)


# --- artwork ------------------------------------------------------------------

_art_cache: dict[tuple, object] = {}


def design_art(master_pdf: Path, page: int, sticker_calibration: dict):
    """The illustration area (above the strip) of a design, as a PIL image in sticker mm space."""
    key = (str(master_pdf), page, sticker_calibration.get("bg_x"), sticker_calibration.get("bg_y"))
    if key not in _art_cache:
        png = core.background_png(master_pdf, page, sticker_calibration, ART_PX_PER_MM)
        from PIL import Image

        image = Image.open(BytesIO(png)).convert("RGB")
        _art_cache[key] = image.crop((0, 0, image.width, round(ART_SOURCE_H * ART_PX_PER_MM)))
        if len(_art_cache) > 12:
            _art_cache.pop(next(iter(_art_cache)))
    return _art_cache[key]


def art_crop_jpeg(art, page: int, width_mm: float, height_mm: float, zoom: float) -> bytes:
    """Crop a width x height window (label mm) around the subject, bottom-aligned to the strip."""
    source_h = art.height / ART_PX_PER_MM
    zoom = max(zoom, height_mm / source_h, width_mm / 100)  # never stretch past the artwork
    src_w = width_mm / zoom
    src_h = height_mm / zoom
    focus = ART_FOCUS_X.get(base_design(page), 50.0)
    left = min(max(focus - src_w / 2, 0), 100 - src_w)
    box = tuple(round(v * ART_PX_PER_MM) for v in (left, source_h - src_h, left + src_w, source_h))
    out = BytesIO()
    art.crop(box).save(out, format="JPEG", quality=90)
    return out.getvalue()


# --- text helpers -------------------------------------------------------------

def wrap(text: str, font: str, size: float, kerning: float, max_width: float) -> list[str]:
    lines: list[str] = []
    for paragraph in str(text or "").splitlines():
        words = paragraph.split()
        if not words:
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if core.text_width_mm(candidate, font, size, kerning) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def split_two_lines(text: str) -> list[str]:
    words = text.split()
    if len(words) < 2:
        return [text]
    best = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
    return [" ".join(words[:best]), " ".join(words[best:])]


def draw_text(c, text: str, x: float, y: float, size: float, weight, kerning: float, height_mm: float,
              color: str = INK, align: str = "left") -> float:
    """Draw logical-order text on centre line y. Returns its width in mm."""
    if not text:
        return 0.0
    font = core.font_for_weight(weight)
    width = core.text_width_mm(text, font, size, kerning)
    if align == "right":
        x -= width
    elif align == "center":
        x -= width / 2
    ascent = pdfmetrics.getAscent(font, size)
    descent = pdfmetrics.getDescent(font, size)
    c.setFillColor(HexColor(color))
    obj = c.beginText(x * MM, height_mm * MM - y * MM - (ascent + descent) / 2)
    obj.setFont(font, size)
    obj.setCharSpace(size * kerning / 1000)
    obj.textLine(get_display(text))
    c.drawText(obj)
    return width


def draw_ink_top(c, text, x, top, size, weight, kerning, height_mm, color=INK, align="left") -> float:
    """Draw text so the top of its letters sits at `top`."""
    return draw_text(c, text, x, top + INK_ABOVE * em(size), size, weight, kerning, height_mm, color, align)


def draw_ink_middle(c, text, x, middle, size, weight, kerning, height_mm, color=INK, align="left") -> float:
    """Draw text so its letters are visually centred on `middle`."""
    shift = (INK_BELOW - INK_ABOVE) / 2 * em(size)
    return draw_text(c, text, x, middle - shift, size, weight, kerning, height_mm, color, align)


# --- block measurement ----------------------------------------------------------

def title_block(title: str, layout: dict, width: float) -> tuple[list[str], float, bool]:
    """Lines and font size for the coffee name; True if it had to shrink or break."""
    t = layout["title"]
    font = core.font_for_weight(t["weight"])
    size = float(t["size"])
    measured = core.text_width_mm(title, font, size, t["kerning"])
    if measured <= width:
        return [title], size, False
    fitted = size * width / measured
    if fitted >= float(t["min_size"]) or len(title.split()) < 2:
        return [title], max(fitted, float(t["min_size"]) * 0.75), True
    lines = split_two_lines(title)
    size = float(t["two_line_size"])
    widest = max(core.text_width_mm(line, font, size, t["kerning"]) for line in lines)
    return lines, min(size, size * width / widest), True


def info_lines(product: dict, layout: dict, width: float) -> list[tuple[dict, list[tuple[str, str]], float]]:
    """Pack each row's items into as few lines as fit, wrapping instead of shrinking."""
    segments = core.row_segments(product)
    gap = float(layout["separator"]["gap"])
    sep_font = core.font_for_weight(layout["separator"]["weight"])
    result = []
    for row in layout["rows"]:
        font = core.font_for_weight(row["weight"])
        size = float(row["size"])
        items = [(item, segments.get(item, "")) for item in row.get("items", [])]
        items = [(item, text) for item, text in items if text]
        line: list[tuple[str, str]] = []
        line_w = 0.0
        for item, text in items:
            w = core.text_width_mm(text, font, size, row["kerning"])
            extra = w if not line else 2 * gap + core.text_width_mm("|", sep_font, size, 0) + w
            if line and line_w + extra > width:
                result.append((row, line, size))
                line, line_w = [], 0.0
                extra = w
            line.append((item, text))
            line_w += extra
        if line:
            result.append((row, line, size))
    return result


def line_width(items: list[tuple[str, str]], row: dict, size: float, layout: dict) -> float:
    """Width of one packed info line including its " | " separators."""
    sep = layout["separator"]
    font = core.font_for_weight(row["weight"])
    width = sum(core.text_width_mm(text, font, size, row["kerning"]) for _, text in items)
    pipe = core.text_width_mm("|", core.font_for_weight(sep["weight"]), size, 0)
    return width + (len(items) - 1) * (2 * float(sep["gap"]) + pipe)


# --- the label ----------------------------------------------------------------

def draw_label(c, product: dict, daily: dict, layout: dict, calibration: dict, art,
               bottom_white: float = 0.0) -> list[str]:
    """Draw one label. `art` is the design illustration (PIL) or None. Returns warnings.

    `bottom_white` (mm) makes the bottom band white and adds that much white below
    its content, so a printer's unprintable page edge at the label bottom is invisible."""
    W, H = calibration["sticker_w"], calibration["sticker_h"]
    page = int(product.get("page") or 6)
    info = core.PAGES.get(page, {})
    specialty = bool(info.get("specialty"))
    design = DESIGN_COLORS.get(base_design(page), "#223529")
    body = SPECIALTY_BODY_COLOR if specialty else BODY_COLOR
    m = float(layout["margin"])
    inner = W - 2 * m
    pad = float(layout["pad"])
    warnings: list[str] = []

    def rect(x, y, w, h, color):
        c.setFillColor(HexColor(color))
        c.rect(x * MM, (H - y - h) * MM, w * MM, h * MM, stroke=0, fill=1)

    rules: list[tuple] = []

    # ---- measure every block --------------------------------------------------
    title = str(product.get("title") or "").strip()
    t = layout["title"]
    title_lines, title_size, title_shrunk = title_block(title, layout, inner) if title else ([], 0, False)
    if title_shrunk and len(title_lines) == 1 and title_size < float(t["min_size"]):
        warnings.append("שם הקפה")
    title_h = len(title_lines) * ink_h(title_size) + max(len(title_lines) - 1, 0) * float(t["line_gap"])

    lines = info_lines(product, layout, inner)
    rows_h = sum(ink_h(size) for _, _, size in lines) + max(len(lines) - 1, 0) * float(layout["row_gap"])

    d = layout["description"]
    description = " ".join(str(product.get("description") or "").split())
    d_font = core.font_for_weight(d["weight"])
    d_size = float(d["size"])

    def description_height(size):
        wrapped = wrap(description, d_font, size, d["kerning"], inner)
        return wrapped, len(wrapped) * ink_h(size) + max(len(wrapped) - 1, 0) * float(d["line_gap"]) * size / float(d["size"])

    d_lines, d_h = description_height(d_size) if description else ([], 0.0)

    bottom_white = max(0.0, float(bottom_white or 0))
    band_content_h = float(layout["band_h"])
    band_h = band_content_h + bottom_white
    strip_h = float(layout["strip_h"])
    blocks = [h for h in (title_h, rows_h) if h]
    tail_h = d_h
    if tail_h:
        blocks.append(tail_h)
    pads = 2 * len(blocks)

    def art_for(tail):
        content = sum(h for h in (title_h, rows_h) if h) + tail
        return H - band_h - strip_h - content - pads * pad

    art_h = art_for(tail_h)
    if art_h < float(layout["art_min"]) and description:
        # Not enough room: shrink the description until the artwork keeps its minimum.
        while art_h < float(layout["art_min"]) and d_size > float(d["min_size"]):
            d_size -= 0.25
            d_lines, d_h = description_height(d_size)
            tail_h = d_h
            art_h = art_for(tail_h)
        if d_size < 9:
            warnings.append("תיאור")
        if art_h < float(layout["art_min"]):
            warnings.append("תיאור (ארוך מדי)")
    if art_h > float(layout["art_max"]):
        # Short content: spread the spare height into the padding instead of a gap.
        pad += (art_h - float(layout["art_max"])) / max(pads, 1)
        art_h = float(layout["art_max"])
    art_h = max(art_h, 10.0)

    # ---- backgrounds, artwork, strip -----------------------------------------
    rect(0, 0, W, H, body)
    if bottom_white:
        rect(0, H - band_h, W, band_h, "#ffffff")
    if art is not None:
        jpeg = art_crop_jpeg(art, page, W, art_h, float(layout["art_zoom"]))
        c.drawImage(ImageReader(BytesIO(jpeg)), 0, (H - art_h) * MM, width=W * MM, height=art_h * MM)
    rect(0, art_h, W, strip_h, design)
    strip_mid = art_h + strip_h / 2
    strip_label = str(info.get("label", "")).replace("ספיישלטי", "").strip()
    st = layout["strip_text"]
    label_w = draw_ink_middle(c, strip_label, m, strip_mid, st["size"], st["weight"], st["kerning"], H, CREAM)
    if specialty:
        sp = layout["specialty"]
        text = f"SPECIALTY COFFEE {str(product.get('score') or '').strip()}".strip()
        font = core.font_for_weight(sp["weight"])
        size = float(sp["size"])
        avail = W - (m + label_w + 2.5) - 2 * 1.5
        width = core.text_width_mm(text, font, size, sp["kerning"])
        if width > avail:
            size *= avail / width
            width = core.text_width_mm(text, font, size, sp["kerning"])
        box_w = width + 2 * 1.5
        rect(W - box_w, art_h, box_w, strip_h, SPECIALTY_ORANGE)
        draw_text(c, text, W - box_w / 2, strip_mid + 0.15, size, sp["weight"], sp["kerning"], H, CREAM, "center")

    # ---- flowing body ---------------------------------------------------------
    y = art_h + strip_h
    if title_lines:
        y += pad
        for index, line in enumerate(title_lines):
            draw_ink_top(c, line, W / 2, y, title_size, t["weight"], t["kerning"], H, align="center")
            y += ink_h(title_size) + (float(t["line_gap"]) if index < len(title_lines) - 1 else 0)
        y += pad
        rules.append(y)

    if lines:
        y += pad
        sep = layout["separator"]
        sep_w_cache = {}
        for index, (row, items, size) in enumerate(lines):
            x = (W - line_width(items, row, size, layout)) / 2  # centred
            for position, (_, text) in enumerate(items):
                if position:
                    x += float(sep["gap"])
                    if size not in sep_w_cache:
                        sep_w_cache[size] = core.text_width_mm("|", core.font_for_weight(sep["weight"]), size, 0)
                    draw_ink_top(c, "|", x, y, size, sep["weight"], 0, H)
                    x += sep_w_cache[size] + float(sep["gap"])
                x += draw_ink_top(c, text, x, y, size, row["weight"], row["kerning"], H)
            y += ink_h(size) + (float(layout["row_gap"]) if index < len(lines) - 1 else 0)
        y += pad
        if tail_h:
            rules.append(y)

    band_top = H - band_h
    if tail_h:
        y += pad
        step = ink_h(d_size) + float(d["line_gap"]) * d_size / float(d["size"])
        for line in d_lines:
            if y + ink_h(d_size) > band_top - pad * 0.5:
                warnings.append("תיאור (נחתך)")
                break
            draw_ink_top(c, line, W / 2, y, d_size, d["weight"], d["kerning"], H, align="center")
            y += step
    rules.append(band_top)

    # ---- date | cup | weight band ----------------------------------------------
    date_right = float(layout["date_cell_right"])
    cup_right = float(layout["cup_cell_right"])
    band_mid = band_top + band_content_h / 2
    band_bottom = H - bottom_white
    surprise = core.is_surprise(daily)
    # A surprise label has no date and no weight: "הפתעה!" fills the band beside the cup.
    cup_left, cup_end = (W - (cup_right - date_right), W) if surprise else (date_right, cup_right)
    if surprise:
        su = layout["surprise"]
        font = core.font_for_weight(su["weight"])
        size = float(su["size"])
        avail = cup_left - 2 * 1.4
        width = core.text_width_mm(core.SURPRISE_TEXT, font, size, su["kerning"])
        if width > avail:
            size *= avail / width
        size = min(size, (band_content_h - 2.4) / (INK_ABOVE + INK_BELOW) * MM)
        draw_ink_middle(c, core.SURPRISE_TEXT, cup_left / 2, band_mid, size, su["weight"], su["kerning"], H, align="center")
    else:
        dl, ds = layout["date_label"], layout["date"]
        stack = ink_h(dl["size"]) + 1.6 + ink_h(ds["size"])
        top = band_mid - stack / 2
        draw_ink_top(c, "תאריך קלייה:", date_right / 2, top, dl["size"], dl["weight"], dl["kerning"], H, align="center")
        date = str(daily.get("date") or "").strip()
        if date:
            draw_ink_top(c, date, date_right / 2, top + ink_h(dl["size"]) + 1.6, ds["size"], ds["weight"], ds["kerning"], H, align="center")

    cup_cx = (cup_left + cup_end) / 2 - 0.6
    radius = min(3.1, band_content_h / 2 - 2.6)
    c.setStrokeColor(HexColor(design))
    c.setLineWidth(1.6)
    c.circle(cup_cx * MM, (H - band_mid) * MM, radius * MM, stroke=1, fill=0)
    c.setLineWidth(0.8)
    c.circle((cup_cx + radius + 0.8) * MM, (H - band_mid) * MM, radius * 0.31 * MM, stroke=1, fill=0)
    c.setFillColor(HexColor(body))
    c.circle(cup_cx * MM, (H - band_mid) * MM, radius * 0.71 * MM, stroke=0, fill=1)
    c.setFillColor(HexColor(design))
    c.circle(cup_cx * MM, (H - band_mid) * MM, radius * 0.63 * MM, stroke=0, fill=1)

    if not surprise:
        draw_weight(c, daily, layout, cup_right, W, H, band_mid, band_content_h)

    # ---- grid rules ------------------------------------------------------------
    c.setStrokeColor(HexColor(RULE))
    c.setLineWidth(0.4)
    for rule_y in rules:
        c.line(0, (H - rule_y) * MM, W * MM, (H - rule_y) * MM)
    for x in ((cup_left,) if surprise else (date_right, cup_right)):
        c.line(x * MM, (H - band_top) * MM, x * MM, (H - band_bottom) * MM)
    return sorted(set(warnings))


def draw_weight(c, daily, layout, cell_left, W, H, band_mid, band_h):
    weight = core.pretty_weight(daily.get("weight", "1"))
    ws, unit = layout["weight"], layout["weight_unit"]
    wsize = float(layout["weight_fraction_size"]) if weight in {"½", "¼"} else float(ws["size"])
    wsize = min(wsize, (band_h - 2.0) / 0.62 * MM)  # digits stand ~0.62 em tall
    unit_w = core.text_width_mm("ק״ג", core.font_for_weight(unit["weight"]), unit["size"], 30)
    num_w = core.text_width_mm(weight, core.font_for_weight(ws["weight"]), wsize, 0)
    left = (cell_left + W) / 2 - (unit_w + 0.6 + num_w) / 2
    draw_ink_middle(c, weight, left + unit_w + 0.6, band_mid, wsize, ws["weight"], 0, H)
    draw_ink_middle(c, "ק״ג", left, band_mid + 0.3 * em(wsize) * 0.62, unit["size"], unit["weight"], 30, H)


def label_pdf(product: dict, daily: dict, layout: dict, calibration: dict, art, copies: int = 1) -> tuple[bytes, list[str]]:
    W, H = calibration["sticker_w"], calibration["sticker_h"]
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(W * MM, H * MM))
    c.setTitle("label")
    rotation = int(calibration.get("rotation") or 0) % 360
    warnings: list[str] = []
    for _ in range(max(1, min(int(copies or 1), 500))):
        if rotation:
            c.setPageRotation(rotation)
        warnings = draw_label(c, product, daily, layout, calibration, art)
        c.showPage()
    c.save()
    return buffer.getvalue(), warnings
