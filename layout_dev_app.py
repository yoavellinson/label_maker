"""Label Maker v3 - worker-first label printing on preprinted coffee stickers.

Data model (grid/labels_db.json):
  products     - one entry per coffee: title, tastes, roast, bean, process, kosher,
                 cup score and which of the six preprinted designs it uses.
  layout       - shared text geometry for all designs (designer mode).
  calibration  - sticker size, background offset and print rotation.

The v2 named-preset database (grid/layout_presets.json) is read once to build
the v3 database and is never modified.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import uuid
from base64 import b64encode
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file

import label_core as core


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def app_data_dir() -> Path:
    if "LABEL_MAKER_V2_DATA_DIR" in os.environ:
        return Path(os.environ["LABEL_MAKER_V2_DATA_DIR"]).expanduser()
    if "LABEL_MAKER_DATA_DIR" in os.environ:
        return Path(os.environ["LABEL_MAKER_DATA_DIR"]).expanduser()
    if getattr(sys, "frozen", False) and os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "LabelMaker_v2"
    return app_base_dir()


BASE_DIR = app_base_dir()
DATA_DIR = app_data_dir()
BUNDLED_GRID_DIR = BASE_DIR / "grid"
GRID_DIR = DATA_DIR / "grid"
FONT_DIR = BASE_DIR / "fonts"
WEB_DIR = BASE_DIR / "web"
DB_PATH = GRID_DIR / "labels_db.json"
LEGACY_PRESETS_PATH = GRID_DIR / "layout_presets.json"
BACKUP_DIR = GRID_DIR / "backups"
LOG_DIR = DATA_DIR / "logs"
MAX_BACKUPS = 60
PREVIEW_PX_PER_MM = 10

core.register_fonts(FONT_DIR)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
log = logging.getLogger("label_maker")


def setup_logging() -> None:
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(LOG_DIR / "label_maker.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.info("start base=%s data=%s frozen=%s", BASE_DIR, DATA_DIR, getattr(sys, "frozen", False))


setup_logging()


# --- storage ----------------------------------------------------------------

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
    # labels_db.json is never bundled: it is always built from the machine's own
    # layout_presets.json so upgrades keep each installation's saved coffees.
    for filename in [core.MASTER_PDF_NAME, "layout_presets.json"]:
        copy_if_missing(BUNDLED_GRID_DIR / filename, GRID_DIR / filename)


def master_pdf_path() -> Path:
    for candidate in (GRID_DIR / core.MASTER_PDF_NAME, BUNDLED_GRID_DIR / core.MASTER_PDF_NAME):
        if candidate.exists():
            return candidate
    raise RuntimeError("קובץ העיצובים (PDF) חסר בתיקיית הנתונים.")


def backup_db() -> None:
    if not DB_PATH.exists():
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    shutil.copy2(DB_PATH, BACKUP_DIR / f"labels_db-{stamp}.json")
    backups = sorted(BACKUP_DIR.glob("labels_db-*.json"))
    for old in backups[:-MAX_BACKUPS]:
        old.unlink(missing_ok=True)


def write_db(db: dict) -> None:
    GRID_DIR.mkdir(parents=True, exist_ok=True)
    backup_db()
    tmp = DB_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DB_PATH)


def read_db() -> dict:
    ensure_runtime_data()
    if DB_PATH.exists():
        db = json.loads(DB_PATH.read_text(encoding="utf-8"))
        if core.upgrade_db(db):
            log.info("upgraded database to version %s", db["version"])
            write_db(db)
        return db
    legacy = {}
    if LEGACY_PRESETS_PATH.exists():
        legacy = json.loads(LEGACY_PRESETS_PATH.read_text(encoding="utf-8"))
    db = core.migrate_presets(legacy)
    log.info("created v3 database from %d legacy presets -> %d products", len(legacy.get("layouts", [])), len(db["products"]))
    write_db(db)
    return db


def clean_product(raw: dict) -> dict:
    product = dict(core.EMPTY_PRODUCT)
    for key in core.EMPTY_PRODUCT:
        if key in raw and raw[key] is not None:
            product[key] = raw[key]
    for key in ("title", "tastes", "roast", "process", "kosher", "score", "bean"):
        product[key] = str(product[key]).strip()
    page = int(product.get("page") or 6)
    product["page"] = page if page in core.PAGES else 6
    return product


def request_settings(payload: dict, db: dict) -> tuple[dict, dict]:
    """Designer mode sends unsaved layout/calibration so the preview updates live."""
    layout = core.merged_layout(payload.get("layout") if payload.get("layout") is not None else db.get("layout"))
    calibration = core.merged_calibration(payload.get("calibration") if payload.get("calibration") is not None else db.get("calibration"))
    return layout, calibration


# --- pages and assets -------------------------------------------------------

@app.get("/")
def index():
    ensure_runtime_data()
    return send_file(WEB_DIR / "index.html")


@app.get("/static/<path:name>")
def static_file(name: str):
    path = (WEB_DIR / name).resolve()
    if WEB_DIR.resolve() not in path.parents or not path.is_file():
        return ("", 404)
    return send_file(path)


@app.get("/font/<name>")
def font(name: str):
    allowed = {
        "karantina-light.ttf": FONT_DIR / "Karantina/static/Karantina-Light.ttf",
        "karantina-regular.ttf": FONT_DIR / "Karantina/static/Karantina-Regular.ttf",
        "heebo.ttf": FONT_DIR / "Heebo/Heebo-VariableFont_wght.ttf",
    }
    path = allowed.get(name)
    if not path or not path.exists():
        return ("", 404)
    return send_file(path, max_age=86400)


_background_cache: dict[tuple, bytes] = {}


@app.get("/api/background/<int:page>.png")
def background(page: int):
    if page not in core.PAGES:
        return ("", 404)
    db = read_db()
    calibration = core.merged_calibration(db.get("calibration"))
    for key in ("bg_x", "bg_y", "bg_scale", "sticker_w", "sticker_h"):
        if key in request.args:
            calibration[key] = float(request.args[key])
    cache_key = (page, calibration["bg_x"], calibration["bg_y"], calibration["bg_scale"], calibration["sticker_w"], calibration["sticker_h"])
    if cache_key not in _background_cache:
        try:
            _background_cache[cache_key] = core.background_png(master_pdf_path(), page, calibration, PREVIEW_PX_PER_MM)
        except Exception as exc:  # noqa: BLE001 - reported to the UI and the log
            log.exception("background render failed page=%s", page)
            return jsonify({"error": f"טעינת הרקע נכשלה: {exc}"}), 500
        if len(_background_cache) > 40:
            _background_cache.pop(next(iter(_background_cache)))
    return Response(_background_cache[cache_key], mimetype="image/png", headers={"Cache-Control": "max-age=3600"})


# --- API --------------------------------------------------------------------

@app.get("/api/state")
def state():
    db = read_db()
    return jsonify({
        "pages": core.PAGES,
        "products": sorted(db.get("products", []), key=lambda p: (p.get("title", ""), p.get("roast", ""))),
        "layout": core.merged_layout(db.get("layout")),
        "calibration": core.merged_calibration(db.get("calibration")),
        "default_layout": core.DEFAULT_LAYOUT,
        "default_calibration": core.DEFAULT_CALIBRATION,
        "row_items": core.ROW_ITEM_LABELS,
        "empty_product": core.EMPTY_PRODUCT,
        "data_dir": str(DATA_DIR),
    })


def compose_request() -> tuple[list[dict], dict, dict]:
    payload = request.get_json(force=True) or {}
    db = read_db()
    layout, calibration = request_settings(payload, db)
    product = clean_product(payload.get("product") or {})
    daily = payload.get("daily") or {}
    return core.compose(product, daily, layout), layout, calibration


@app.post("/api/preview")
def preview():
    fields, layout, calibration = compose_request()
    try:
        png = core.pdf_to_png(core.label_pdf(fields, calibration), PREVIEW_PX_PER_MM)
    except Exception as exc:  # noqa: BLE001
        log.exception("preview failed")
        return jsonify({"error": f"התצוגה נכשלה: {exc}"}), 500
    return jsonify({
        "image": "data:image/png;base64," + b64encode(png).decode("ascii"),
        "shrunk": core.shrunk_groups(fields),
        "overflow": core.overflowing(fields, layout),
    })


@app.post("/api/print.pdf")
def print_pdf():
    payload = request.get_json(force=True) or {}
    fields, _, calibration = compose_request()
    background = None
    if payload.get("with_background"):
        page = clean_product(payload.get("product") or {})["page"]
        background = core.background_png(master_pdf_path(), page, calibration, 24)
    copies = int(payload.get("copies") or 1)
    pdf = core.label_pdf(fields, calibration, copies=copies, background_png=background)
    log.info("print copies=%s background=%s title=%s", copies, bool(background), (payload.get("product") or {}).get("title", ""))
    return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": "inline; filename=label.pdf"})


@app.post("/api/products")
def save_product():
    payload = request.get_json(force=True) or {}
    product = clean_product(payload.get("product") or {})
    if not product["title"]:
        return jsonify({"error": "צריך שם קפה לפני שמירה."}), 400
    db = read_db()
    products = db.setdefault("products", [])
    product_id = str((payload.get("product") or {}).get("id") or "").strip()
    product["id"] = product_id or uuid.uuid4().hex
    product["updated_at"] = datetime.now().isoformat(timespec="seconds")
    for index, existing in enumerate(products):
        if existing.get("id") == product["id"]:
            products[index] = product
            break
    else:
        products.append(product)
    write_db(db)
    log.info("saved product %s %s", product["id"], product["title"])
    return jsonify({"product": product})


@app.delete("/api/products/<product_id>")
def delete_product(product_id: str):
    db = read_db()
    products = db.setdefault("products", [])
    remaining = [p for p in products if p.get("id") != product_id]
    if len(remaining) == len(products):
        return jsonify({"error": "הקפה לא נמצא."}), 404
    db["products"] = remaining
    write_db(db)
    log.info("deleted product %s", product_id)
    return jsonify({"ok": True})


@app.post("/api/settings")
def save_settings():
    payload = request.get_json(force=True) or {}
    db = read_db()
    if "layout" in payload:
        db["layout"] = core.merged_layout(payload["layout"])
    if "calibration" in payload:
        db["calibration"] = core.merged_calibration(payload["calibration"])
    write_db(db)
    _background_cache.clear()
    log.info("settings saved")
    return jsonify({"layout": db["layout"], "calibration": db["calibration"]})


@app.post("/api/open-data-folder")
def open_data_folder():
    try:
        if os.name == "nt":
            os.startfile(DATA_DIR)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess

            subprocess.Popen(["open", str(DATA_DIR)])
        else:
            import subprocess

            subprocess.Popen(["xdg-open", str(DATA_DIR)])
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@app.errorhandler(Exception)
def unhandled(exc):
    from werkzeug.exceptions import HTTPException

    if isinstance(exc, HTTPException):
        return exc
    log.exception("unhandled error")
    return jsonify({"error": f"שגיאה: {exc}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True, use_reloader=False)
