"""A4 sheet of narrow "הפתעה!" labels (default 2 x 6 slots of 105 x 46 mm).

Each slot holds the narrow surprise label (label_full), drawn at the narrow
label's own width and uniformly scaled to fit (50 mm -> 46 mm), then rotated
90 degrees to lie in the landscape slot. Once peeled it is stuck upright on
the bag and looks like a slightly smaller 50x100 label.
Every label fills its full slot (105 x 46 mm). The two columns mirror each
other like the v1 sheets: label tops (the artwork) meet in the middle of the
page and the bottoms face the outer paper edges. Printers leave the outer few millimetres of the page unprinted, which falls on
each label's bottom. So the bottom band is printed white and `white_edge` mm of
extra white sits under its content: the printer's margin blends in.
Printing can start part-way through a sheet so a partly used sheet can be
finished.
"""

from __future__ import annotations

import copy
from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

import label_full as full
from label_full import MM

DEFAULT_SHEET_LAYOUT = {
    "page_w": 210.0,
    "page_h": 297.0,
    "cols": 2,
    "rows_per_page": 6,
    "label_w": 105.0,   # slot size on the sheet (landscape)
    "label_h": 46.0,
    "margin_top": 10.0,
    "margin_left": 0.0,
    "gap_x": 0.0,
    "gap_y": 0.0,
    "white_edge": 4.0,  # printer's unprintable edge; white space under the bottom band
}


def merged_layout(stored: dict | None) -> dict:
    layout = copy.deepcopy(DEFAULT_SHEET_LAYOUT)
    for key, value in (stored or {}).items():
        if key in layout:
            layout[key] = copy.deepcopy(value)
    return layout


def labels_per_page(layout: dict) -> int:
    return int(layout["cols"]) * int(layout["rows_per_page"])


def sheet_pdf(product: dict, layout: dict, art, full_layout: dict | None = None, count: int = 12,
              start: int = 1, outlines: bool = False, narrow_width: float = 50.0) -> tuple[bytes, list[str], int]:
    """Lay `count` surprise labels onto A4 pages starting at slot `start` (1-based).

    Returns (pdf, warnings, pages)."""
    per_page = labels_per_page(layout)
    start = max(1, min(int(start or 1), per_page))
    count = max(1, min(int(count or 1), 500))
    page_w, page_h = float(layout["page_w"]), float(layout["page_h"])
    slot_w, slot_h = float(layout["label_w"]), float(layout["label_h"])
    full_layout = full_layout or full.merged_layout({})
    # Draw at the narrow label's width, scaled so its width matches the slot height
    # and its length exactly fills the slot.
    scale = min(1.0, slot_h / float(narrow_width or slot_h))
    label_size = {"sticker_w": slot_h / scale, "sticker_h": slot_w / scale}
    white = max(0.0, min(float(layout.get("white_edge") or 0), slot_w / 3))
    daily = {"date_mode": "surprise"}
    cols = int(layout["cols"])

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_w * MM, page_h * MM))
    c.setTitle("הפתעה A4")
    warnings: list[str] = []
    slot, printed, pages = start - 1, 0, 1
    while printed < count:
        if slot >= per_page:
            c.showPage()
            pages += 1
            slot = 0
        col = slot % cols
        row = slot // cols
        x = float(layout["margin_left"]) + col * (slot_w + float(layout["gap_x"]))
        top = float(layout["margin_top"]) + row * (slot_h + float(layout["gap_y"]))
        bottom = page_h - top - slot_h
        c.saveState()
        clip = c.beginPath()
        clip.rect(x * MM, bottom * MM, slot_w * MM, slot_h * MM)
        c.clipPath(clip, stroke=0, fill=0)
        if cols > 1 and col == cols - 1:
            # Right column: top at the slot's left edge (page centre), bottom at the right edge.
            c.translate((x + slot_w) * MM, bottom * MM)
            c.rotate(90)
        else:
            # Left column: top at the slot's right edge (page centre), bottom at the left edge.
            c.translate(x * MM, (bottom + slot_h) * MM)
            c.rotate(-90)
        c.scale(scale, scale)
        warnings = full.draw_label(c, product, daily, full_layout, label_size, art, bottom_white=white / scale)
        c.restoreState()
        if outlines:
            c.setStrokeColor(HexColor("#999999"))
            c.setLineWidth(0.3)
            c.rect(x * MM, bottom * MM, slot_w * MM, slot_h * MM, stroke=1, fill=0)
        slot += 1
        printed += 1
    c.showPage()
    c.save()
    return buffer.getvalue(), sorted(set(warnings)), pages
