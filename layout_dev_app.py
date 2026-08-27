from __future__ import annotations

import base64
import json
import re
import subprocess
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


BASE_DIR = Path(__file__).resolve().parent
GRID_DIR = BASE_DIR / "grid"
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
    leading = size * 0.95
    lines = text.splitlines() or [text]
    start_y = y_center + ((len(lines) - 1) * leading / 2)
    font = font_name(field.get("font_weight"))
    align = field.get("align") or "center"
    rtl = field.get("rtl", True)

    c.setFillColor(HexColor(field.get("color") or "#111111"))
    c.setFont(font, size)
    for index, line in enumerate(lines):
        display = get_display(line) if rtl else line
        y = start_y - index * leading
        if align == "right":
            c.drawRightString(x, y, display)
        elif align == "left":
            c.drawString(x, y, display)
        else:
            c.drawCentredString(x, y, display)


def read_layout_db() -> dict:
    if not LAYOUT_DB_PATH.exists():
        return {"layouts": []}
    return json.loads(LAYOUT_DB_PATH.read_text(encoding="utf-8"))


def write_layout_db(data: dict) -> None:
    GRID_DIR.mkdir(exist_ok=True)
    LAYOUT_DB_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
    sources = []
    for path in sorted(GRID_DIR.glob("*.pdf")):
        sources.append({"kind": "pdf", "name": path.name, "path": str(path.relative_to(BASE_DIR))})
    for path in sorted((GRID_DIR / "backgrounds").glob("*.png")):
        sources.append({"kind": "image", "name": path.stem, "path": str(path.relative_to(BASE_DIR))})
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
        candidate = (BASE_DIR / source).resolve()
        if BASE_DIR not in candidate.parents and candidate != BASE_DIR:
            return jsonify({"error": "Invalid source."}), 400
        if not candidate.exists():
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
    GRID_DIR.mkdir(exist_ok=True)
    LAYOUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "path": str(LAYOUT_PATH.relative_to(BASE_DIR))})


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
    if not name:
        return jsonify({"error": "Layout name is required."}), 400
    if not isinstance(layout, dict):
        return jsonify({"error": "Layout data is required."}), 400

    db = read_layout_db()
    layouts = db.setdefault("layouts", [])
    if not layout_id:
        existing = next((item for item in layouts if item.get("name") == name), None)
        layout_id = existing["id"] if existing else uuid.uuid4().hex

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
    return jsonify({"ok": True, "layout": layout_summary(record), "path": str(LAYOUT_DB_PATH.relative_to(BASE_DIR))})


