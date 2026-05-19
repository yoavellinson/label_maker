from base64 import b64decode, b64encode
import csv
from datetime import date, datetime
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets

from flask import Flask, Response, jsonify, render_template_string, request, send_file
from markupsafe import Markup, escape
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.exceptions import RequestEntityTooLarge

from pdf_chat import LabelData, create_label_pdf


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


FIELD_LABELS = {
    "badge_line_1": "שורה ראשונה בתג",
    "badge_line_2": "שורה שנייה בתג",
    "roast_level": "דרגת קלייה",
    "note_1": "טעם 1",
    "note_2": "טעם 2",
    "note_3": "טעם 3",
    "description": "תיאור",
}

MAX_TEXTURE_SIZE = (900, 900)
STATE_PATH = Path(".label_state.json")
BLENDS_PATH = Path("blends.csv")
TEXTURES_DIR = Path("textures")
ADMIN_PASSWORD = os.environ.get("LABEL_ADMIN_PASSWORD", "coffee")
BLEND_FIELDNAMES = [
    "blend_id",
    "name",
    "badge_line_1",
    "badge_line_2",
    "roast_level",
    "note_1",
    "note_2",
    "note_3",
    "highlighted_note",
    "description",
    "bottom_mode",
    "roast_date",
    "accent_color",
    "texture_path",
]


