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
import label_full as full
import label_sheet as sheet
import printing
import updater


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
APP_VERSION = updater.read_version(BASE_DIR)

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
    log.info("start version=%s base=%s data=%s frozen=%s", updater.read_version(BASE_DIR), BASE_DIR, DATA_DIR, getattr(sys, "frozen", False))


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
    for key in ("title", "tastes", "roast", "process", "kosher", "score", "bean", "description"):
        product[key] = str(product[key]).strip()
    page = int(product.get("page") or 6)
    product["page"] = page if page in core.PAGES else 6
    return product


def pick(payload: dict, db: dict, key: str):
    """Designer mode sends unsaved settings so the preview updates live."""
    return payload.get(key) if payload.get(key) is not None else db.get(key)


def request_settings(payload: dict, db: dict) -> tuple[dict, dict]:
    layout = core.merged_layout(pick(payload, db, "layout"))
    calibration = core.merged_calibration(pick(payload, db, "calibration"))
    return layout, calibration


def full_settings(payload: dict, db: dict) -> tuple[dict, dict]:
    return full.merged_layout(pick(payload, db, "full_layout")), full.merged_calibration(pick(payload, db, "full_calibration"))


def sheet_label(payload: dict, outlines: bool = False) -> tuple[bytes, list[str], int]:
    """A4 sheet of 105x46 surprise labels."""
    db = read_db()
    layout = sheet.merged_layout(pick(payload, db, "sheet_layout"))
    product = clean_product(payload.get("product") or {})
    art = full.design_art(master_pdf_path(), product["page"], core.merged_calibration(db.get("calibration")))
    # In A4 mode the counter is in sheets: every sheet is filled completely, the
    # first one from the chosen start slot.
    per_page = sheet.labels_per_page(layout)
    sheets = max(1, min(int(payload.get("copies") or 1), 50))
    start = max(1, min(int(payload.get("start") or 1), per_page))
    count = sheets * per_page - (start - 1)
    full_layout = full.merged_layout(pick(payload, db, "full_layout"))
    narrow_width = full.merged_calibration(pick(payload, db, "full_calibration"))["sticker_w"]
    return sheet.sheet_pdf(product, layout, art, full_layout, count=count, start=int(payload.get("start") or 1),
                           outlines=outlines, narrow_width=narrow_width)


def full_label(payload: dict, copies: int = 1, rotate: bool = True) -> tuple[bytes, list[str]]:
    """Render the full-print 50x100 label for a request payload."""
    db = read_db()
    layout, calibration = full_settings(payload, db)
    product = clean_product(payload.get("product") or {})
    sticker_calibration = core.merged_calibration(db.get("calibration"))
    art = full.design_art(master_pdf_path(), product["page"], sticker_calibration)
    return full.label_pdf(product, daily_for(payload), layout, calibration, art, copies=copies, rotate=rotate)


LABEL_FORMATS = {"preprinted", "full", "surprise", "sheet"}
SHEET_PREVIEW_PX_PER_MM = 3.2


def label_format(payload: dict) -> str:
    value = payload.get("format")
    return value if value in LABEL_FORMATS else "preprinted"


def is_full(payload: dict) -> bool:
    """Narrow 50x100 labels: the regular one and the surprise one."""
    return label_format(payload) in {"full", "surprise"}


def daily_for(payload: dict) -> dict:
    """The surprise label prints "הפתעה!" with no date and no weight."""
    daily = dict(payload.get("daily") or {})
    daily["date_mode"] = "surprise" if label_format(payload) in {"surprise", "sheet"} else "date"
    return daily


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
        "full_layout": full.merged_layout(db.get("full_layout")),
        "full_calibration": full.merged_calibration(db.get("full_calibration")),
        "default_full_layout": full.DEFAULT_FULL_LAYOUT,
        "default_full_calibration": full.DEFAULT_FULL_CALIBRATION,
        "sheet_layout": sheet.merged_layout(db.get("sheet_layout")),
        "default_sheet_layout": sheet.DEFAULT_SHEET_LAYOUT,
        "row_items": core.ROW_ITEM_LABELS,
        "empty_product": core.EMPTY_PRODUCT,
        "data_dir": str(DATA_DIR),
        "version": APP_VERSION,
        "printers": configured_printers(db),
        "direct_print": printing.available(),
    })