@app.post("/text-only.pdf")
def text_only_pdf():
    payload = request.get_json(force=True)
    parameters = payload.get("parameters", payload)
    sticker = parameters.get("sticker", {})
    width = float(sticker.get("width_pt") or 283.46)
    height = float(sticker.get("height_pt") or 283.46)
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))
    for field in parameters.get("text_fields", []):
        draw_text_field(c, field)
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
      font-size: 14px;
      color: var(--muted);
      font-weight: 700;
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
        <p class="section-title">תוכן למשתמש</p>
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
            עיבוד
            <input id="contentProcess" type="text">
          </label>
          <label>
            כשרות
            <input id="contentKosher" type="text" value="כשר בהשגחת הרבנות חתם סופר">
          </label>
        </div>
        <div class="buttons">
          <button id="applyContent">עדכון טקסטים</button>
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
            <input id="viewScale" type="range" min="2" max="8" step="0.1" value="4.2">
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
          <button class="danger" id="deleteItem">מחיקת טקסט</button>
        </div>
      </section>

      <section class="section layout-section">
        <p class="section-title">מאגר פריסות</p>
        <label>
          שם פריסה
          <input id="layoutName" type="text" value="מדבקה עמוד 6">
        </label>
        <label style="margin-top:10px">
          פריסות שמורות
          <select id="savedLayouts"></select>
        </label>
        <div class="buttons" style="margin-top:10px">
          <button id="saveNamedLayout">שמירה למאגר</button>
          <button class="secondary" id="loadNamedLayout">טעינת פריסה</button>
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
      contentProcess: document.querySelector("#contentProcess"),
      contentKosher: document.querySelector("#contentKosher"),
      itemText: document.querySelector("#itemText"),
      itemX: document.querySelector("#itemX"),
      itemY: document.querySelector("#itemY"),
      itemSize: document.querySelector("#itemSize"),
      itemWeight: document.querySelector("#itemWeight"),
      itemAlign: document.querySelector("#itemAlign"),
      itemColor: document.querySelector("#itemColor"),
      layoutName: document.querySelector("#layoutName"),
      savedLayouts: document.querySelector("#savedLayouts"),
      jsonOut: document.querySelector("#jsonOut"),
      textList: document.querySelector("#textList"),
    };

    const presets = {
      origin: {role: "origin", name: "מדינה / בלנד", text: "קולומביה", x: 18, y: 13, size: 18, weight: "400", align: "left"},
      title: {role: "title", name: "שם מרכזי", text: "שם הקפה", x: 48, y: 25, size: 28, weight: "300", align: "center"},
      taste: {role: "tastes", name: "טעמים", text: "פירות יער | שוקולד | הדרים", x: 50, y: 37, size: 13, weight: "400", align: "center"},
      roast: {role: "roast_level", name: "קלייה", text: "קלייה בינונית כהה", x: 28, y: 39, size: 13, weight: "400", align: "center"},
      process: {role: "process_label", name: "עיבוד", text: "עיבוד:", x: 62, y: 44, size: 13, weight: "400", align: "center"},
      kosher: {role: "kosher", name: "כשרות", text: "כשר בהשגחת הרבנות חתם סופר", x: 28, y: 44, size: 12.5, weight: "300", align: "center"},
      weight: {role: "weight_value", name: "משקל", text: "1", x: 86, y: 28, size: 28, weight: "300", align: "center"},
      weight_unit: {role: "weight_unit", name: "ק״ג", text: "ק״ג", x: 78, y: 28, size: 28, weight: "300", align: "center"},
      date: {role: "roast_date", name: "תאריך", text: "", x: 85, y: 42, size: 13, weight: "400", align: "center"},
    };

    let state = {
      source: "",
      page: 1,
      sticker: {w: 100, h: 50},
      viewScale: 4.2,
      background: {x: 0, y: 0, scale: 1, pageW: 100, pageH: 100, image: ""},
      mode: "text",
      selectedId: null,
      texts: [],
    };

    function numberValue(input, fallback) {
      const value = Number(input.value);
      return Number.isFinite(value) ? value : fallback;
    }

    function todayText() {
      const today = new Date();
      const dd = String(today.getDate()).padStart(2, "0");
      const mm = String(today.getMonth() + 1).padStart(2, "0");
      return `${dd}/${mm}/${today.getFullYear()}`;
    }

    function activeItem() {
      return state.texts.find((item) => item.id === state.selectedId) || null;
    }

    function applyInputsToState() {
      state.source = controls.source.value;
      state.page = Math.max(1, Math.round(numberValue(controls.page, 1)));
      state.sticker.w = numberValue(controls.stickerW, 100);
      state.sticker.h = numberValue(controls.stickerH, 50);
      state.viewScale = numberValue(controls.viewScale, 4.2);
      state.background.scale = numberValue(controls.bgScale, 1);
      state.background.x = numberValue(controls.bgX, 0);
      state.background.y = numberValue(controls.bgY, 0);
    }

    function syncInputsFromState() {
      controls.stickerW.value = state.sticker.w;
      controls.stickerH.value = state.sticker.h;
      controls.viewScale.value = state.viewScale;
      controls.bgScale.value = state.background.scale;
      controls.bgX.value = round1(state.background.x);
      controls.bgY.value = round1(state.background.y);
      controls.page.value = state.page;
      if (state.source) controls.source.value = state.source;
    }

    function syncItemInputs() {
      const item = activeItem();
      const disabled = !item;
      for (const input of [controls.itemText, controls.itemX, controls.itemY, controls.itemSize, controls.itemWeight, controls.itemAlign, controls.itemColor]) {
        input.disabled = disabled;
      }
      document.querySelector("#deleteItem").disabled = disabled;
      if (!item) {
        controls.itemText.value = "";
        controls.itemX.value = "";
        controls.itemY.value = "";
        controls.itemSize.value = "";
        return;
      }
      controls.itemText.value = item.text;
      controls.itemX.value = round1(item.x);
      controls.itemY.value = round1(item.y);
      controls.itemSize.value = item.size;
      controls.itemWeight.value = item.weight;
      controls.itemAlign.value = item.align;
      controls.itemColor.value = item.color || "#111111";
    }

    function round1(value) {
      return Math.round(value * 10) / 10;
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
      if (text && text !== "|") return "tastes";
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
    }

    function setRoleText(role, text) {
      const roles = itemsByRole();
      if (roles[role]) {
        roles[role].text = text;
      }
    }

    function applyContent() {
      if (!controls.contentDate.value.trim()) {
        controls.contentDate.value = todayText();
      }
      setRoleText("title", controls.contentTitle.value);
      setRoleText("tastes", controls.contentTastes.value);
      setRoleText("roast_level", controls.contentRoast.value);
      setRoleText("weight_value", controls.contentWeight.value);
      setRoleText("roast_date", controls.contentDate.value);
      setRoleText("process_label", controls.contentProcess.value ? `עיבוד: ${controls.contentProcess.value}` : "עיבוד:");
      setRoleText("kosher", controls.contentKosher.value);
      render();
    }

    function updateActiveItem() {
      const item = activeItem();
      if (!item) return;
      item.text = controls.itemText.value;
      item.x = numberValue(controls.itemX, item.x);
      item.y = numberValue(controls.itemY, item.y);
      item.size = numberValue(controls.itemSize, item.size);
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

    function editorState() {
      return {
        source: state.source,
        page: state.page,
        sticker: {...state.sticker},
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
      const textFields = state.texts.map((item) => {
        const xPt = item.x * mmToPt;
        const yFromTopPt = item.y * mmToPt;
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
        },
        sticker: {
          width_mm: round1(state.sticker.w),
          height_mm: round1(state.sticker.h),
          width_pt: round2(stickerWidthPt),
          height_pt: round2(stickerHeightPt),
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
          draw_background: "drawImage(background, x_pt, y_pt_from_bottom, width=rendered_width_mm*mm, height=rendered_height_mm*mm) and clip/crop to sticker bounds",
          draw_text: "draw text at x_pt, y_pt_from_bottom using align and font_size_pt",
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
          sticker: {
            w: layout.sticker?.width_mm || 100,
            h: layout.sticker?.height_mm || 100,
          },
          background: {
            x: layout.background_crop?.x_mm || 0,
            y: layout.background_crop?.y_mm_from_top || 0,
            scale: layout.background_crop?.scale || 1,
            pageW: layout.background_crop?.source_width_mm || 100,
            pageH: layout.background_crop?.source_height_mm || 100,
          },
          texts: layout.text_fields.map((field) => ({
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
          })),
        };
      }
      return layout;
    }

    function applyLayout(layout, options = {}) {
      const normalized = normalizeLayout(layout);
      if (!normalized) return false;
      const image = options.keepImage ? state.background.image : "";
      state = {
        ...state,
        source: normalized.source || "",
        page: normalized.page || 1,
        sticker: {
          w: normalized.sticker?.w || normalized.sticker?.width_mm || 100,
          h: normalized.sticker?.h || normalized.sticker?.height_mm || 100,
        },
        background: {
          ...state.background,
          ...(normalized.background || {}),
          image,
          pageW: normalized.background?.pageW || normalized.background?.source_width_mm || state.background.pageW,
          pageH: normalized.background?.pageH || normalized.background?.source_height_mm || state.background.pageH,
        },
        texts: (normalized.texts || []).map((item) => ({
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
        })),
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
    }

    async function saveNamedLayout() {
      const name = controls.layoutName.value.trim();
      if (!name) {
        statusEl.textContent = "צריך לתת שם לפריסה לפני שמירה.";
        return;
      }
      const response = await fetch("/layouts", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          id: controls.savedLayouts.value || "",
          name,
          layout: exportState(),
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        statusEl.textContent = payload.error || "השמירה למאגר נכשלה.";
        return;
      }
      await refreshLayoutList();
      controls.savedLayouts.value = payload.layout.id;
      statusEl.textContent = `נשמר למאגר: ${payload.layout.name}`;
    }

    async function loadNamedLayout() {
      const id = controls.savedLayouts.value;
      if (!id) return;
      const response = await fetch(`/layouts/${encodeURIComponent(id)}`);
      const payload = await response.json();
      if (!response.ok) {
        statusEl.textContent = payload.error || "טעינת הפריסה נכשלה.";
        return;
      }
      controls.layoutName.value = payload.layout.name;
      applyLayout(payload.layout.layout, {keepImage: false});
      await loadSource();
      statusEl.textContent = `נטענה פריסה: ${payload.layout.name}`;
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

    for (const input of [controls.stickerW, controls.stickerH, controls.viewScale, controls.bgScale, controls.bgX, controls.bgY]) {
      input.addEventListener("input", render);
    }

    for (const input of [controls.itemText, controls.itemX, controls.itemY, controls.itemSize, controls.itemWeight, controls.itemAlign, controls.itemColor]) {
      input.addEventListener("input", updateActiveItem);
    }

    for (const input of [controls.contentTitle, controls.contentTastes, controls.contentRoast, controls.contentWeight, controls.contentDate, controls.contentProcess, controls.contentKosher]) {
      input.addEventListener("input", applyContent);
    }

    document.querySelector("#loadSource").addEventListener("click", loadSource);
    document.querySelector("#applyContent").addEventListener("click", applyContent);
    document.querySelector("#saveLayout").addEventListener("click", saveLayout);
    document.querySelector("#loadSaved").addEventListener("click", loadSaved);
    document.querySelector("#saveNamedLayout").addEventListener("click", saveNamedLayout);
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
    document.querySelector("#toggleCompact").addEventListener("click", () => {
      setCompact(!sidePanel.classList.contains("compact"));
    });
    document.querySelector("#deleteItem").addEventListener("click", () => {
      state.texts = state.texts.filter((item) => item.id !== state.selectedId);
      state.selectedId = state.texts[0]?.id || null;
      render();
    });
    document.querySelector("#textMode").addEventListener("click", () => setMode("text"));
    document.querySelector("#bgMode").addEventListener("click", () => setMode("background"));

    for (const button of document.querySelectorAll("[data-add]")) {
      button.addEventListener("click", () => addText(button.dataset.add));
    }

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
    refreshLayoutList();
    loadSource();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True, use_reloader=False)