TEMPLATE = """
<!doctype html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>עורך תווית קפה</title>
  <style>
    @font-face {
      font-family: "Heebo";
      src: url("font/heebo-regular.ttf") format("truetype");
      font-weight: 400;
    }

    @font-face {
      font-family: "Heebo";
      src: url("font/heebo-bold.ttf") format("truetype");
      font-weight: 700;
    }

    :root {
      color-scheme: light;
      --bg: #f7f3ed;
      --panel: #fffaf3;
      --ink: #251814;
      --muted: #745f55;
      --accent: #7a2f1d;
      --line: #daccc1;
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

    .app {
      display: grid;
      grid-template-columns: minmax(360px, 1fr) minmax(360px, 440px);
      gap: 24px;
      min-height: 100vh;
      padding: 24px;
      direction: ltr;
    }

    .preview,
    .editor {
      min-width: 0;
    }

    .preview {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      direction: rtl;
    }

    h1 {
      margin: 0;
      font-size: 22px;
      line-height: 1.25;
    }

    .download {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 auto;
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: white;
      padding: 10px 14px;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
      font: inherit;
    }

    iframe {
      width: 100%;
      height: calc(100vh - 92px);
      min-height: 640px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: white;
    }

    .editor {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      overflow: auto;
      direction: rtl;
    }

    form {
      display: grid;
      gap: 14px;
    }

    fieldset {
      display: grid;
      gap: 10px;
      margin: 0;
      padding: 0 0 14px;
      border: 0;
      border-bottom: 1px solid var(--line);
    }

    fieldset:last-of-type {
      border-bottom: 0;
      padding-bottom: 0;
    }

    legend {
      margin-bottom: 2px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }

    label {
      display: grid;
      gap: 5px;
      font-size: 13px;
      font-weight: 700;
    }

    input,
    textarea,
    select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      color: var(--ink);
      padding: 10px 11px;
      font: inherit;
      direction: rtl;
      text-align: right;
    }

    textarea {
      min-height: 120px;
      resize: vertical;
      line-height: 1.45;
    }

    input[type="color"] {
      height: 44px;
      padding: 4px;
      direction: ltr;
    }

    input[type="file"] {
      direction: rtl;
      line-height: 1.5;
    }

    .choice-group {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }

    .choice {
      display: flex;
      align-items: center;
      justify-content: flex-start;
      gap: 8px;
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      padding: 8px 10px;
      font-weight: 700;
    }

    .choice input {
      width: auto;
      margin: 0;
    }

    input:focus,
    textarea:focus,
    select:focus {
      border-color: var(--accent);
      outline: 2px solid rgba(122, 47, 29, 0.16);
    }

    .actions {
      position: sticky;
      bottom: -18px;
      display: flex;
      gap: 10px;
      padding: 14px 0 0;
      background: linear-gradient(rgba(255, 250, 243, 0), var(--panel) 18px);
    }

    button {
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: white;
      padding: 11px 16px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }

    .secondary {
      border: 1px solid var(--line);
      background: white;
      color: var(--ink);
    }

    .message {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      padding: 10px 12px;
      font-size: 13px;
      font-weight: 700;
    }

    .message.error {
      border-color: #b34236;
      color: #8b241b;
    }

    .message.ok {
      border-color: #3d7a4d;
      color: #246139;
    }

    @media (max-width: 900px) {
      .app {
        grid-template-columns: 1fr;
        padding: 14px;
        direction: rtl;
      }

      iframe {
        height: 620px;
        min-height: 520px;
      }
    }
  </style>
</head>
<body>
  <main class="app">
    <section class="preview" aria-label="תצוגה מקדימה">
      <div class="toolbar">
        <h1>עורך תווית קפה</h1>
        <button class="download" type="submit" form="label-form" formaction="label.pdf">הורדת PDF</button>
      </div>
      <iframe id="pdf-preview" title="תצוגה מקדימה של התווית" src="data:application/pdf;base64,{{ pdf_base64 }}"></iframe>
    </section>

    <aside class="editor" aria-label="פרטי התווית">
      <form id="label-form" method="post" enctype="multipart/form-data" autocomplete="off">
        <input type="hidden" name="texture_data" value="{{ texture_base64 }}">
        {% if message %}
          <div class="message {{ message_type }}">{{ message }}</div>
        {% endif %}

        <fieldset>
          <legend>בלנדים</legend>
          <label>
            טעינה מ-CSV
            <select name="selected_blend_id">
              <option value="">בחירת בלנד</option>
              {% for blend in blends %}
                <option value="{{ blend.id }}" {% if selected_blend == blend.id %}selected{% endif %}>{{ blend.name }}</option>
              {% endfor %}
            </select>
          </label>
          <button type="submit" name="action" value="load_blend">טעינת בלנד</button>
          <label>
            מזהה לשמירה
            <input name="save_blend_id" value="{{ selected_blend }}" dir="ltr">
          </label>
          <label>
            שם בלנד לשמירה
            <input name="save_blend_name" value="{{ selected_blend_name }}" dir="rtl">
          </label>
          <label>
            סיסמת עריכה
            <input type="password" name="admin_password" value="" dir="ltr">
          </label>
          <button type="submit" name="action" value="save_blend">שמירת בלנד ל-CSV</button>
        </fieldset>

        <fieldset>
          <legend>תג מרכזי</legend>
          {{ input("badge_line_1") }}
          {{ input("badge_line_2") }}
          {{ input("roast_level") }}
        </fieldset>

        <fieldset>
          <legend>טעמים</legend>
          {{ input("note_1") }}
          {{ input("note_2") }}
          {{ input("note_3") }}
          <label>
            טעם מודגש
            <select name="highlighted_note">
              {% for value in [1, 2, 3] %}
                <option value="{{ value }}" {% if label.highlighted_note == value %}selected{% endif %}>{{ value }}</option>
              {% endfor %}
            </select>
          </label>
        </fieldset>

        <fieldset>
          <legend>תיאור</legend>
          <label>
            תיאור
            <textarea name="description" dir="rtl">{{ label.description }}</textarea>
          </label>
        </fieldset>

        <fieldset>
          <legend>תחתית</legend>
          <div class="choice-group">
            <label class="choice">
              <input type="radio" name="bottom_mode" value="roast_date" {% if label.bottom_mode == "roast_date" %}checked{% endif %}>
              תאריך קלייה
            </label>
            <label class="choice">
              <input type="radio" name="bottom_mode" value="surprise" {% if label.bottom_mode == "surprise" %}checked{% endif %}>
              הפתעה!
            </label>
          </div>
          <label>
            תאריך קלייה
            <input name="roast_date" value="{{ label.roast_date }}" inputmode="numeric" pattern="\\d{2}/\\d{2}/\\d{4}" dir="ltr">
          </label>
        </fieldset>

        <fieldset>
          <legend>עיצוב</legend>
          <label>
            צבע מוביל
            <input type="color" name="accent_color" value="{{ label.accent_color }}">
          </label>
          <label>
            טקסטורה לחלק העליון
            <input type="file" name="texture_file" accept="image/png,image/jpeg,image/webp,image/gif">
          </label>
        </fieldset>

        <div class="actions">
          <button type="submit" name="action" value="update">עדכון תצוגה</button>
          <a class="download secondary" href="./?reset=1">איפוס</a>
        </div>
      </form>
    </aside>
  </main>
  <script>
    const form = document.getElementById("label-form");
    const preview = document.getElementById("pdf-preview");
    const autoFields = form.querySelectorAll(
      'input[name="badge_line_1"], input[name="badge_line_2"], input[name="roast_level"], input[name="note_1"], input[name="note_2"], input[name="note_3"], select[name="highlighted_note"], textarea[name="description"], input[name="bottom_mode"], input[name="roast_date"], input[name="accent_color"], input[name="texture_file"]'
    );

    let previewTimer;
    let previewController;

    async function refreshPreview() {
      if (previewController) {
        previewController.abort();
      }

      previewController = new AbortController();
      const data = new FormData(form);
      data.set("action", "auto_preview");

      try {
        const response = await fetch("preview", {
          method: "POST",
          body: data,
          signal: previewController.signal
        });

        if (!response.ok) {
          return;
        }

        const payload = await response.json();
        preview.src = `data:application/pdf;base64,${payload.pdf_base64}`;
        form.querySelector('input[name="texture_data"]').value = payload.texture_base64;
      } catch (error) {
        if (error.name !== "AbortError") {
          console.error(error);
        }
      }
    }

    function schedulePreview() {
      clearTimeout(previewTimer);
      previewTimer = setTimeout(refreshPreview, 450);
    }

    autoFields.forEach((field) => {
      const eventName = field.type === "file" || field.tagName === "SELECT" || field.type === "radio" || field.type === "color"
        ? "change"
        : "input";
      field.addEventListener(eventName, schedulePreview);
    });
  </script>
</body>
</html>
"""


