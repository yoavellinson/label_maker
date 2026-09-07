from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request, send_file
from bidi.algorithm import get_display
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def app_data_dir() -> Path:
    if "LABEL_MAKER_DATA_DIR" in os.environ:
        return Path(os.environ["LABEL_MAKER_DATA_DIR"]).expanduser()
    if getattr(sys, "frozen", False) and os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "LabelMaker"
    return app_base_dir()


BASE_DIR = app_base_dir()
DATA_DIR = app_data_dir()
BUNDLED_GRID_DIR = BASE_DIR / "grid"
GRID_DIR = DATA_DIR / "grid"
FONT_DIR = BASE_DIR / "fonts"
LAYOUT_PATH = GRID_DIR / "layout_dev_config.json"
LAYOUT_DB_PATH = GRID_DIR / "layout_presets.json"

POINTS_PER_MM = 72 / 25.4
RENDER_DPI = 144


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024


pdfmetrics.registerFont(TTFont("KarantinaLight", str(FONT_DIR / "Karantina/static/Karantina-Light.ttf")))
pdfmetrics.registerFont(TTFont("Karantina", str(FONT_DIR / "Karantina/static/Karantina-Regular.ttf")))
pdfmetrics.registerFont(TTFont("KarantinaBold", str(FONT_DIR / "Karantina/static/Karantina-Bold.ttf")))


def font_name(weight: str | int) -> str:
    try:
        value = int(weight)
    except (TypeError, ValueError):
        value = 400
    if value >= 700:
        return "KarantinaBold"
    if value <= 300:
        return "KarantinaLight"
    return "Karantina"


def draw_text_field(c: canvas.Canvas, field: dict) -> None:
    text = str(field.get("text") or "")
    if not text:
        return

    x = float(field.get("x_pt") or 0)
    y_center = float(field.get("y_pt_from_bottom") or 0)
    size = float(field.get("font_size_pt") or 12)
    kerning = field.get("kerning")
    if kerning is None:
        letter_spacing = float(field.get("letter_spacing_pt") or 0)
    else:
        letter_spacing = size * float(kerning or 0) / 1000
    leading = size * 0.95
    lines = text.splitlines() or [text]
    start_y = y_center + ((len(lines) - 1) * leading / 2)
    font = font_name(field.get("font_weight"))
    align = field.get("align") or "center"
    rtl = field.get("rtl", True)

    c.setFillColor(HexColor(field.get("color") or "#111111"))
    for index, line in enumerate(lines):
        display = get_display(line) if rtl else line
        y = start_y - index * leading
        text_width = pdfmetrics.stringWidth(display, font, size)
        if display:
            text_width += max(len(display) - 1, 0) * letter_spacing
        draw_x = x
        if align == "right":
            draw_x = x - text_width
        elif align == "left":
            draw_x = x
        else:
            draw_x = x - text_width / 2

        text_obj = c.beginText(draw_x, y)
        text_obj.setFont(font, size)
        text_obj.setCharSpace(letter_spacing)
        text_obj.textLine(display)
        c.drawText(text_obj)


def scaled_text_field(field: dict, scale_x: float, scale_y: float) -> dict:
    scaled = dict(field)
    scaled["x_pt"] = float(field.get("x_pt") or 0) * scale_x
    scaled["y_pt_from_bottom"] = float(field.get("y_pt_from_bottom") or 0) * scale_y
    scaled["font_size_pt"] = float(field.get("font_size_pt") or 12) * min(scale_x, scale_y)
    if "kerning" not in scaled:
        scaled["letter_spacing_pt"] = float(field.get("letter_spacing_pt") or 0) * min(scale_x, scale_y)
    return scaled


def read_layout_db() -> dict:
    ensure_runtime_data()
    if not LAYOUT_DB_PATH.exists():
        return {"layouts": []}
    return json.loads(LAYOUT_DB_PATH.read_text(encoding="utf-8"))


def write_layout_db(data: dict) -> None:
    GRID_DIR.mkdir(parents=True, exist_ok=True)
    LAYOUT_DB_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def copy_if_missing(source: Path, destination: Path) -> None:
    if destination.exists() or not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)


def ensure_runtime_data() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    copy_if_missing(BUNDLED_GRID_DIR / "backgrounds", GRID_DIR / "backgrounds")
    for filename in [
        "Master_stickers_guide.pdf",
        "final_stickers.con_16.8.26 (1).pdf",
        "layout_presets.json",
        "layout_dev_config.json",
    ]:
        copy_if_missing(BUNDLED_GRID_DIR / filename, GRID_DIR / filename)


def logical_source_path(path: Path) -> str:
    try:
        return str(path.relative_to(DATA_DIR))
    except ValueError:
        return str(path.relative_to(BASE_DIR))


def resolve_source_path(source: str) -> Path | None:
    if not source:
        return None
    candidates = [
        (DATA_DIR / source).resolve(),
        (BASE_DIR / source).resolve(),
    ]
    for candidate in candidates:
        if candidate.exists() and (
            candidate == DATA_DIR
            or DATA_DIR in candidate.parents
            or candidate == BASE_DIR
            or BASE_DIR in candidate.parents
        ):
            return candidate
    return None


def layout_summary(layout: dict) -> dict:
    return {
        "id": layout["id"],
        "name": layout["name"],
        "updated_at": layout.get("updated_at", ""),
        "source": layout.get("layout", {}).get("source", {}).get("file")
        or layout.get("layout", {}).get("source", ""),
        "page": layout.get("layout", {}).get("source", {}).get("page")
        or layout.get("layout", {}).get("page", ""),
    }


def available_sources() -> list[dict[str, str]]:
    ensure_runtime_data()
    sources = []
    for path in sorted(GRID_DIR.glob("*.pdf")):
        sources.append({"kind": "pdf", "name": path.name, "path": logical_source_path(path)})
    for path in sorted((GRID_DIR / "backgrounds").glob("*.png")):
        sources.append({"kind": "image", "name": path.stem, "path": logical_source_path(path)})
    return sources


def run_command(args: list[str]) -> str:
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return result.stdout


def pdf_page_size_mm(pdf_path: Path, page: int) -> tuple[float, float]:
    info = run_command(["pdfinfo", "-f", str(page), "-l", str(page), str(pdf_path)])
    width_pt = height_pt = None
    for line in info.splitlines():
        match = re.search(r"Page(?:\s+\d+)?\s+size:\s+([\d.]+)\s+x\s+([\d.]+)\s+pts", line)
        if match:
            width_pt = float(match.group(1))
            height_pt = float(match.group(2))
            break
    if width_pt is None or height_pt is None:
        raise RuntimeError("Could not read PDF page size.")
    return width_pt / POINTS_PER_MM, height_pt / POINTS_PER_MM


def image_response(path: Path, width_mm: float | None = None, height_mm: float | None = None):
    data = path.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "image": f"data:image/png;base64,{encoded}",
        "page_width_mm": width_mm,
        "page_height_mm": height_mm,
    }


@app.get("/")
def index():
    return render_template_string(TEMPLATE, sources=available_sources())


@app.get("/font/<name>")
def font(name: str):
    allowed = {
        "karantina-light.ttf": FONT_DIR / "Karantina/static/Karantina-Light.ttf",
        "karantina-regular.ttf": FONT_DIR / "Karantina/static/Karantina-Regular.ttf",
        "karantina-bold.ttf": FONT_DIR / "Karantina/static/Karantina-Bold.ttf",
        "heebo.ttf": FONT_DIR / "Heebo/Heebo-VariableFont_wght.ttf",
    }
    path = allowed.get(name)
    if not path or not path.exists():
        return ("", 404)
    return send_file(path)


@app.post("/render-source")
def render_source():
    page = max(1, int(request.form.get("page") or 1))
    source = request.form.get("source") or ""
    upload = request.files.get("pdf")

    if upload and upload.filename:
        suffix = Path(upload.filename).suffix.lower() or ".pdf"
        temp_pdf = Path(tempfile.gettempdir()) / f"label-layout-{uuid.uuid4().hex}{suffix}"
        upload.save(temp_pdf)
        pdf_path = temp_pdf
    else:
        candidate = resolve_source_path(source)
        if not candidate:
            return jsonify({"error": "Source file was not found."}), 404
        if candidate.suffix.lower() != ".pdf":
            return jsonify(image_response(candidate, None, None))
        pdf_path = candidate

    out_prefix = Path(tempfile.gettempdir()) / f"label-layout-render-{uuid.uuid4().hex}"
    try:
        width_mm, height_mm = pdf_page_size_mm(pdf_path, page)
        run_command([
            "pdftoppm",
            "-png",
            "-singlefile",
            "-f",
            str(page),
            "-l",
            str(page),
            "-r",
            str(RENDER_DPI),
            str(pdf_path),
            str(out_prefix),
        ])
        return jsonify(image_response(out_prefix.with_suffix(".png"), width_mm, height_mm))
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        rendered = out_prefix.with_suffix(".png")
        if rendered.exists():
            rendered.unlink(missing_ok=True)


@app.get("/load-layout")
def load_layout():
    if not LAYOUT_PATH.exists():
        return jsonify({"layout": None})
    return jsonify({"layout": json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))})