@app.get("/api/version")
def version():
    return jsonify({"version": APP_VERSION})


def update_password_ok(payload: dict) -> bool:
    ok = updater.check_password(payload.get("password", ""))
    if not ok:
        log.warning("update: wrong password")
    return ok


@app.post("/api/update/check")
def update_check():
    payload = request.get_json(force=True) or {}
    if not update_password_ok(payload):
        return jsonify({"error": "סיסמה שגויה."}), 403
    try:
        result = updater.check(APP_VERSION)
    except updater.UpdateError as exc:
        return jsonify({"error": str(exc)}), 502
    result["can_install"] = updater.can_install()
    log.info("update check: current=%s latest=%s", APP_VERSION, result["latest"])
    return jsonify(result)


@app.post("/api/update/install")
def update_install():
    payload = request.get_json(force=True) or {}
    if not update_password_ok(payload):
        return jsonify({"error": "סיסמה שגויה."}), 403
    if not updater.can_install():
        return jsonify({"error": "התקנת עדכון אפשרית רק בגרסת Windows המותקנת."}), 400
    try:
        release = updater.latest_release()
        if updater.parse_version(release["version"]) <= updater.parse_version(APP_VERSION):
            return jsonify({"error": "כבר מותקנת הגרסה האחרונה."}), 400
        installer = updater.download_installer(release["url"], release["size"])
        log.info("update: installing %s from %s", release["version"], installer)
        updater.start_installer(installer)
    except (updater.UpdateError, OSError) as exc:
        log.exception("update failed")
        return jsonify({"error": str(exc)}), 502
    # Leave so the installer can replace our files; it reopens the app when done.
    import threading

    threading.Timer(1.5, lambda: os._exit(0)).start()
    return jsonify({"ok": True, "version": release["version"]})


def compose_request() -> tuple[list[dict], dict, dict]:
    payload = request.get_json(force=True) or {}
    db = read_db()
    layout, calibration = request_settings(payload, db)
    product = clean_product(payload.get("product") or {})
    return core.compose(product, daily_for(payload), layout), layout, calibration


@app.post("/api/preview")
def preview():
    payload = request.get_json(force=True) or {}
    if label_format(payload) == "sheet":
        try:
            pdf, warnings, pages = sheet_label(payload, outlines=True)
            png = core.pdf_to_png(pdf, SHEET_PREVIEW_PX_PER_MM, transparent=False)
        except Exception as exc:  # noqa: BLE001
            log.exception("sheet preview failed")
            return jsonify({"error": f"התצוגה נכשלה: {exc}"}), 500
        return jsonify({"image": "data:image/png;base64," + b64encode(png).decode("ascii"), "shrunk": warnings, "overflow": [], "pages": pages})
    if is_full(payload):
        try:
            pdf, warnings = full_label(payload, rotate=False)
            png = core.pdf_to_png(pdf, PREVIEW_PX_PER_MM, transparent=False)
        except Exception as exc:  # noqa: BLE001
            log.exception("full preview failed")
            return jsonify({"error": f"התצוגה נכשלה: {exc}"}), 500
        return jsonify({"image": "data:image/png;base64," + b64encode(png).decode("ascii"), "shrunk": warnings, "overflow": []})
    fields, layout, calibration = compose_request()
    try:
        png = core.pdf_to_png(core.label_pdf(fields, calibration, rotate=False), PREVIEW_PX_PER_MM)
    except Exception as exc:  # noqa: BLE001
        log.exception("preview failed")
        return jsonify({"error": f"התצוגה נכשלה: {exc}"}), 500
    return jsonify({
        "image": "data:image/png;base64," + b64encode(png).decode("ascii"),
        "shrunk": core.shrunk_groups(fields),
        "overflow": core.overflowing(fields, layout),
    })