def default_label_data() -> LabelData:
    return LabelData(roast_date=today_string())


def build_label_data(args, defaults: LabelData | None = None) -> LabelData:
    defaults = defaults or default_label_data()
    values = {}

    for field in FIELD_LABELS:
        if field in args:
            values[field] = args.get(field, "").strip()
        else:
            values[field] = getattr(defaults, field)

    try:
        highlighted_note = int(args.get("highlighted_note", defaults.highlighted_note))
    except ValueError:
        highlighted_note = defaults.highlighted_note

    if highlighted_note not in {1, 2, 3}:
        highlighted_note = defaults.highlighted_note

    bottom_mode = args.get("bottom_mode", defaults.bottom_mode)
    if bottom_mode not in {"roast_date", "surprise"}:
        bottom_mode = defaults.bottom_mode

    roast_date = normalize_roast_date(args.get("roast_date", defaults.roast_date))

    accent_color = args.get("accent_color", defaults.accent_color)
    if not (len(accent_color) == 7 and accent_color.startswith("#")):
        accent_color = defaults.accent_color

    return LabelData(
        **values,
        highlighted_note=highlighted_note,
        bottom_mode=bottom_mode,
        roast_date=roast_date,
        accent_color=accent_color,
        texture_bytes=get_texture_bytes(defaults.texture_bytes),
    )


def today_string() -> str:
    return date.today().strftime("%d/%m/%Y")


def normalize_roast_date(value: str) -> str:
    value = value.strip()

    if not value:
        return ""

    try:
        parsed = datetime.strptime(value, "%d/%m/%Y")
    except ValueError:
        return today_string()

    return parsed.strftime("%d/%m/%Y")