@app.post("/save-layout")
def save_layout():
    data = request.get_json(force=True)
    GRID_DIR.mkdir(parents=True, exist_ok=True)
    LAYOUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "path": logical_source_path(LAYOUT_PATH)})


@app.get("/layouts")
def list_layouts():
    db = read_layout_db()
    layouts = sorted(db.get("layouts", []), key=lambda item: item.get("name", ""))
    return jsonify({"layouts": [layout_summary(item) for item in layouts]})


@app.get("/layouts/<layout_id>")
def get_layout(layout_id: str):
    db = read_layout_db()
    for layout in db.get("layouts", []):
        if layout.get("id") == layout_id:
            return jsonify({"layout": layout})
    return jsonify({"error": "Layout not found."}), 404


@app.post("/layouts")
def save_named_layout():
    payload = request.get_json(force=True)
    name = (payload.get("name") or "").strip()
    layout = payload.get("layout")
    layout_id = (payload.get("id") or "").strip()
    force_new = bool(payload.get("force_new"))
    if not name:
        return jsonify({"error": "Layout name is required."}), 400
    if not isinstance(layout, dict):
        return jsonify({"error": "Layout data is required."}), 400

    db = read_layout_db()
    layouts = db.setdefault("layouts", [])
    if not layout_id and not force_new:
        existing = next((item for item in layouts if item.get("name") == name), None)
        layout_id = existing["id"] if existing else uuid.uuid4().hex
    elif not layout_id:
        layout_id = uuid.uuid4().hex

    record = {
        "id": layout_id,
        "name": name,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "layout": layout,
    }
    for index, item in enumerate(layouts):
        if item.get("id") == layout_id:
            layouts[index] = record
            break
    else:
        layouts.append(record)
    write_layout_db(db)
    return jsonify({"ok": True, "layout": layout_summary(record), "path": logical_source_path(LAYOUT_DB_PATH)})


@app.post("/text-only.pdf")
def text_only_pdf():
    payload = request.get_json(force=True)
    parameters = payload.get("parameters", payload)
    sticker = parameters.get("sticker", {})
    print_size = parameters.get("print_size", {})
    layout_width = float(sticker.get("width_pt") or 283.46)
    layout_height = float(sticker.get("height_pt") or 283.46)
    width = float(print_size.get("width_pt") or layout_width)
    height = float(print_size.get("height_pt") or layout_height)
    scale_x = width / layout_width if layout_width else 1
    scale_y = height / layout_height if layout_height else 1
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))
    for field in parameters.get("text_fields", []):
        draw_text_field(c, scaled_text_field(field, scale_x, scale_y))
    c.showPage()
    c.save()
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=False,
        download_name="label-text-only.pdf",
    )