def build_print_pdf(payload: dict) -> tuple[bytes, str]:
    """The PDF that gets printed for a request, and a file name for it."""
    copies = int(payload.get("copies") or 1)
    title = (payload.get("product") or {}).get("title", "")
    if label_format(payload) == "sheet":
        pdf, _, pages = sheet_label(payload, outlines=bool(payload.get("outlines")))
        log.info("print sheet sheets=%s start=%s pages=%s title=%s", copies, payload.get("start"), pages, title)
        return pdf, "surprise-a4.pdf"
    if is_full(payload):
        pdf, _ = full_label(payload, copies=copies)
        log.info("print %s copies=%s title=%s", label_format(payload), copies, title)
        return pdf, "label.pdf"
    fields, _, calibration = compose_request()
    background = None
    if payload.get("with_background"):
        page = clean_product(payload.get("product") or {})["page"]
        background = core.background_png(master_pdf_path(), page, calibration, 24)
    pdf = core.label_pdf(fields, calibration, copies=copies, background_png=background)
    log.info("print copies=%s background=%s title=%s", copies, bool(background), title)
    return pdf, "label.pdf"


@app.post("/api/print.pdf")
def print_pdf():
    pdf, name = build_print_pdf(request.get_json(force=True) or {})
    return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f"inline; filename={name}"})


def configured_printers(db: dict) -> dict:
    stored = db.get("printers") or {}
    return {fmt: str(stored.get(fmt) or "") for fmt in sorted(LABEL_FORMATS)}


@app.get("/api/printers")
def printers():
    info = printing.list_printers()
    info["assigned"] = configured_printers(read_db())
    return jsonify(info)


@app.post("/api/print-direct")
def print_direct():
    """Print on the printer assigned to this label type, without a dialog."""
    payload = request.get_json(force=True) or {}
    printer = configured_printers(read_db()).get(label_format(payload), "")
    if not printer:
        return jsonify({"error": "לא הוגדרה מדפסת לסוג המדבקה הזה."}), 400
    pdf, name = build_print_pdf(payload)
    try:
        pages = printing.print_pdf(pdf, printer, job_name=f"Label Maker - {name}")
    except printing.PrintError as exc:
        log.warning("direct print failed on %s: %s", printer, exc)
        return jsonify({"error": str(exc)}), 502
    log.info("direct print %s pages on %s", pages, printer)
    return jsonify({"ok": True, "printer": printer, "pages": pages})


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
    if "full_layout" in payload:
        db["full_layout"] = full.merged_layout(payload["full_layout"])
    if "full_calibration" in payload:
        db["full_calibration"] = full.merged_calibration(payload["full_calibration"])
    if "sheet_layout" in payload:
        db["sheet_layout"] = sheet.merged_layout(payload["sheet_layout"])
    if "printers" in payload:
        db["printers"] = {fmt: str((payload["printers"] or {}).get(fmt) or "") for fmt in sorted(LABEL_FORMATS)}
    write_db(db)
    _background_cache.clear()
    log.info("settings saved")
    return jsonify({
        "layout": core.merged_layout(db.get("layout")),
        "calibration": core.merged_calibration(db.get("calibration")),
        "full_layout": full.merged_layout(db.get("full_layout")),
        "full_calibration": full.merged_calibration(db.get("full_calibration")),
        "sheet_layout": sheet.merged_layout(db.get("sheet_layout")),
        "printers": configured_printers(db),
    })


def v1_blends_path() -> Path | None:
    """The live v1 database if the v1 app is installed, otherwise the bundled copy."""
    candidates = []
    if os.name == "nt":
        candidates.append(Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "LabelMaker" / "blends.csv")
    candidates += [DATA_DIR / "blends.csv", BASE_DIR / "blends.csv"]
    return next((path for path in candidates if path.exists()), None)


@app.post("/api/import-v1")
def import_v1():
    import csv

    path = v1_blends_path()
    if not path:
        return jsonify({"error": "לא נמצא קובץ blends.csv של הגרסה הישנה."}), 404
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    db = read_db()
    added = core.import_blends(db, rows)
    if added:
        write_db(db)
    log.info("imported %d of %d v1 blends from %s", added, len(rows), path)
    return jsonify({"added": added, "total": len(rows), "source": str(path), "products": db["products"]})


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