def load_blends() -> list[dict[str, str]]:
    if not BLENDS_PATH.exists():
        return []

    try:
        with BLENDS_PATH.open(newline="", encoding="utf-8-sig") as csv_file:
            rows = list(csv.DictReader(csv_file))
    except OSError:
        return []

    blends = []
    for index, row in enumerate(rows, start=1):
        blend_id = (row.get("blend_id") or str(index)).strip()
        name = (row.get("name") or row.get("badge_line_2") or blend_id).strip()
        row["_id"] = blend_id
        row["_name"] = name
        blends.append(row)

    return blends


def ensure_blends_file() -> None:
    if BLENDS_PATH.exists():
        return

    with BLENDS_PATH.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=BLEND_FIELDNAMES)
        writer.writeheader()


def blend_choices(blends: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"id": blend["_id"], "name": blend["_name"]} for blend in blends]


def blend_name(blend_id: str) -> str:
    for blend in load_blends():
        if blend["_id"] == blend_id:
            return blend["_name"]

    return ""


def normalize_blend_id(value: str, fallback: str) -> str:
    value = value.strip() or fallback
    value = re.sub(r"\s+", "-", value)
    value = value.strip("-._")
    return value or "blend"


def texture_file_stem(blend_id: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z._-]+", "-", blend_id).strip("-._")
    return stem or "blend"


def save_texture_file(blend_id: str, texture_bytes: bytes | None) -> str:
    if not texture_bytes:
        return ""

    TEXTURES_DIR.mkdir(exist_ok=True)
    texture_path = TEXTURES_DIR / f"{texture_file_stem(blend_id)}.jpg"
    texture_path.write_bytes(texture_bytes)
    return str(texture_path)