TEMPLATE = """
<!doctype html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>כלי מיקום מדבקות</title>
  <style>
    @font-face {
      font-family: "Karantina";
      src: url("/font/karantina-regular.ttf") format("truetype");
      font-weight: 400;
    }

    @font-face {
      font-family: "Karantina";
      src: url("/font/karantina-bold.ttf") format("truetype");
      font-weight: 700;
    }

    @font-face {
      font-family: "Karantina";
      src: url("/font/karantina-light.ttf") format("truetype");
      font-weight: 300;
    }

    @font-face {
      font-family: "Heebo";
      src: url("/font/heebo.ttf") format("truetype");
      font-weight: 100 900;
    }

    :root {
      --bg: #f5f1ea;
      --panel: #fffaf3;
      --ink: #231916;
      --muted: #76645d;
      --line: #ddcec2;
      --accent: #0e6241;
      --stage: #dad0c5;
      --selected: #ef3b2c;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--ink);
      font-family: "Heebo", Arial, sans-serif;
    }

    button,
    input,
    select,
    textarea {
      font: inherit;
    }

    .app {
      display: grid;
      grid-template-columns: minmax(520px, 1fr) 420px;
      gap: 18px;
      min-height: 100vh;
      padding: 18px;
      direction: ltr;
    }

    .stage-wrap,
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      min-width: 0;
    }

    .stage-wrap {
      display: grid;
      grid-template-rows: auto 1fr;
      overflow: hidden;
    }

    .stage-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      direction: ltr;
    }

    .stage-title {
      direction: rtl;
      text-align: right;
    }

    .stage-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      direction: rtl;
    }

    h1 {
      margin: 0;
      font-size: 18px;
      line-height: 1.1;
    }

    .hint {
      color: var(--muted);
      font-size: 13px;
    }

    .canvas-pad {
      min-height: 0;
      display: grid;
      place-items: center;
      padding: 18px;
      overflow: auto;
      background:
        linear-gradient(45deg, #d9d1c7 25%, transparent 25%) 0 0 / 24px 24px,
        linear-gradient(45deg, transparent 75%, #d9d1c7 75%) 0 0 / 24px 24px,
        linear-gradient(45deg, transparent 75%, #d9d1c7 75%) 12px 12px / 24px 24px,
        linear-gradient(45deg, #d9d1c7 25%, #e7ded5 25%) 12px 12px / 24px 24px;
    }

    .sticker {
      position: relative;
      overflow: hidden;
      background: white;
      box-shadow: 0 18px 44px rgba(46, 31, 24, 0.22);
      transform-origin: center;
      user-select: none;
    }

    .sticker::after {
      content: "";
      position: absolute;
      inset: 0;
      border: 1px dashed rgba(0, 0, 0, 0.45);
      pointer-events: none;
    }

    .bg-image {
      position: absolute;
      top: 0;
      left: 0;
      pointer-events: none;
      transform-origin: top left;
    }

    .text-item {
      position: absolute;
      min-width: 12px;
      min-height: 14px;
      padding: 1px 4px;
      color: #111;
      font-family: "Karantina", "Heebo", sans-serif;
      line-height: 0.95;
      white-space: pre-wrap;
      cursor: grab;
      border: 1px solid transparent;
      transform: translate(-50%, -50%);
      text-align: center;
      direction: rtl;
    }

    .text-item[data-align="left"] {
      text-align: left;
      transform: translate(0, -50%);
    }

    .text-item[data-align="right"] {
      text-align: right;
      transform: translate(-100%, -50%);
    }

    .text-item.selected {
      border-color: var(--selected);
      background: rgba(255, 255, 255, 0.42);
    }

    .panel {
      padding: 14px;
      direction: rtl;
      overflow: auto;
      max-height: calc(100vh - 36px);
      display: flex;
      flex-direction: column;
    }

    .panel.compact .dev-section,
    .panel.compact .export-section {
      display: none;
    }

    .layout-section {
      order: 1;
    }

    .content-section {
      order: 2;
    }

    .dev-section {
      order: 3;
    }

    .export-section {
      order: 4;
    }

    .section {
      padding: 12px 0;
      border-bottom: 1px solid var(--line);
    }

    .section:first-child {
      padding-top: 0;
    }

    .section:last-child {
      border-bottom: 0;
      padding-bottom: 0;
    }

    .section-title {
      margin: 0 0 10px;
      font-size: 15px;
      color: var(--ink);
      font-weight: 700;
    }

    .section-subtitle {
      margin: -4px 0 10px;
      color: var(--muted);
      font-size: 12px;
    }

    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }

    label {
      display: grid;
      gap: 5px;
      min-width: 0;
      font-size: 13px;
      color: var(--muted);
    }

    input,
    select,
    textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      color: var(--ink);
      padding: 8px 9px;
      min-height: 38px;
    }

    textarea {
      min-height: 82px;
      resize: vertical;
      direction: rtl;
    }

    input[type="color"] {
      padding: 2px;
    }

    .buttons {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }

    button {
      border: 1px solid var(--accent);
      background: var(--accent);
      color: white;
      border-radius: 6px;
      padding: 8px 12px;
      cursor: pointer;
      min-height: 38px;
    }

    button.secondary {
      background: white;
      color: var(--accent);
    }

    button.secondary.active {
      background: #e9f4ee;
      color: #0b4f35;
      border-color: #0b4f35;
      font-weight: 700;
    }

    .sticker-buttons {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-bottom: 12px;
    }

    .sticker-buttons button {
      min-height: 44px;
      background: white;
      color: var(--ink);
      border-color: var(--line);
    }

    .sticker-buttons button.active {
      color: white;
      border-color: var(--accent);
      background: var(--accent);
      font-weight: 700;
    }

    .daily-actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-top: 10px;
    }

    .daily-actions .print-button {
      grid-column: span 2;
      min-height: 48px;
      font-size: 17px;
      font-weight: 700;
      background: #111;
      border-color: #111;
    }

    .save-status {
      margin-top: 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      background: #f7f4ef;
      color: var(--ink);
      font-size: 13px;
      font-weight: 700;
    }

    .save-status.clean {
      border-color: #0b6b45;
      background: #e9f4ee;
      color: #0b4f35;
    }

    .save-status.dirty {
      border-color: #b86b19;
      background: #fff2df;
      color: #7a430d;
    }

    .save-status.draft {
      border-color: #8c8274;
      background: #f6f1e9;
      color: #5c5348;
    }

    button:disabled {
      opacity: 0.42;
      cursor: not-allowed;
    }

    button.danger {
      border-color: #b33b2d;
      background: #b33b2d;
    }

    .print-hero {
      min-height: 46px;
      padding: 10px 20px;
      font-size: 18px;
      font-weight: 700;
      background: #111;
      border-color: #111;
    }

    .text-list {
      display: grid;
      gap: 6px;
      max-height: 160px;
      overflow: auto;
      padding-left: 4px;
    }

    .row-button {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      background: white;
      color: var(--ink);
      border-color: var(--line);
      text-align: right;
    }

    .row-button.active {
      border-color: var(--selected);
      color: var(--selected);
    }

    .json-box {
      direction: ltr;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      min-height: 140px;
    }

    .mode {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }

    .mode button {
      border-color: var(--line);
      background: white;
      color: var(--ink);
    }

    .mode button.active {
      border-color: var(--accent);
      background: var(--accent);
      color: white;
    }

    @media (max-width: 980px) {
      .app {
        grid-template-columns: 1fr;
      }

      .panel {
        max-height: none;
      }
    }
  </style>
</head>
<body>
  <main class="app">
    <section class="stage-wrap">
      <div class="stage-head">
        <div class="stage-actions">
          <button class="print-hero" id="printPdfTop">הדפסת טקסט</button>
          <button class="secondary" id="toggleCompact">הצג עריכה מלאה</button>
        </div>
        <div class="stage-title">
          <h1>כלי מיקום למדבקה מודפסת</h1>
          <div class="hint" id="status">בחר PDF או רקע, ואז גרור טקסטים ורקע למקום.</div>
        </div>
        <div class="hint" id="readout"></div>
      </div>
      <div class="canvas-pad">
        <div class="sticker" id="sticker">
          <img class="bg-image" id="bgImage" alt="">
          <div id="textLayer"></div>
        </div>
      </div>
    </section>

    <aside class="panel compact" id="sidePanel">
      <section class="section content-section">
        <p class="section-title">2. פרטי המדבקה</p>
        <label>
          שם הקפה
          <input id="contentTitle" type="text" value="אתיופיה דג׳ימה">
        </label>
        <div class="grid" style="margin-top:10px">
          <label>
            טעמים
            <input id="contentTastes" type="text" value="חזק, אש, מעושן">
          </label>
          <label>
            קלייה
            <input id="contentRoast" type="text" value="קלייה בינונית כהה">
          </label>
          <label>
            משקל
            <input id="contentWeight" type="text" value="1">
          </label>
          <label>
            תאריך קלייה
            <input id="contentDate" type="text">
          </label>
          <label>
            רוחב הדפסה מ״מ
            <input id="printW" type="number" min="10" step="0.5" value="97">
          </label>
          <label>
            גובה הדפסה מ״מ
            <input id="printH" type="number" min="10" step="0.5" value="97">
          </label>
          <label>
            עיבוד
            <input id="contentProcess" type="text">
          </label>
          <label>
            כשרות
            <input id="contentKosher" type="text" value="כשר בהשגחת הרבנות חתם סופר">
          </label>
        </div>
        <div class="buttons">
          <button class="secondary" id="todayDate">תאריך היום</button>
          <button class="secondary weight-button" data-weight="1">1 ק״ג</button>
          <button class="secondary weight-button" data-weight="0.5">½ ק״ג</button>
          <button class="secondary weight-button" data-weight="0.25">¼ ק״ג</button>
        </div>
        <div class="daily-actions">
          <button class="secondary" id="newLabel">מדבקה חדשה</button>
          <button id="applyContent">עדכון תצוגה</button>
          <button class="print-button" id="printPdfSide">הדפסת טקסט</button>
        </div>
      </section>

      <section class="section dev-section">
        <p class="section-title">רקע</p>
        <label>
          קובץ קיים
          <select id="source">
            {% for source in sources %}
              <option value="{{ source.path }}">{{ source.name }}</option>
            {% endfor %}
          </select>
        </label>
        <div class="grid" style="margin-top:10px">
          <label>
            עמוד PDF
            <input id="page" type="number" min="1" step="1" value="1">
          </label>
          <label>
            העלאת PDF
            <input id="pdfUpload" type="file" accept="application/pdf">
          </label>
        </div>
        <div class="buttons">
          <button id="loadSource">טעינת רקע</button>
          <button class="secondary" id="loadSaved">טעינת שמירה</button>
        </div>
      </section>

      <section class="section dev-section">
        <p class="section-title">גודל מדבקה ותצוגה</p>
        <div class="grid">
          <label>
            רוחב מ״מ
            <input id="stickerW" type="number" min="10" step="0.5" value="100">
          </label>
          <label>
            גובה מ״מ
            <input id="stickerH" type="number" min="10" step="0.5" value="50">
          </label>
          <label>
            זום תצוגה
            <input id="viewScale" type="range" min="2" max="16" step="0.1" value="4.2">
          </label>
          <label>
            כיוון עדין
            <div class="buttons" style="margin-top:0">
              <button class="secondary" id="zoomOut" type="button">-</button>
              <button class="secondary" id="zoomReset" type="button">100%</button>
              <button class="secondary" id="zoomIn" type="button">+</button>
            </div>
          </label>
          <label>
            זום רקע
            <input id="bgScale" type="number" min="0.1" max="5" step="0.01" value="1">
          </label>
          <label>
            רקע X מ״מ
            <input id="bgX" type="number" step="0.1" value="0">
          </label>
          <label>
            רקע Y מ״מ
            <input id="bgY" type="number" step="0.1" value="0">
          </label>
        </div>
        <div class="mode" style="margin-top:10px">
          <button id="textMode" class="active">גרירת טקסט</button>
          <button id="bgMode">הזזת רקע</button>
        </div>
      </section>

      <section class="section dev-section">
        <p class="section-title">טקסטים</p>
        <div class="buttons" style="margin-top:0">
          <button class="secondary" data-add="origin">מדינה / בלנד</button>
          <button class="secondary" data-add="title">שם מרכזי</button>
          <button class="secondary" data-add="taste">טעמים</button>
          <button class="secondary" data-add="weight">משקל</button>
          <button class="secondary" data-add="date">תאריך</button>
          <button class="secondary" data-add="separator">מפריד |</button>
        </div>
        <div class="text-list" id="textList" style="margin-top:10px"></div>
      </section>

      <section class="section dev-section">
        <p class="section-title">עריכת טקסט נבחר</p>
        <label>
          טקסט
          <textarea id="itemText"></textarea>
        </label>
        <div class="grid" style="margin-top:10px">
          <label>
            X מ״מ
            <input id="itemX" type="number" step="0.1">
          </label>
          <label>
            Y מ״מ
            <input id="itemY" type="number" step="0.1">
          </label>
          <label>
            גודל
            <input id="itemSize" type="number" min="4" step="0.5">
          </label>
          <label>
            Kerning מאסטר
            <input id="itemLetterSpacing" type="number" step="1" value="0">
          </label>
          <label>
            משקל
            <select id="itemWeight">
              <option value="300">דק</option>
              <option value="400">רגיל</option>
              <option value="700">מודגש</option>
            </select>
          </label>
          <label>
            יישור
            <select id="itemAlign">
              <option value="center">מרכז</option>
              <option value="right">ימין</option>
              <option value="left">שמאל</option>
            </select>
          </label>
          <label>
            צבע
            <input id="itemColor" type="color" value="#111111">
          </label>
        </div>
        <div class="buttons">
          <button class="secondary" id="applyMasterKerning">החלת Kerning מהמאסטר</button>
          <button class="danger" id="deleteItem">מחיקת טקסט</button>
        </div>
        <div class="buttons">
          <button class="secondary" id="saveWeightHalf">שמירת מיקום ½</button>
          <button class="secondary" id="saveWeightQuarter">שמירת מיקום ¼</button>
        </div>
      </section>

      <section class="section layout-section">
        <p class="section-title">1. טעינה ושמירה</p>
        <div class="sticker-buttons">
          <button type="button" data-sticker-page="4">שוקולדי</button>
          <button type="button" data-sticker-page="5">פירותי</button>
          <button type="button" data-sticker-page="6">פורטה</button>
          <button type="button" data-sticker-page="10">ספיישלטי שוקולדי</button>
          <button type="button" data-sticker-page="11">ספיישלטי פירותי</button>
          <button type="button" data-sticker-page="12">ספיישלטי פורטה</button>
        </div>
        <label>
          שם פריסה
          <input id="layoutName" type="text" value="מדבקה עמוד 6">
        </label>
        <label style="margin-top:10px">
          פריסות שמורות
          <select id="savedLayouts"></select>
        </label>
        <div class="save-status draft" id="saveStatus">טיוטה מקומית</div>
        <div class="buttons" style="margin-top:10px">
          <button id="loadNamedLayout">טעינת הבחירה</button>
          <button class="secondary" id="saveNamedLayout">עדכון הפריסה השמורה</button>
          <button class="secondary" id="saveAsNamedLayout">שמירה כפריסה חדשה</button>
          <button class="secondary" id="resetNamedLayout">ביטול שינויים</button>
          <button class="secondary" id="refreshLayouts">רענון רשימה</button>
        </div>
      </section>

      <section class="section export-section">
        <p class="section-title">ייצוא טכני</p>
        <div class="buttons" style="margin-top:0">
          <button id="saveLayout">שמירה לקובץ</button>
          <button class="secondary" id="copyJson">העתקת JSON</button>
          <button class="secondary" id="downloadJson">הורדת JSON</button>
          <button class="secondary" id="downloadPdf">הורדת PDF טקסט</button>
          <button class="secondary" id="printPdf">הדפסת טקסט</button>
        </div>
        <textarea class="json-box" id="jsonOut" readonly></textarea>
      </section>
    </aside>
  </main>

  <script>
    const sticker = document.querySelector("#sticker");
    const bgImage = document.querySelector("#bgImage");
    const textLayer = document.querySelector("#textLayer");
    const statusEl = document.querySelector("#status");
    const readout = document.querySelector("#readout");
    const sidePanel = document.querySelector("#sidePanel");
    const stateStorageKey = "label-layout-dev-state-v1";
    const stateStorageVersion = 2;
    const stickerPdfSource = "grid/final_stickers.con_16.8.26 (1).pdf";
    const stickerChoices = {
      4: {label: "שוקולדי", image: "grid/backgrounds/sticker-04-chocolate.png"},
      5: {label: "פירותי", image: "grid/backgrounds/sticker-05-fruity.png"},
      6: {label: "פורטה", image: "grid/backgrounds/sticker-06-forte.png"},
      10: {label: "ספיישלטי שוקולדי", image: "grid/backgrounds/sticker-10-specialty-chocolate.png"},
      11: {label: "ספיישלטי פירותי", image: "grid/backgrounds/sticker-11-specialty-fruity.png"},
      12: {label: "ספיישלטי פורטה", image: "grid/backgrounds/sticker-12-specialty-forte.png"},
    };

    const controls = {
      source: document.querySelector("#source"),
      page: document.querySelector("#page"),
      pdfUpload: document.querySelector("#pdfUpload"),
      stickerW: document.querySelector("#stickerW"),
      stickerH: document.querySelector("#stickerH"),
      viewScale: document.querySelector("#viewScale"),
      bgScale: document.querySelector("#bgScale"),
      bgX: document.querySelector("#bgX"),
      bgY: document.querySelector("#bgY"),
      contentTitle: document.querySelector("#contentTitle"),
      contentTastes: document.querySelector("#contentTastes"),
      contentRoast: document.querySelector("#contentRoast"),
      contentWeight: document.querySelector("#contentWeight"),
      contentDate: document.querySelector("#contentDate"),
      printW: document.querySelector("#printW"),
      printH: document.querySelector("#printH"),
      contentProcess: document.querySelector("#contentProcess"),
      contentKosher: document.querySelector("#contentKosher"),
      itemText: document.querySelector("#itemText"),
      itemX: document.querySelector("#itemX"),
      itemY: document.querySelector("#itemY"),
      itemSize: document.querySelector("#itemSize"),
      itemLetterSpacing: document.querySelector("#itemLetterSpacing"),
      itemWeight: document.querySelector("#itemWeight"),
      itemAlign: document.querySelector("#itemAlign"),
      itemColor: document.querySelector("#itemColor"),
      layoutName: document.querySelector("#layoutName"),
      savedLayouts: document.querySelector("#savedLayouts"),
      saveStatus: document.querySelector("#saveStatus"),
      saveNamedLayout: document.querySelector("#saveNamedLayout"),
      saveAsNamedLayout: document.querySelector("#saveAsNamedLayout"),
      resetNamedLayout: document.querySelector("#resetNamedLayout"),
      loadNamedLayout: document.querySelector("#loadNamedLayout"),
      jsonOut: document.querySelector("#jsonOut"),
      textList: document.querySelector("#textList"),
    };

    const presets = {
      origin: {role: "origin", name: "מדינה / בלנד", text: "קולומביה", x: 18, y: 13, size: 18, kerning: 30, weight: "400", align: "left"},
      title: {role: "title", name: "שם מרכזי", text: "שם הקפה", x: 48, y: 25, size: 28, kerning: 10, weight: "300", align: "center"},
      taste: {role: "tastes", name: "טעמים", text: "פירות יער | שוקולד | הדרים", x: 50, y: 37, size: 13, kerning: 30, weight: "400", align: "center"},
      roast: {role: "roast_level", name: "קלייה", text: "קלייה בינונית כהה", x: 28, y: 39, size: 13, kerning: 30, weight: "400", align: "center"},
      process: {role: "process_label", name: "עיבוד", text: "עיבוד:", x: 62, y: 44, size: 13, kerning: 30, weight: "400", align: "center"},
      kosher: {role: "kosher", name: "כשרות", text: "כשר בהשגחת הרבנות חתם סופר", x: 28, y: 44, size: 12.5, kerning: 30, weight: "300", align: "center"},
      weight: {role: "weight_value", name: "משקל", text: "1", x: 86, y: 28, size: 49, kerning: 0, weight: "300", align: "center"},
      weight_unit: {role: "weight_unit", name: "ק״ג", text: "ק״ג", x: 78, y: 28, size: 28, kerning: 30, weight: "300", align: "center"},
      date: {role: "roast_date", name: "תאריך", text: "", x: 85, y: 42, size: 13, kerning: 30, weight: "400", align: "center"},
      separator: {role: "separator", name: "מפריד", text: "|", x: 35.5, y: 89.4, size: 13, kerning: 0, weight: "400", align: "center"},
    };

    const masterKerningByRole = {
      title: 10,
      origin: 30,
      tastes: 30,
      roast_level: 30,
      process_label: 30,
      kosher: 30,
      roast_date: 30,
      weight_unit: 30,
      cup_score: 40,
      logo: 10,
      separator: 0,
      weight_value: 0,
    };

    let state = {
      source: "",
      page: 1,
      originalPdfPage: 1,
      sticker: {w: 100, h: 50},
      printSize: {w: 97, h: 97},
      weightVariants: {},
      viewScale: 4.2,
      background: {x: 0, y: 0, scale: 1, pageW: 100, pageH: 100, image: ""},
      mode: "text",
      selectedId: null,
      texts: [],
    };

    let activeLayoutId = "";
    let activeLayoutName = "";
    let savedDatabaseSnapshot = "";

    function numberValue(input, fallback) {
      const value = Number(input.value);
      return Number.isFinite(value) ? value : fallback;
    }

    function kerningToLetterSpacing(kerning, size) {
      return Number(size || 0) * Number(kerning || 0) / 1000;
    }

    function letterSpacingToKerning(letterSpacing, size) {
      const fontSize = Number(size || 0);
      if (!fontSize) return 0;
      return Math.round(Number(letterSpacing || 0) * 1000 / fontSize);
    }

    function itemKerning(item) {
      if (item.kerning !== undefined && item.kerning !== null) {
        return Number(item.kerning) || 0;
      }
      const role = roleForItem(item);
      if (masterKerningByRole[role] !== undefined) {
        return masterKerningByRole[role];
      }
      if (item.letterSpacing !== undefined && item.letterSpacing !== null) {
        return letterSpacingToKerning(item.letterSpacing, item.size);
      }
      if (item.letter_spacing_pt !== undefined && item.letter_spacing_pt !== null) {
        return letterSpacingToKerning(item.letter_spacing_pt, item.size || item.font_size_pt);
      }
      return 0;
    }

    function todayText() {
      const today = new Date();
      const dd = String(today.getDate()).padStart(2, "0");
      const mm = String(today.getMonth() + 1).padStart(2, "0");
      return `${dd}/${mm}/${today.getFullYear()}`;
    }

    function prettyWeight(value) {
      const normalized = String(value || "").trim();
      const compact = normalized.replace(new RegExp("\\\\s", "g"), "").replace("ק״ג", "").replace("קג", "");
      if (["0.25", ".25", "1/4", "¼"].includes(compact)) return "¼";
      if (["0.5", ".5", "1/2", "½"].includes(compact)) return "½";
      if (["1", "1.0"].includes(compact)) return "1";
      return normalized;
    }

    function markActiveWeight() {
      const current = prettyWeight(controls.contentWeight.value);
      for (const button of document.querySelectorAll("[data-weight]")) {
        button.classList.toggle("active", prettyWeight(button.dataset.weight) === current);
      }
    }

    function markActiveSticker() {
      for (const button of document.querySelectorAll("[data-sticker-page]")) {
        button.classList.toggle(
          "active",
          Number(button.dataset.stickerPage) === Number(state.originalPdfPage || state.page),
        );
      }
    }

    function activeItem() {
      return state.texts.find((item) => item.id === state.selectedId) || null;
    }

    function persistedState() {
      return {
        storageVersion: stateStorageVersion,
        ...editorState(),
        activeLayoutId,
        activeLayoutName,
        savedDatabaseSnapshot,
        viewScale: state.viewScale,
        selectedId: state.selectedId,
        mode: state.mode,
        compact: sidePanel.classList.contains("compact"),
        content: {
          title: controls.contentTitle.value,
          tastes: controls.contentTastes.value,
          roast: controls.contentRoast.value,
          weight: controls.contentWeight.value,
          date: controls.contentDate.value,
          process: controls.contentProcess.value,
          kosher: controls.contentKosher.value,
        },
      };
    }

    function saveCurrentState() {
      try {
        localStorage.setItem(stateStorageKey, JSON.stringify(persistedState()));
      } catch (error) {
        // Browser storage can be unavailable in private windows.
      }
    }

    function currentDatabaseSnapshot() {
      return JSON.stringify({
        name: controls.layoutName.value.trim(),
        layout: exportState(),
      });
    }

    function hasUnsavedDatabaseChanges() {
      return Boolean(activeLayoutId && savedDatabaseSnapshot && currentDatabaseSnapshot() !== savedDatabaseSnapshot);
    }

    function updateSaveStatus() {
      const dirty = hasUnsavedDatabaseChanges();
      controls.saveNamedLayout.disabled = !activeLayoutId || !dirty;
      controls.resetNamedLayout.disabled = !activeLayoutId || !dirty;
      controls.loadNamedLayout.disabled = !controls.savedLayouts.value;
      controls.saveStatus.classList.remove("clean", "dirty", "draft");
      if (!activeLayoutId) {
        controls.saveStatus.classList.add("draft");
        controls.saveStatus.textContent = "טיוטה מקומית - שמירה לא תדרוס פריסה קיימת";
        return;
      }
      if (dirty) {
        controls.saveStatus.classList.add("dirty");
        controls.saveStatus.textContent = `שינויים שלא נשמרו בפריסה: ${activeLayoutName || controls.layoutName.value.trim()}`;
        return;
      }
      controls.saveStatus.classList.add("clean");
      controls.saveStatus.textContent = `שמורה: ${activeLayoutName || controls.layoutName.value.trim()}`;
    }

    function markLayoutClean(layoutId, layoutName) {
      activeLayoutId = layoutId || "";
      activeLayoutName = layoutName || controls.layoutName.value.trim();
      savedDatabaseSnapshot = activeLayoutId ? currentDatabaseSnapshot() : "";
      updateSaveStatus();
      saveCurrentState();
    }

    function clearActiveLayout() {
      activeLayoutId = "";
      activeLayoutName = "";
      savedDatabaseSnapshot = "";
      controls.savedLayouts.value = "";
      updateSaveStatus();
      saveCurrentState();
    }

    function loadCurrentState() {
      try {
        const raw = localStorage.getItem(stateStorageKey);
        const parsed = raw ? JSON.parse(raw) : null;
        if (parsed && parsed.storageVersion !== stateStorageVersion) {
          parsed.printSize = {w: 97, h: 97};
        }
        return parsed;
      } catch (error) {
        return null;
      }
    }

    function applyPersistedContent(content) {
      if (!content) return;
      controls.contentTitle.value = content.title ?? controls.contentTitle.value;
      controls.contentTastes.value = content.tastes ?? controls.contentTastes.value;
      controls.contentRoast.value = content.roast ?? controls.contentRoast.value;
      controls.contentWeight.value = content.weight ?? controls.contentWeight.value;
      controls.contentDate.value = content.date ?? controls.contentDate.value;
      controls.contentProcess.value = content.process ?? controls.contentProcess.value;
      controls.contentKosher.value = content.kosher ?? controls.contentKosher.value;
      if (!controls.contentWeight.value.trim()) controls.contentWeight.value = "1";
    }

    function applyInputsToState() {
      state.source = controls.source.value;
      state.page = Math.max(1, Math.round(numberValue(controls.page, 1)));
      state.sticker.w = numberValue(controls.stickerW, 100);
      state.sticker.h = numberValue(controls.stickerH, 50);
      state.printSize.w = numberValue(controls.printW, 97);
      state.printSize.h = numberValue(controls.printH, 97);
      state.viewScale = numberValue(controls.viewScale, 4.2);
      state.background.scale = numberValue(controls.bgScale, 1);
      state.background.x = numberValue(controls.bgX, 0);
      state.background.y = numberValue(controls.bgY, 0);
    }

    function syncInputsFromState() {
      controls.stickerW.value = state.sticker.w;
      controls.stickerH.value = state.sticker.h;
      controls.printW.value = state.printSize?.w || state.sticker.w;
      controls.printH.value = state.printSize?.h || state.sticker.h;
      controls.viewScale.value = state.viewScale;
      controls.bgScale.value = state.background.scale;
      controls.bgX.value = round1(state.background.x);
      controls.bgY.value = round1(state.background.y);
      controls.page.value = state.page;
      if (state.source) controls.source.value = state.source;
      markActiveSticker();
    }

    function syncItemInputs() {
      const item = activeItem();
      const disabled = !item;
      for (const input of [controls.itemText, controls.itemX, controls.itemY, controls.itemSize, controls.itemLetterSpacing, controls.itemWeight, controls.itemAlign, controls.itemColor]) {
        input.disabled = disabled;
      }
      document.querySelector("#deleteItem").disabled = disabled;
      if (!item) {
        controls.itemText.value = "";
        controls.itemX.value = "";
        controls.itemY.value = "";
        controls.itemSize.value = "";
        controls.itemLetterSpacing.value = "";
        return;
      }
      controls.itemText.value = item.text;
      controls.itemX.value = round1(item.x);
      controls.itemY.value = round1(item.y);
      controls.itemSize.value = item.size;
      controls.itemLetterSpacing.value = itemKerning(item);
      controls.itemWeight.value = item.weight;
      controls.itemAlign.value = item.align;
      controls.itemColor.value = item.color || "#111111";
    }

    function round1(value) {
      return Math.round(value * 10) / 10;
    }

    function setViewZoom(value) {
      const min = Number(controls.viewScale.min || 2);
      const max = Number(controls.viewScale.max || 16);
      state.viewScale = Math.min(max, Math.max(min, value));
      controls.viewScale.value = state.viewScale;
      render();
    }

    function render() {
      applyInputsToState();
      const scale = state.viewScale;
      sticker.style.width = `${state.sticker.w * scale}px`;
      sticker.style.height = `${state.sticker.h * scale}px`;

      bgImage.src = state.background.image || "";
      bgImage.style.left = `${state.background.x * scale}px`;
      bgImage.style.top = `${state.background.y * scale}px`;
      bgImage.style.width = `${state.background.pageW * state.background.scale * scale}px`;
      bgImage.style.height = `${state.background.pageH * state.background.scale * scale}px`;

      textLayer.innerHTML = "";
      for (const item of state.texts) {
        const node = document.createElement("div");
        node.className = `text-item${item.id === state.selectedId ? " selected" : ""}`;
        node.dataset.id = item.id;
        node.dataset.align = item.align;
        node.textContent = item.text;
        node.style.left = `${item.x * scale}px`;
        node.style.top = `${item.y * scale}px`;
        node.style.fontSize = `${item.size * scale / 2.834}px`;
        node.style.letterSpacing = `${kerningToLetterSpacing(itemKerning(item), item.size) * scale / 2.834}px`;
        node.style.fontWeight = item.weight;
        node.style.color = item.color || "#111111";
        node.addEventListener("pointerdown", startTextDrag);
        node.addEventListener("click", (event) => {
          event.stopPropagation();
          selectItem(item.id);
        });
        textLayer.appendChild(node);
      }

      renderTextList();
      syncItemInputs();
      readout.textContent = `${round1(state.sticker.w)}×${round1(state.sticker.h)} מ״מ`;
      controls.jsonOut.value = JSON.stringify(exportState(), null, 2);
      markActiveSticker();
      updateSaveStatus();
      saveCurrentState();
    }

    function renderTextList() {
      controls.textList.innerHTML = "";
      for (const item of state.texts) {
        const button = document.createElement("button");
        button.className = `row-button${item.id === state.selectedId ? " active" : ""}`;
        button.type = "button";
        button.innerHTML = `<span>${escapeHtml(item.name)}</span><span>${round1(item.x)}, ${round1(item.y)}</span>`;
        button.addEventListener("click", () => selectItem(item.id));
        controls.textList.appendChild(button);
      }
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;",
      }[char]));
    }

    function selectItem(id) {
      state.selectedId = id;
      render();
    }

    function addText(kind) {
      const preset = presets[kind];
      const item = {
        id: crypto.randomUUID(),
        color: "#111111",
        kerning: 0,
        ...preset,
      };
      state.texts.push(item);
      state.selectedId = item.id;
      render();
    }

    function roleForItem(item) {
      if (item.role) return item.role;
      const text = (item.text || "").trim();
      if (item.name === "שם מרכזי") return "title";
      if (item.name === "תאריך" || new RegExp("^\\\\d{2}/\\\\d{2}/\\\\d{4}$").test(text) || text.includes("תאריך קלייה")) return "roast_date";
      if (text === "ק״ג") return "weight_unit";
      if (/^\\d+(\\.\\d+)?\\s*$/.test(text) || text.includes("ק״ג")) return "weight_value";
      if (text === "עיבוד:" || text.startsWith("עיבוד:")) return "process_label";
      if (text.includes("כשר")) return "kosher";
      if (text.includes("קלייה")) return "roast_level";
      if (text === "|") return "separator";
      if (text) return "tastes";
      return "";
    }

    function itemsByRole() {
      const roles = {};
      for (const item of state.texts) {
        const role = roleForItem(item);
        item.role = role || item.role || "";
        if (role && !roles[role]) roles[role] = item;
      }
      return roles;
    }

    function syncContentFromTexts() {
      const roles = itemsByRole();
      if (roles.title) controls.contentTitle.value = roles.title.text;
      if (roles.tastes) controls.contentTastes.value = roles.tastes.text;
      if (roles.roast_level) controls.contentRoast.value = roles.roast_level.text;
      if (roles.weight_value) controls.contentWeight.value = roles.weight_value.text.replace("ק״ג", "").trim();
      if (roles.roast_date) controls.contentDate.value = roles.roast_date.text.replace("תאריך קלייה:", "").trim() || todayText();
      if (roles.process_label) controls.contentProcess.value = roles.process_label.text.replace("עיבוד:", "").trim();
      if (roles.kosher) controls.contentKosher.value = roles.kosher.text;
      if (!controls.contentDate.value.trim()) controls.contentDate.value = todayText();
      if (!controls.contentWeight.value.trim()) controls.contentWeight.value = "1";
      markActiveWeight();
    }

    function setRoleText(role, text) {
      const roles = itemsByRole();
      if (roles[role]) {
        roles[role].text = text;
      }
      return roles[role] || null;
    }

    function weightVariantKey(value) {
      const pretty = prettyWeight(value);
      if (pretty === "½") return "0.5";
      if (pretty === "¼") return "0.25";
      return "";
    }

    function activeWeightItem() {
      return itemsByRole().weight_value || null;
    }

    function captureWeightVariant(value) {
      const item = activeWeightItem();
      const key = weightVariantKey(value);
      if (!item || !key) {
        statusEl.textContent = "בחר שדה משקל ושמור וריאנט של ½ או ¼.";
        return;
      }
      state.weightVariants[key] = {
        x: item.x,
        y: item.y,
        size: item.size,
        kerning: itemKerning(item),
        align: item.align || "center",
      };
      statusEl.textContent = `נשמר מיקום משקל ${prettyWeight(value)} לפריסה הזו.`;
      render();
    }

    function applyWeightVariant(value, item) {
      const key = weightVariantKey(value);
      const variant = key ? state.weightVariants[key] : null;
      if (!item || !variant) return;
      item.x = variant.x;
      item.y = variant.y;
      item.size = variant.size;
      item.kerning = variant.kerning ?? letterSpacingToKerning(variant.letterSpacing || 0, item.size);
      item.align = variant.align || item.align;
    }

    function applyMasterKerning() {
      for (const item of state.texts) {
        const role = roleForItem(item);
        item.kerning = masterKerningByRole[role] ?? 0;
        delete item.letterSpacing;
      }
      statusEl.textContent = "הוחל Kerning לפי קובץ המאסטר.";
      render();
    }

    function applyContent() {
      if (!controls.contentDate.value.trim()) {
        controls.contentDate.value = todayText();
      }
      setRoleText("title", controls.contentTitle.value);
      setRoleText("tastes", controls.contentTastes.value);
      setRoleText("roast_level", controls.contentRoast.value);
      const weightItem = setRoleText("weight_value", prettyWeight(controls.contentWeight.value));
      if (weightItem && Number(weightItem.size || 0) < 49) {
        weightItem.size = 49;
      }
      applyWeightVariant(controls.contentWeight.value, weightItem);
      setRoleText("roast_date", controls.contentDate.value);
      setRoleText("process_label", controls.contentProcess.value ? `עיבוד: ${controls.contentProcess.value}` : "עיבוד:");
      setRoleText("kosher", controls.contentKosher.value);
      markActiveWeight();
      render();
    }

    function resetDailyContent() {
      controls.contentTitle.value = "אתיופיה דג׳ימה";
      controls.contentTastes.value = "חזק, אש, מעושן";
      controls.contentRoast.value = "קלייה בינונית כהה";
      controls.contentWeight.value = "1";
      controls.contentDate.value = todayText();
      controls.contentProcess.value = "";
      controls.contentKosher.value = "כשר בהשגחת הרבנות חתם סופר";
      applyContent();
    }

    function updateActiveItem() {
      const item = activeItem();
      if (!item) return;
      item.text = controls.itemText.value;
      item.x = numberValue(controls.itemX, item.x);
      item.y = numberValue(controls.itemY, item.y);
      item.size = numberValue(controls.itemSize, item.size);
      item.kerning = numberValue(controls.itemLetterSpacing, itemKerning(item));
      delete item.letterSpacing;
      item.weight = controls.itemWeight.value;
      item.align = controls.itemAlign.value;
      item.color = controls.itemColor.value;
      render();
    }

    async function loadSource() {
      applyInputsToState();
      statusEl.textContent = "טוען רקע...";
      const form = new FormData();
      form.append("source", state.source);
      form.append("page", String(state.page));
      if (controls.pdfUpload.files[0]) {
        form.append("pdf", controls.pdfUpload.files[0]);
      }
      const response = await fetch("/render-source", {method: "POST", body: form});
      const payload = await response.json();
      if (!response.ok) {
        statusEl.textContent = payload.error || "טעינת הרקע נכשלה.";
        return;
      }
      state.background.image = payload.image;
      state.background.pageW = payload.page_width_mm || state.sticker.w;
      state.background.pageH = payload.page_height_mm || state.sticker.h;
      statusEl.textContent = "הרקע נטען. אפשר לגרור אותו במצב הזזת רקע.";
      render();
    }

    async function chooseSticker(page) {
      const pageNumber = Number(page);
      const choice = stickerChoices[pageNumber];
      state.source = choice?.image || stickerPdfSource;
      state.page = 1;
      state.originalPdfPage = pageNumber;
      controls.source.value = state.source;
      controls.page.value = state.page;
      controls.layoutName.value = choice?.label || `מדבקה עמוד ${pageNumber}`;
      clearActiveLayout();
      saveCurrentState();
      markActiveSticker();
      await loadSource();
      statusEl.textContent = `נבחרה מדבקת ${choice?.label || pageNumber}.`;
    }

    function editorState() {
      return {
        source: state.source,
        page: state.page,
        originalPdf: stickerPdfSource,
        originalPdfPage: state.originalPdfPage || state.page,
        sticker: {...state.sticker},
        printSize: {...state.printSize},
        weightVariants: {...state.weightVariants},
        background: {
          x: state.background.x,
          y: state.background.y,
          scale: state.background.scale,
          pageW: state.background.pageW,
          pageH: state.background.pageH,
        },
        texts: state.texts.map((item) => ({...item})),
      };
    }

    function layoutParameters() {
      const mmToPt = 72 / 25.4;
      const stickerWidthPt = state.sticker.w * mmToPt;
      const stickerHeightPt = state.sticker.h * mmToPt;
      const printWidthPt = state.printSize.w * mmToPt;
      const printHeightPt = state.printSize.h * mmToPt;
      const textFields = state.texts.map((item) => {
        const xPt = item.x * mmToPt;
        const yFromTopPt = item.y * mmToPt;
        const kerning = itemKerning(item);
        const letterSpacingPt = kerningToLetterSpacing(kerning, item.size);
        return {
          id: item.id,
          role: roleForItem(item),
          name: item.name,
          text: item.text,
          x_mm: round1(item.x),
          y_mm_from_top: round1(item.y),
          x_pt: round2(xPt),
          y_pt_from_bottom: round2(stickerHeightPt - yFromTopPt),
          font_family: "Karantina",
          font_weight: item.weight,
          font_size_pt: item.size,
          kerning,
          letter_spacing_pt: round2(letterSpacingPt),
          align: item.align,
          rtl: true,
          color: item.color || "#111111",
        };
      });
      return {
        purpose: "label_layout_parameters",
        units: {
          editor_positions: "millimeters from top-left",
          reportlab_positions: "points from bottom-left",
          font_sizes: "points",
        },
        source: {
          file: state.source,
          page: state.page,
          original_pdf: stickerPdfSource,
          original_pdf_page: state.originalPdfPage || state.page,
        },
        sticker: {
          width_mm: round1(state.sticker.w),
          height_mm: round1(state.sticker.h),
          width_pt: round2(stickerWidthPt),
          height_pt: round2(stickerHeightPt),
        },
        print_size: {
          width_mm: round1(state.printSize.w),
          height_mm: round1(state.printSize.h),
          width_pt: round2(printWidthPt),
          height_pt: round2(printHeightPt),
          note: "Used only for text-only PDF output. Preview/layout coordinates stay based on sticker size.",
        },
        weight_variants: {
          "0.5": state.weightVariants["0.5"] || null,
          "0.25": state.weightVariants["0.25"] || null,
          note: "Optional manual x/y/size overrides for fractional weight labels.",
        },
        background_crop: {
          x_mm: round1(state.background.x),
          y_mm_from_top: round1(state.background.y),
          x_pt: round2(state.background.x * mmToPt),
          y_pt_from_bottom: round2(stickerHeightPt - ((state.background.y + state.background.pageH * state.background.scale) * mmToPt)),
          scale: Number(state.background.scale),
          source_width_mm: round1(state.background.pageW),
          source_height_mm: round1(state.background.pageH),
          rendered_width_mm: round1(state.background.pageW * state.background.scale),
          rendered_height_mm: round1(state.background.pageH * state.background.scale),
        },
        text_fields: textFields,
        reportlab_hint: {
          canvas_size: [round2(stickerWidthPt), round2(stickerHeightPt)],
          print_canvas_size: [round2(printWidthPt), round2(printHeightPt)],
          draw_background: "drawImage(background, x_pt, y_pt_from_bottom, width=rendered_width_mm*mm, height=rendered_height_mm*mm) and clip/crop to sticker bounds",
          draw_text: "draw text at x_pt, y_pt_from_bottom using align, font_size_pt, and kerning; kerning is Illustrator-style tracking, where 30 means 30/1000 of font size",
        },
      };
    }

    function exportState() {
      return {
        purpose: "label_layout_preset",
        version: 1,
        name: controls.layoutName.value.trim(),
        editor_state: editorState(),
        parameters: layoutParameters(),
      };
    }

    function round2(value) {
      return Math.round(value * 100) / 100;
    }

    async function saveLayout() {
      const response = await fetch("/save-layout", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(exportState()),
      });
      const payload = await response.json();
      statusEl.textContent = payload.ok ? `נשמר: ${payload.path}` : "השמירה נכשלה.";
    }

    async function textOnlyPdfBlob() {
      applyContent();
      const response = await fetch("/text-only.pdf", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(exportState()),
      });
      if (!response.ok) {
        statusEl.textContent = "יצירת PDF נכשלה.";
        return null;
      }
      return response.blob();
    }

    async function downloadTextOnlyPdf() {
      const blob = await textOnlyPdfBlob();
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "label-text-only.pdf";
      link.click();
      URL.revokeObjectURL(url);
      statusEl.textContent = "PDF טקסט בלבד נוצר.";
    }

    async function printTextOnlyPdf() {
      const blob = await textOnlyPdfBlob();
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const frame = document.createElement("iframe");
      frame.style.position = "fixed";
      frame.style.right = "0";
      frame.style.bottom = "0";
      frame.style.width = "0";
      frame.style.height = "0";
      frame.style.border = "0";
      frame.src = url;
      frame.onload = () => {
        frame.contentWindow.focus();
        frame.contentWindow.print();
      };
      document.body.appendChild(frame);
      statusEl.textContent = "נפתח חלון הדפסה לטקסט בלבד.";
      setTimeout(() => {
        URL.revokeObjectURL(url);
        frame.remove();
      }, 120000);
    }

    function normalizeLayout(layout) {
      if (!layout) return null;
      if (layout.layout) {
        return normalizeLayout(layout.layout);
      }
      if (layout.editor_state) {
        return layout.editor_state;
      }
      if (layout.parameters) {
        return normalizeLayout(layout.parameters);
      }
      if (layout.text_fields) {
        return {
          source: layout.source?.file || "",
          page: layout.source?.page || 1,
          originalPdfPage: layout.source?.original_pdf_page || layout.source?.page || 1,
          sticker: {
            w: layout.sticker?.width_mm || 100,
            h: layout.sticker?.height_mm || 100,
          },
          printSize: {
            w: layout.print_size?.width_mm || 97,
            h: layout.print_size?.height_mm || 97,
          },
          weightVariants: layout.weight_variants || {},
          background: {
            x: layout.background_crop?.x_mm || 0,
            y: layout.background_crop?.y_mm_from_top || 0,
            scale: layout.background_crop?.scale || 1,
            pageW: layout.background_crop?.source_width_mm || 100,
            pageH: layout.background_crop?.source_height_mm || 100,
          },
          texts: layout.text_fields.map((field) => {
            const item = {
              id: field.id || crypto.randomUUID(),
              role: field.role || "",
              name: field.name || "טקסט",
              text: field.text || "",
              x: field.x_mm || 0,
              y: field.y_mm_from_top || 0,
              size: field.font_size_pt || 12,
              weight: String(field.font_weight || "400"),
              align: field.align || "center",
              color: field.color || "#111111",
            };
            if (field.kerning !== undefined && field.kerning !== null) {
              item.kerning = field.kerning;
            } else if (field.letter_spacing_pt !== undefined && field.letter_spacing_pt !== null) {
              item.letterSpacing = field.letter_spacing_pt;
            }
            return item;
          }),
        };
      }
      return layout;
    }

    function sourceForOriginalPage(page, fallbackSource) {
      const pageNumber = Number(page);
      return stickerChoices[pageNumber]?.image || fallbackSource || "";
    }

    function applyLayout(layout, options = {}) {
      const normalized = normalizeLayout(layout);
      if (!normalized) return false;
      const image = options.keepImage ? state.background.image : "";
      const originalPdfPage = normalized.originalPdfPage || normalized.original_pdf_page || normalized.originalPdf?.page || normalized.source?.original_pdf_page || normalized.page || 1;
      const source = sourceForOriginalPage(originalPdfPage, normalized.source);
      state = {
        ...state,
        source,
        page: source === normalized.source ? normalized.page || 1 : 1,
        originalPdfPage,
        sticker: {
          w: normalized.sticker?.w || normalized.sticker?.width_mm || 100,
          h: normalized.sticker?.h || normalized.sticker?.height_mm || 100,
        },
        printSize: {
          w: normalized.printSize?.w || normalized.printSize?.width_mm || normalized.print_size?.width_mm || 97,
          h: normalized.printSize?.h || normalized.printSize?.height_mm || normalized.print_size?.height_mm || 97,
        },
        weightVariants: normalized.weightVariants || normalized.weight_variants || {},
        background: {
          ...state.background,
          ...(normalized.background || {}),
          image,
          pageW: normalized.background?.pageW || normalized.background?.source_width_mm || state.background.pageW,
          pageH: normalized.background?.pageH || normalized.background?.source_height_mm || state.background.pageH,
        },
        texts: (normalized.texts || []).map((item) => {
          const normalizedItem = {
            id: item.id || crypto.randomUUID(),
            color: item.color || "#111111",
            role: item.role || "",
            name: item.name || "טקסט",
            text: item.text || "",
            x: item.x || item.x_mm || 0,
            y: item.y || item.y_mm_from_top || 0,
            size: item.size || item.font_size_pt || 12,
            weight: String(item.weight || item.font_weight || "400"),
            align: item.align || "center",
          };
          if (item.kerning !== undefined && item.kerning !== null) {
            normalizedItem.kerning = item.kerning;
          } else if (item.letterSpacing !== undefined && item.letterSpacing !== null) {
            normalizedItem.letterSpacing = item.letterSpacing;
          } else if (item.letter_spacing_pt !== undefined && item.letter_spacing_pt !== null) {
            normalizedItem.letter_spacing_pt = item.letter_spacing_pt;
          }
          normalizedItem.kerning = itemKerning(normalizedItem);
          delete normalizedItem.letterSpacing;
          delete normalizedItem.letter_spacing_pt;
          return normalizedItem;
        }),
        mode: "text",
      };
      state.selectedId = state.texts[0]?.id || null;
      syncInputsFromState();
      syncContentFromTexts();
      render();
      return true;
    }

    async function loadSaved() {
      const response = await fetch("/load-layout");
      const payload = await response.json();
      if (!payload.layout) {
        statusEl.textContent = "אין עדיין שמירה.";
        return;
      }
      applyLayout(payload.layout, {keepImage: true});
      statusEl.textContent = "השמירה נטענה. לחץ טעינת רקע אם צריך לרענן את התמונה.";
    }

    async function refreshLayoutList() {
      const response = await fetch("/layouts");
      const payload = await response.json();
      const selected = controls.savedLayouts.value || activeLayoutId;
      controls.savedLayouts.innerHTML = "";
      const newOption = document.createElement("option");
      newOption.value = "";
      newOption.textContent = "פריסה חדשה";
      controls.savedLayouts.appendChild(newOption);
      for (const layout of payload.layouts || []) {
        const option = document.createElement("option");
        option.value = layout.id;
        option.textContent = `${layout.name} (${layout.page || "-"})`;
        controls.savedLayouts.appendChild(option);
      }
      if (selected) {
        controls.savedLayouts.value = selected;
      }
    }

    async function postLayoutSave({forceNew = false, id = ""} = {}) {
      const name = controls.layoutName.value.trim();
      if (!name) {
        statusEl.textContent = "צריך לתת שם לפריסה לפני שמירה.";
        return null;
      }
      const response = await fetch("/layouts", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          id,
          name,
          layout: exportState(),
          force_new: forceNew,
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        statusEl.textContent = payload.error || "השמירה למאגר נכשלה.";
        return null;
      }
      await refreshLayoutList();
      controls.savedLayouts.value = payload.layout.id;
      markLayoutClean(payload.layout.id, payload.layout.name);
      return payload.layout;
    }

    async function saveNamedLayout() {
      if (!activeLayoutId) {
        const saved = await postLayoutSave({forceNew: true});
        if (saved) {
          statusEl.textContent = `נשמרה פריסה חדשה: ${saved.name}`;
        }
        return;
      }
      if (!hasUnsavedDatabaseChanges()) {
        statusEl.textContent = "אין שינויים לשמירה.";
        return;
      }
      if (!window.confirm("לעדכן את הפריסה השמורה עם השינויים הנוכחיים?")) {
        statusEl.textContent = "השמירה בוטלה.";
        return;
      }
      const saved = await postLayoutSave({id: activeLayoutId});
      if (saved) {
        statusEl.textContent = `עודכנה הפריסה: ${saved.name}`;
      }
    }

    async function saveAsNamedLayout() {
      const saved = await postLayoutSave({forceNew: true});
      if (saved) {
        statusEl.textContent = `נשמרה פריסה חדשה: ${saved.name}`;
      }
    }

    async function loadNamedLayout() {
      const id = controls.savedLayouts.value;
      if (!id) return;
      if (hasUnsavedDatabaseChanges() && !window.confirm("יש שינויים שלא נשמרו. לטעון פריסה אחרת ולזרוק את הטיוטה?")) {
        statusEl.textContent = "הטעינה בוטלה.";
        return;
      }
      const response = await fetch(`/layouts/${encodeURIComponent(id)}`);
      const payload = await response.json();
      if (!response.ok) {
        statusEl.textContent = payload.error || "טעינת הפריסה נכשלה.";
        return;
      }
      controls.layoutName.value = payload.layout.name;
      applyLayout(payload.layout.layout, {keepImage: false});
      markLayoutClean(payload.layout.id, payload.layout.name);
      await loadSource();
      statusEl.textContent = `נטענה פריסה: ${payload.layout.name}`;
    }

    async function resetNamedLayout() {
      if (!activeLayoutId) {
        statusEl.textContent = "אין פריסה שמורה לחזור אליה.";
        return;
      }
      const response = await fetch(`/layouts/${encodeURIComponent(activeLayoutId)}`);
      const payload = await response.json();
      if (!response.ok) {
        statusEl.textContent = payload.error || "טעינת הפריסה השמורה נכשלה.";
        return;
      }
      controls.layoutName.value = payload.layout.name;
      applyLayout(payload.layout.layout, {keepImage: false});
      markLayoutClean(payload.layout.id, payload.layout.name);
      await loadSource();
      statusEl.textContent = `חזרת לגרסה השמורה: ${payload.layout.name}`;
    }

    function setMode(mode) {
      state.mode = mode;
      document.querySelector("#textMode").classList.toggle("active", mode === "text");
      document.querySelector("#bgMode").classList.toggle("active", mode === "background");
    }

    function setCompact(compact) {
      sidePanel.classList.toggle("compact", compact);
      document.querySelector("#toggleCompact").textContent = compact ? "הצג עריכה מלאה" : "הסתר עריכה";
      statusEl.textContent = compact ? "מצב פשוט: בחר פריסה, עדכן תוכן והדפס." : "מצב עריכה מלא: אפשר להזיז רקע וטקסטים.";
      saveCurrentState();
    }

    function startTextDrag(event) {
      if (state.mode !== "text") return;
      event.preventDefault();
      const item = state.texts.find((candidate) => candidate.id === event.currentTarget.dataset.id);
      if (!item) return;
      state.selectedId = item.id;
      const scale = state.viewScale;
      const startX = event.clientX;
      const startY = event.clientY;
      const initial = {x: item.x, y: item.y};
      event.currentTarget.setPointerCapture(event.pointerId);
      const move = (moveEvent) => {
        item.x = initial.x + (moveEvent.clientX - startX) / scale;
        item.y = initial.y + (moveEvent.clientY - startY) / scale;
        render();
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    }

    sticker.addEventListener("pointerdown", (event) => {
      if (state.mode !== "background") return;
      event.preventDefault();
      const scale = state.viewScale;
      const startX = event.clientX;
      const startY = event.clientY;
      const initial = {x: state.background.x, y: state.background.y};
      sticker.setPointerCapture(event.pointerId);
      const move = (moveEvent) => {
        state.background.x = initial.x + (moveEvent.clientX - startX) / scale;
        state.background.y = initial.y + (moveEvent.clientY - startY) / scale;
        syncInputsFromState();
        render();
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });

    sticker.addEventListener("click", () => {
      if (state.mode === "text") selectItem(null);
    });

    for (const input of [controls.stickerW, controls.stickerH, controls.printW, controls.printH, controls.viewScale, controls.bgScale, controls.bgX, controls.bgY]) {
      input.addEventListener("input", render);
    }

    for (const input of [controls.itemText, controls.itemX, controls.itemY, controls.itemSize, controls.itemLetterSpacing, controls.itemWeight, controls.itemAlign, controls.itemColor]) {
      input.addEventListener("input", updateActiveItem);
    }

    for (const input of [controls.contentTitle, controls.contentTastes, controls.contentRoast, controls.contentWeight, controls.contentDate, controls.contentProcess, controls.contentKosher]) {
      input.addEventListener("input", applyContent);
    }

    for (const input of [controls.source, controls.page, controls.layoutName]) {
      input.addEventListener("input", saveCurrentState);
      input.addEventListener("change", saveCurrentState);
    }
    controls.layoutName.addEventListener("input", updateSaveStatus);
    controls.savedLayouts.addEventListener("change", () => {
      updateSaveStatus();
      if (controls.savedLayouts.value) {
        loadNamedLayout();
      }
    });

    document.querySelector("#loadSource").addEventListener("click", loadSource);
    document.querySelector("#applyContent").addEventListener("click", applyContent);
    document.querySelector("#newLabel").addEventListener("click", resetDailyContent);
    document.querySelector("#todayDate").addEventListener("click", () => {
      controls.contentDate.value = todayText();
      applyContent();
    });
    for (const button of document.querySelectorAll("[data-weight]")) {
      button.addEventListener("click", () => {
        controls.contentWeight.value = button.dataset.weight;
        applyContent();
      });
    }
    for (const button of document.querySelectorAll("[data-sticker-page]")) {
      button.addEventListener("click", () => chooseSticker(button.dataset.stickerPage));
    }
    document.querySelector("#saveLayout").addEventListener("click", saveLayout);
    document.querySelector("#loadSaved").addEventListener("click", loadSaved);
    document.querySelector("#saveNamedLayout").addEventListener("click", saveNamedLayout);
    document.querySelector("#saveAsNamedLayout").addEventListener("click", saveAsNamedLayout);
    document.querySelector("#resetNamedLayout").addEventListener("click", resetNamedLayout);
    document.querySelector("#loadNamedLayout").addEventListener("click", loadNamedLayout);
    document.querySelector("#refreshLayouts").addEventListener("click", refreshLayoutList);
    document.querySelector("#copyJson").addEventListener("click", async () => {
      await navigator.clipboard.writeText(controls.jsonOut.value);
      statusEl.textContent = "ה-JSON הועתק.";
    });
    document.querySelector("#downloadJson").addEventListener("click", () => {
      const blob = new Blob([controls.jsonOut.value], {type: "application/json;charset=utf-8"});
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "label-layout-parameters.json";
      link.click();
      URL.revokeObjectURL(url);
    });
    document.querySelector("#downloadPdf").addEventListener("click", downloadTextOnlyPdf);
    document.querySelector("#printPdf").addEventListener("click", printTextOnlyPdf);
    document.querySelector("#printPdfTop").addEventListener("click", printTextOnlyPdf);
    document.querySelector("#printPdfSide").addEventListener("click", printTextOnlyPdf);
    document.querySelector("#toggleCompact").addEventListener("click", () => {
      setCompact(!sidePanel.classList.contains("compact"));
    });
    document.querySelector("#deleteItem").addEventListener("click", () => {
      state.texts = state.texts.filter((item) => item.id !== state.selectedId);
      state.selectedId = state.texts[0]?.id || null;
      render();
    });
    document.querySelector("#applyMasterKerning").addEventListener("click", applyMasterKerning);
    document.querySelector("#textMode").addEventListener("click", () => setMode("text"));
    document.querySelector("#bgMode").addEventListener("click", () => setMode("background"));
    document.querySelector("#zoomIn").addEventListener("click", () => setViewZoom(state.viewScale + 0.5));
    document.querySelector("#zoomOut").addEventListener("click", () => setViewZoom(state.viewScale - 0.5));
    document.querySelector("#zoomReset").addEventListener("click", () => setViewZoom(4.2));
    document.querySelector("#saveWeightHalf").addEventListener("click", () => captureWeightVariant("0.5"));
    document.querySelector("#saveWeightQuarter").addEventListener("click", () => captureWeightVariant("0.25"));

    for (const button of document.querySelectorAll("[data-add]")) {
      button.addEventListener("click", () => addText(button.dataset.add));
    }

    const savedState = loadCurrentState();
    if (savedState) {
      activeLayoutId = savedState.activeLayoutId || "";
      activeLayoutName = savedState.activeLayoutName || "";
      savedDatabaseSnapshot = savedState.savedDatabaseSnapshot || "";
      applyLayout(savedState, {keepImage: false});
      state.viewScale = savedState.viewScale || state.viewScale;
      state.selectedId = savedState.selectedId || state.selectedId;
      state.mode = savedState.mode || "text";
      applyPersistedContent(savedState.content);
      if (!savedState.content?.weight) {
        controls.contentWeight.value = "1";
      }
      syncInputsFromState();
      applyContent();
      setCompact(savedState.compact !== false);
    } else {
      if (controls.source.options.length) {
        state.source = controls.source.value;
      }
      controls.contentDate.value = todayText();
      addText("origin");
      addText("title");
      addText("taste");
      addText("roast");
      addText("process");
      addText("kosher");
      addText("weight");
      addText("weight_unit");
      addText("date");
      syncContentFromTexts();
      applyContent();
      render();
      setCompact(true);
    }
    refreshLayoutList();
    loadSource();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True, use_reloader=False)