def save_blend_to_csv(blend_id: str, name: str, label_data: LabelData) -> str:
    blend_id = normalize_blend_id(blend_id, label_data.badge_line_1)
    name = name.strip() or label_data.badge_line_1 or blend_id
    ensure_blends_file()

    rows = []
    with BLENDS_PATH.open(newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            rows.append({field: row.get(field, "") for field in BLEND_FIELDNAMES})

    texture_path = save_texture_file(blend_id, label_data.texture_bytes)
    next_row = {
        "blend_id": blend_id,
        "name": name,
        "badge_line_1": label_data.badge_line_1,
        "badge_line_2": label_data.badge_line_2,
        "roast_level": label_data.roast_level,
        "note_1": label_data.note_1,
        "note_2": label_data.note_2,
        "note_3": label_data.note_3,
        "highlighted_note": str(label_data.highlighted_note),
        "description": label_data.description,
        "bottom_mode": label_data.bottom_mode,
        "roast_date": label_data.roast_date,
        "accent_color": label_data.accent_color,
        "texture_path": texture_path,
    }

    replaced = False
    for index, row in enumerate(rows):
        if row.get("blend_id") == blend_id:
            if not texture_path:
                next_row["texture_path"] = row.get("texture_path", "")
            rows[index] = next_row
            replaced = True
            break

    if not replaced:
        rows.append(next_row)

    with BLENDS_PATH.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=BLEND_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    return blend_id


def is_admin_password(value: str) -> bool:
    return secrets.compare_digest(value, ADMIN_PASSWORD)


def label_from_blend(blend_id: str, fallback: LabelData | None = None) -> LabelData:
    fallback = fallback or default_label_data()

    for blend in load_blends():
        if blend["_id"] != blend_id:
            continue

        values = {}
        for field in FIELD_LABELS:
            if field in blend and blend.get(field) is not None:
                values[field] = blend.get(field, "").strip()
            else:
                values[field] = getattr(fallback, field)

        try:
            highlighted_note = int(blend.get("highlighted_note") or fallback.highlighted_note)
        except (TypeError, ValueError):
            highlighted_note = fallback.highlighted_note

        if highlighted_note not in {1, 2, 3}:
            highlighted_note = fallback.highlighted_note

        bottom_mode = (blend.get("bottom_mode") or fallback.bottom_mode).strip()
        if bottom_mode not in {"roast_date", "surprise"}:
            bottom_mode = fallback.bottom_mode

        accent_color = (blend.get("accent_color") or fallback.accent_color).strip()
        if not (len(accent_color) == 7 and accent_color.startswith("#")):
            accent_color = fallback.accent_color

        return LabelData(
            **values,
            highlighted_note=highlighted_note,
            bottom_mode=bottom_mode,
            roast_date=normalize_roast_date(blend.get("roast_date", fallback.roast_date)),
            accent_color=accent_color,
            texture_bytes=texture_from_path(blend.get("texture_path", "")) or fallback.texture_bytes,
        )

    return fallback


def texture_from_path(texture_path: str) -> bytes | None:
    texture_path = texture_path.strip()
    if not texture_path:
        return None

    path = Path(texture_path)
    if not path.is_absolute():
        path = Path.cwd() / path

    try:
        return prepare_texture(path.read_bytes())
    except OSError:
        return None


def load_label_state() -> LabelData | None:
    if not STATE_PATH.exists():
        return None

    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    defaults = default_label_data()
    values = {}

    for field in FIELD_LABELS:
        if field in state:
            values[field] = str(state.get(field, "")).strip()
        else:
            values[field] = getattr(defaults, field)

    try:
        highlighted_note = int(state.get("highlighted_note", defaults.highlighted_note))
    except (TypeError, ValueError):
        highlighted_note = defaults.highlighted_note

    if highlighted_note not in {1, 2, 3}:
        highlighted_note = defaults.highlighted_note

    bottom_mode = state.get("bottom_mode", defaults.bottom_mode)
    if bottom_mode not in {"roast_date", "surprise"}:
        bottom_mode = defaults.bottom_mode

    accent_color = state.get("accent_color", defaults.accent_color)
    if not (isinstance(accent_color, str) and len(accent_color) == 7 and accent_color.startswith("#")):
        accent_color = defaults.accent_color

    texture_bytes = None
    texture_data = state.get("texture_data", "")
    if texture_data:
        try:
            texture_bytes = b64decode(texture_data)
        except ValueError:
            texture_bytes = None

    return LabelData(
        **values,
        highlighted_note=highlighted_note,
        bottom_mode=bottom_mode,
        roast_date=normalize_roast_date(str(state.get("roast_date", defaults.roast_date))),
        accent_color=accent_color,
        texture_bytes=texture_bytes,
    )


def save_label_state(label_data: LabelData) -> None:
    state = {
        field: getattr(label_data, field)
        for field in FIELD_LABELS
    }
    state.update(
        {
            "highlighted_note": label_data.highlighted_note,
            "bottom_mode": label_data.bottom_mode,
            "roast_date": label_data.roast_date,
            "accent_color": label_data.accent_color,
            "texture_data": b64encode(label_data.texture_bytes or b"").decode("ascii"),
        }
    )

    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_label_state() -> None:
    try:
        STATE_PATH.unlink()
    except FileNotFoundError:
        pass


def get_texture_bytes(default: bytes | None = None) -> bytes | None:
    upload = request.files.get("texture_file")
    if upload and upload.filename:
        return prepare_texture(upload.read())

    texture_data = request.form.get("texture_data", "")
    if texture_data:
        try:
            return b64decode(texture_data)
        except ValueError:
            return default

    return default


def prepare_texture(texture_bytes: bytes) -> bytes | None:
    if not texture_bytes:
        return None

    try:
        with Image.open(BytesIO(texture_bytes)) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail(MAX_TEXTURE_SIZE, Image.Resampling.LANCZOS)

            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")

            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=82, optimize=True)
            return buffer.getvalue()
    except (OSError, UnidentifiedImageError):
        return None


def render_pdf(data: LabelData) -> bytes:
    buffer = BytesIO()
    create_label_pdf(buffer, data)
    return buffer.getvalue()


@app.errorhandler(RequestEntityTooLarge)
def request_too_large(error):
    return (
        """
        <!doctype html>
        <html lang="he" dir="rtl">
        <meta charset="utf-8">
        <title>הקובץ גדול מדי</title>
        <body style="font-family: Arial, sans-serif; padding: 32px; background: #f7f3ed; color: #251814;">
          <h1>הקובץ גדול מדי</h1>
          <p>אפשר להעלות תמונה עד 16MB. נסה תמונה קטנה יותר או דחוסה יותר.</p>
          <p><a href="./">חזרה לעורך</a></p>
        </body>
        </html>
        """,
        413,
    )


@app.template_global()
def input(field):
    return Markup(
        f"""
        <label>
          {escape(FIELD_LABELS[field])}
          <input name="{escape(field)}" value="{escape(getattr(request.label_data, field))}" dir="rtl">
        </label>
        """
    )


@app.route("/", methods=["GET", "POST"])
def index():
    selected_blend = ""
    selected_blend_name = ""
    message = ""
    message_type = ""

    if request.args.get("reset") == "1":
        clear_label_state()
        label_data = default_label_data()
    elif request.method == "POST" and request.form.get("action") == "load_blend":
        selected_blend = request.form.get("selected_blend_id", "")
        selected_blend_name = blend_name(selected_blend)
        label_data = label_from_blend(selected_blend, load_label_state() or default_label_data())
        save_label_state(label_data)
    elif request.method == "POST" and request.form.get("action") == "save_blend":
        label_data = build_label_data(request.form, load_label_state())
        selected_blend = request.form.get("save_blend_id", "")
        selected_blend_name = request.form.get("save_blend_name", "")
        if is_admin_password(request.form.get("admin_password", "")):
            selected_blend = save_blend_to_csv(selected_blend, selected_blend_name, label_data)
            selected_blend_name = blend_name(selected_blend)
            message = "הבלנד נשמר ל-CSV."
            message_type = "ok"
        else:
            message = "סיסמת העריכה שגויה. הבלנד לא נשמר."
            message_type = "error"
        save_label_state(label_data)
    elif request.method == "POST":
        label_data = build_label_data(request.form, load_label_state())
        selected_blend = request.form.get("selected_blend_id", "")
        selected_blend_name = blend_name(selected_blend)
        save_label_state(label_data)
    else:
        label_data = load_label_state() or default_label_data()

    request.label_data = label_data
    pdf = render_pdf(label_data)
    return render_template_string(
        TEMPLATE,
        label=label_data,
        pdf_base64=b64encode(pdf).decode("ascii"),
        texture_base64=b64encode(label_data.texture_bytes or b"").decode("ascii"),
        blends=blend_choices(load_blends()),
        selected_blend=selected_blend,
        selected_blend_name=selected_blend_name,
        message=message,
        message_type=message_type,
    )


@app.get("/font/<name>")
def font(name):
    fonts = {
        "heebo-regular.ttf": Path("fonts/Heebo/static/Heebo-Regular.ttf"),
        "heebo-bold.ttf": Path("fonts/Heebo/static/Heebo-Bold.ttf"),
    }

    if name not in fonts:
        return Response(status=404)

    return send_file(fonts[name], mimetype="font/ttf")


@app.post("/preview")
def preview_pdf():
    label_data = build_label_data(request.form, load_label_state())
    save_label_state(label_data)
    pdf = render_pdf(label_data)
    return jsonify(
        {
            "pdf_base64": b64encode(pdf).decode("ascii"),
            "texture_base64": b64encode(label_data.texture_bytes or b"").decode("ascii"),
        }
    )


@app.route("/label.pdf", methods=["GET", "POST"])
def label_pdf():
    if request.method == "POST":
        label_data = build_label_data(request.form, load_label_state())
        save_label_state(label_data)
    else:
        label_data = load_label_state() or default_label_data()

    pdf = render_pdf(label_data)
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": "inline; filename=coffee_label.pdf"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True, use_reloader=False)
