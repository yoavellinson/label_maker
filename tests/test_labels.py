"""Regression tests for the label layout engine, migration and API.

Run with:  .venv/bin/python -m unittest discover tests
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="label-maker-test-"))
os.environ["LABEL_MAKER_V2_DATA_DIR"] = str(TMP)

import label_core as core  # noqa: E402
import label_full as full  # noqa: E402
import label_sheet as sheet  # noqa: E402
import layout_dev_app  # noqa: E402
import printing  # noqa: E402
import updater  # noqa: E402

core.register_fonts(ROOT / "fonts")
LAYOUT = core.merged_layout({})
DAILY = {"weight": "1", "date": "24/11/2026"}
BASE = {
    "title": "אתיופיה דג׳ימה",
    "tastes": "חזק, אש, מעושן",
    "roast": "קלייה בינונית כהה",
    "bean": "single:ערביקה",
    "process": "חצי שטוף",
    "kosher": "כשרות חתם סופר",
    "score": "",
    "page": 6,
}


def by_role(fields, role):
    return [f for f in fields if f["role"] == role]


def row(fields, group):
    return sorted((f for f in fields if f.get("group") == group), key=lambda f: f["x"])


class ComposeTests(unittest.TestCase):
    def test_rows_follow_configured_order_left_to_right(self):
        fields = core.compose(BASE, DAILY, LAYOUT)
        top = [f["role"] for f in row(fields, "השורה העליונה")]
        bottom = [f["role"] for f in row(fields, "השורה התחתונה")]
        self.assertEqual(top, ["roast", "separator", "tastes"])
        self.assertEqual(bottom, ["kosher", "separator", "process", "separator", "bean"])

    def test_blank_processing_drops_prefix_and_separator(self):
        fields = core.compose(dict(BASE, process=""), DAILY, LAYOUT)
        bottom = [f["role"] for f in row(fields, "השורה התחתונה")]
        self.assertEqual(bottom, ["kosher", "separator", "bean"])
        self.assertFalse(any("עיבוד" in f["text"] for f in fields))

    def test_segments_never_touch_and_gaps_are_even(self):
        for product in (BASE, dict(BASE, tastes="פירות יער, שוקולד מריר, הדרים, קרמל, וניל, אגוזי לוז")):
            for group in core.ROW_GROUP_LABELS:
                items = row(core.compose(product, DAILY, LAYOUT), group)
                gaps = [round(b["x"] - (a["x"] + a["width"]), 3) for a, b in zip(items, items[1:])]
                for gap in gaps:
                    self.assertAlmostEqual(gap, LAYOUT["separator"]["gap"], places=2)

    def test_long_text_shrinks_to_fit_column(self):
        product = dict(
            BASE,
            title="ברזיל סנטוס קרמל ודבש מיוחד",
            tastes="פירות יער, שוקולד מריר, הדרים, קרמל, וניל, אגוזי לוז",
            kosher="כשר בהשגחת הרבנות חתם סופר",
        )
        fields = core.compose(product, DAILY, LAYOUT)
        self.assertEqual(core.overflowing(fields, LAYOUT), [])
        self.assertIn("השורה העליונה", core.shrunk_groups(fields))
        self.assertIn("שם הקפה", core.shrunk_groups(fields))
        for f in fields:
            if f.get("group"):
                self.assertLessEqual(f["x"] + f["width"], LAYOUT["text_right"] + 0.01)
                self.assertGreaterEqual(f["x"], LAYOUT["margin_left"] - 0.01)

    def test_specialty_skips_preprinted_weight_and_prints_score(self):
        fields = core.compose(dict(BASE, page=10, score="85.4"), DAILY, LAYOUT)
        self.assertFalse(by_role(fields, "weight"))
        self.assertFalse(by_role(fields, "weight_unit"))
        self.assertEqual(by_role(fields, "score")[0]["text"], "85.4")

    def test_regular_design_ignores_score_and_uses_fraction_position(self):
        fields = core.compose(dict(BASE, score="85.4"), {"weight": "0.5", "date": ""}, LAYOUT)
        self.assertFalse(by_role(fields, "score"))
        self.assertFalse(by_role(fields, "date"))
        weight = by_role(fields, "weight")[0]
        self.assertEqual(weight["text"], "½")
        self.assertEqual(weight["size"], LAYOUT["weight_fraction"]["size"])

    def test_old_database_is_upgraded(self):
        db = {"version": 3, "layout": {"rows": [{}, {"items": ["bean", "process", "kosher"]}]},
              "products": [{"kosher": "כשר בהשגחת הרבנות חתם סופר"}]}
        self.assertTrue(core.upgrade_db(db))
        self.assertEqual(db["products"][0]["kosher"], "כשרות חתם סופר")
        self.assertEqual(db["layout"]["rows"][1]["items"], ["kosher", "process", "bean"])
        self.assertFalse(core.upgrade_db(db))

    def test_migration_uses_standard_kosher_text(self):
        presets = json.loads((ROOT / "grid" / "layout_presets.json").read_text(encoding="utf-8"))
        kosher = {p["kosher"] for p in core.migrate_presets(presets)["products"]}
        self.assertEqual(kosher, {"כשרות חתם סופר"})

    def test_bean_texts(self):
        self.assertEqual(core.bean_text("single:רובוסטה"), "חד זני - רובוסטה")
        self.assertEqual(core.bean_text("blend:70"), "תערובת 70% ערביקה")
        self.assertEqual(core.bean_text(""), "")

    def test_pdf_has_one_page_per_copy(self):
        import pypdfium2 as pdfium

        pdf = core.label_pdf(core.compose(BASE, DAILY, LAYOUT), core.merged_calibration({}), copies=3)
        self.assertEqual(len(pdfium.PdfDocument(pdf)), 3)


class SurpriseAndFullLabelTests(unittest.TestCase):
    def test_surprise_replaces_date_on_preprinted_label(self):
        fields = core.compose(BASE, dict(DAILY, date_mode="surprise"), LAYOUT)
        dates = by_role(fields, "date")
        self.assertEqual([f["text"] for f in dates], ["הפתעה!"])

    def full_pdf(self, product, daily):
        import pypdfium2 as pdfium

        pdf, warnings = full.label_pdf(product, daily, full.merged_layout({}), full.merged_calibration({}), None, copies=2)
        document = pdfium.PdfDocument(pdf)
        text = document[0].get_textpage().get_text_range()
        w, h = document[0].get_size()
        return len(document), text, (round(w / core.MM), round(h / core.MM)), warnings

    def test_rotation_is_drawn_not_flagged(self):
        import pypdfium2 as pdfium

        for rotation, size in ((0, (50, 100)), (180, (50, 100)), (90, (100, 50)), (-90, (100, 50))):
            pdf, _ = full.label_pdf(BASE, DAILY, full.merged_layout({}), full.merged_calibration({"rotation": rotation}), None)
            page = pdfium.PdfDocument(pdf)[0]
            self.assertEqual(page.get_rotation(), 0)
            self.assertEqual(tuple(round(v / core.MM) for v in page.get_size()), size)

    def test_full_label_size_copies_and_content(self):
        pages, text, size, warnings = self.full_pdf(dict(BASE, description="קפה עם גוף מלא", page=10, score="86"), DAILY)
        self.assertEqual(pages, 2)
        self.assertEqual(size, (50, 100))
        self.assertIn("24/11/2026", text)
        self.assertIn("SPECIALTY COFFEE 86", text)
        self.assertEqual(warnings, [])

    def test_full_label_surprise_has_no_date_or_weight(self):
        _, text, _, _ = self.full_pdf(BASE, dict(DAILY, date_mode="surprise"))
        self.assertFalse(any(ch.isdigit() for ch in text), text)

    def test_full_label_long_description_warns_instead_of_overflowing(self):
        _, _, _, warnings = self.full_pdf(dict(BASE, description="מילה " * 200), DAILY)
        self.assertTrue(any(w.startswith("תיאור") for w in warnings))


class SheetTests(unittest.TestCase):
    def pages(self, **kwargs):
        import pypdfium2 as pdfium

        pdf, warnings, pages = sheet.sheet_pdf(dict(BASE, description="תיאור"), sheet.merged_layout({}), None, **kwargs)
        document = pdfium.PdfDocument(pdf)
        w, h = document[0].get_size()
        return len(document), pages, (round(w / core.MM), round(h / core.MM)), document[0].get_textpage().get_text_range()

    def test_full_sheet_is_one_a4_page(self):
        count, pages, size, text = self.pages(count=12)
        self.assertEqual((count, pages, size), (1, 1, (210, 297)))
        self.assertNotIn("2026", text)
        self.assertEqual(text.count("תיאור"[::-1]) + text.count("תיאור"), 12)

    def test_start_position_overflows_to_next_page(self):
        count, pages, _, _ = self.pages(count=12, start=5)
        self.assertEqual((count, pages), (2, 2))

    def test_labels_fill_exact_slots_with_10mm_margins(self):
        from io import BytesIO

        from PIL import Image

        layout = sheet.merged_layout({})
        self.assertEqual((layout["margin_top"], layout["label_w"], layout["label_h"]), (10.0, 105.0, 46.0))
        pdf, _, _ = sheet.sheet_pdf(BASE, layout, None, count=12)
        image = Image.open(BytesIO(core.pdf_to_png(pdf, 4, transparent=False))).convert("L")
        px = image.load()
        column = [px[int(52 * 4), y] for y in range(image.height)]
        printed = [y for y, v in enumerate(column) if v < 250]
        self.assertAlmostEqual(printed[0] / 4, 10.0, delta=0.5)
        self.assertAlmostEqual((printed[-1] + 1) / 4, 286.0, delta=0.5)
        # The outer ends (label bottoms) are white where the printer cannot print.
        edge = int(layout["white_edge"] * 4) - 1
        for y in range(int(12 * 4), int(284 * 4), 9):
            for x in list(range(0, edge)) + list(range(image.width - edge, image.width)):
                self.assertGreater(px[x, y], 245, (x, y))
        # ...while the artwork still reaches the middle of the page from both sides.
        self.assertLess(px[image.width // 2 - 3, int(30 * 4)], 250)
        self.assertLess(px[image.width // 2 + 3, int(30 * 4)], 250)

    def test_label_grid_fills_a4_height(self):
        layout = sheet.merged_layout({})
        used = layout["margin_top"] * 2 + layout["rows_per_page"] * layout["label_h"]
        self.assertLessEqual(used, 297.0)
        self.assertEqual(layout["cols"] * layout["label_w"], 210.0)


class V1ImportTests(unittest.TestCase):
    def test_blend_rows_map_to_products(self):
        rows = [
            {"badge_line_1": "תערובת Ravello", "badge_line_2": "70% ערביקה", "roast_level": "קלייה בינונית כהה",
             "note_1": "שוקולד מריר", "note_2": "מוקה", "note_3": "", "description": "קפה  טוב\nמאוד"},
            {"badge_line_1": "קולומביה", "badge_line_2": "Hulia", "roast_level": "", "specialty_coffee": "1",
             "cup_score": "85.5", "specialty_process": "שטוף", "note_1": "פירותי"},
            {"badge_line_1": "שרי הודו", "badge_line_2": "רובוסטה", "roast_level": "קלייה בינונית", "note_1": "אדמתי"},
        ]
        ravello, hulia, cherry = (core.product_from_blend(r) for r in rows)
        self.assertEqual((ravello["title"], ravello["bean"], ravello["tastes"]), ("תערובת Ravello", "blend:70", "שוקולד מריר, מוקה"))
        self.assertEqual(ravello["description"], "קפה טוב מאוד")
        self.assertEqual((hulia["title"], hulia["score"], hulia["process"], hulia["page"]), ("קולומביה Hulia", "85.5", "שטוף", 11))
        self.assertEqual((cherry["title"], cherry["bean"]), ("שרי הודו", "single:רובוסטה"))

    def test_import_skips_existing(self):
        rows = [{"badge_line_1": "פרו", "roast_level": "קלייה בינונית"}]
        db = {"products": [dict(core.EMPTY_PRODUCT, title="פרו", roast="קלייה בינונית")]}
        self.assertEqual(core.import_blends(db, rows), 0)
        self.assertEqual(core.import_blends({"products": []}, rows), 1)


class UpdaterTests(unittest.TestCase):
    def test_password(self):
        self.assertTrue(updater.check_password("yoav"))
        self.assertFalse(updater.check_password("Yoav"))
        self.assertFalse(updater.check_password(""))

    def test_version_compare(self):
        self.assertGreater(updater.parse_version("v3.10.0"), updater.parse_version("3.9.9"))
        self.assertEqual(updater.parse_version("3.1.0"), (3, 1, 0))

    def test_app_reports_bundled_version(self):
        self.assertEqual(layout_dev_app.APP_VERSION, (ROOT / "VERSION").read_text().strip())

    def test_check_endpoint(self):
        from unittest import mock

        client = layout_dev_app.app.test_client()
        self.assertEqual(client.post("/api/update/check", json={"password": "nope"}).status_code, 403)
        release = {"version": "99.0.0", "notes": "חדש", "url": "x", "size": 1}
        with mock.patch.object(updater, "latest_release", return_value=release):
            result = client.post("/api/update/check", json={"password": "yoav"}).get_json()
        self.assertTrue(result["available"])
        self.assertEqual(result["latest"], "99.0.0")
        self.assertFalse(result["can_install"])  # tests do not run as the frozen Windows app
        with mock.patch.object(updater, "latest_release", return_value=dict(release, version="0.1")):
            self.assertFalse(client.post("/api/update/check", json={"password": "yoav"}).get_json()["available"])
        self.assertEqual(client.post("/api/update/install", json={"password": "yoav"}).status_code, 400)
        self.assertEqual(client.post("/api/update/install", json={"password": "x"}).status_code, 403)


class FakeDC:
    """Stands in for a Windows printer device context (300 dpi, given paper size in mm)."""

    media_mm = (100, 100)

    def __init__(self, log):
        self.log = log

    def CreatePrinterDC(self, name):
        self.log.append(("printer", name))

    def GetDeviceCaps(self, index):
        return {printing.LOGPIXELSX: 300, printing.LOGPIXELSY: 300,
                printing.PHYSICALOFFSETX: 50, printing.PHYSICALOFFSETY: 40,
                printing.PHYSICALWIDTH: round(self.media_mm[0] / 25.4 * 300),
                printing.PHYSICALHEIGHT: round(self.media_mm[1] / 25.4 * 300)}[index]

    def StartDoc(self, name):
        self.log.append(("doc", name))

    def StartPage(self):
        pass

    def EndPage(self):
        self.log.append(("page",))

    def EndDoc(self):
        self.log.append(("end",))

    def AbortDoc(self):
        pass

    def DeleteDC(self):
        pass

    def GetHandleOutput(self):
        return 0


class DirectPrintTests(unittest.TestCase):
    def fake_windows(self, printers=("Thermal", "Color Label", "Office A4")):
        from unittest import mock

        log = []

        class FakeDib:
            def __init__(self, image):
                self.image = image

            def draw(self, handle, box):
                log.append(("draw", box, self.image.size))

        win32print = mock.Mock(PRINTER_ENUM_LOCAL=2, PRINTER_ENUM_CONNECTIONS=4)
        self.dialog_answer = printing.IDOK
        win32print.DocumentProperties.side_effect = lambda *args: log.append(("dialog", args[2])) or self.dialog_answer
        win32print.EnumPrinters.return_value = [(0, "", name, "") for name in printers]
        win32print.GetDefaultPrinter.return_value = printers[0]
        devmode = mock.Mock(Fields=0)
        win32print.GetPrinter.return_value = {"pDevMode": devmode}
        win32ui = mock.Mock()
        win32ui.CreateDC.side_effect = lambda: FakeDC(log)
        win32ui.CreateDCFromHandle.side_effect = lambda handle: FakeDC(log)
        win32gui = mock.Mock()
        win32gui.CreateDC.side_effect = lambda driver, name, mode: log.append(("devmode", name, mode.Scale, mode.Fields)) or 1
        image_win = mock.Mock(Dib=FakeDib)
        patches = [
            mock.patch.object(printing, "win32gui", win32gui),
            mock.patch.object(printing, "win32print", win32print),
            mock.patch.object(printing, "win32ui", win32ui),
            mock.patch.object(printing, "ImageWin", image_win),
            mock.patch.object(printing.os, "name", "nt"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return log

    def test_prints_at_actual_size_compensating_printer_margin(self):
        log = self.fake_windows()
        pdf = core.label_pdf(core.compose(BASE, DAILY, LAYOUT), core.merged_calibration({}), copies=2)
        self.assertEqual(printing.print_pdf(pdf, "Thermal"), 2)
        draws = [entry for entry in log if entry[0] == "draw"]
        mm100 = round(100 / 25.4 * 300)
        self.assertEqual(draws[0][1], (-50, -40, mm100 - 50, mm100 - 40))
        # Only scaling is forced (100%); paper size/orientation stay the printer's own.
        self.assertIn(("devmode", "Thermal", 100, printing.DM_SCALE), log)

    def test_settings_window_before_printing(self):
        log = self.fake_windows()
        pdf = core.label_pdf(core.compose(BASE, DAILY, LAYOUT), core.merged_calibration({}))
        printing.print_pdf(pdf, "Thermal", prompt=True)
        self.assertIn(("dialog", "Thermal"), log)
        self.assertTrue(any(entry[0] == "draw" for entry in log))

    def test_cancel_in_settings_window_prints_nothing(self):
        log = self.fake_windows()
        self.dialog_answer = 2  # IDCANCEL
        pdf = core.label_pdf(core.compose(BASE, DAILY, LAYOUT), core.merged_calibration({}))
        with self.assertRaises(printing.PrintCancelled):
            printing.print_pdf(pdf, "Thermal", prompt=True)
        self.assertFalse(any(entry[0] in ("draw", "doc") for entry in log))

    def test_prompt_setting_reaches_printer_and_can_be_turned_off(self):
        from unittest import mock

        self.fake_windows()
        client = layout_dev_app.app.test_client()
        client.post("/api/settings", json={"printers": {"preprinted": "Thermal"}})
        self.assertTrue(client.get("/api/state").get_json()["print_prompt"])
        seen = []
        with mock.patch.object(printing, "print_pdf", side_effect=lambda pdf, printer, job_name, prompt: seen.append(prompt) or 1):
            client.post("/api/print-direct", json={"product": BASE, "format": "preprinted"})
            client.post("/api/settings", json={"print_prompt": False})
            client.post("/api/print-direct", json={"product": BASE, "format": "preprinted"})
        self.assertEqual(seen, [True, False])
        with mock.patch.object(printing, "print_pdf", side_effect=printing.PrintCancelled("x")):
            self.assertTrue(client.post("/api/print-direct", json={"product": BASE, "format": "preprinted"}).get_json()["cancelled"])
        client.post("/api/settings", json={"printers": {}, "print_prompt": True})

    def draws_on_media(self, pdf, media_mm):
        log = self.fake_windows()
        FakeDC.media_mm = media_mm
        self.addCleanup(setattr, FakeDC, "media_mm", (100, 100))
        printing.print_pdf(pdf, "Color Label")
        return [entry for entry in log if entry[0] == "draw"]

    def test_narrow_label_turns_to_fit_landscape_roll(self):
        pdf, _ = full.label_pdf(BASE, DAILY, full.merged_layout({}), full.merged_calibration({}), None)
        draw = self.draws_on_media(pdf, (100, 50))[0]
        x0, y0, x1, y1 = draw[1]
        self.assertEqual((x1 - x0, y1 - y0), (round(100 / 25.4 * 300), round(50 / 25.4 * 300)))
        self.assertEqual(draw[2][0] > draw[2][1], True)  # the image itself was turned

    def test_narrow_label_on_portrait_media_is_not_turned(self):
        pdf, _ = full.label_pdf(BASE, DAILY, full.merged_layout({}), full.merged_calibration({}), None)
        x0, y0, x1, y1 = self.draws_on_media(pdf, (50, 100))[0][1]
        self.assertLess(x1 - x0, y1 - y0)

    def test_square_label_never_turned_and_centred(self):
        pdf = core.label_pdf(core.compose(BASE, DAILY, LAYOUT), core.merged_calibration({}))
        x0, y0, x1, y1 = self.draws_on_media(pdf, (104, 100))[0][1]
        side = round(100 / 25.4 * 300)
        self.assertEqual((x1 - x0, y1 - y0), (side, side))
        self.assertEqual(x0, (round(104 / 25.4 * 300) - side) // 2 - 50)

    def test_unknown_printer_is_rejected(self):
        self.fake_windows()
        with self.assertRaises(printing.PrintError):
            printing.print_pdf(b"%PDF", "Missing")

    def test_each_label_type_goes_to_its_printer(self):
        from unittest import mock

        self.fake_windows()
        client = layout_dev_app.app.test_client()
        assigned = {"preprinted": "Thermal", "full": "Color Label", "surprise": "Color Label", "sheet": "Office A4"}
        client.post("/api/settings", json={"printers": assigned})
        self.assertEqual(client.get("/api/state").get_json()["printers"], assigned)
        sent = []
        with mock.patch.object(printing, "print_pdf", side_effect=lambda pdf, printer, job_name, prompt: sent.append(printer) or 1):
            for fmt in ("preprinted", "full", "surprise", "sheet"):
                response = client.post("/api/print-direct", json={"product": BASE, "daily": DAILY, "format": fmt})
                self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(sent, ["Thermal", "Color Label", "Color Label", "Office A4"])
        client.post("/api/settings", json={"printers": {}})
        self.assertEqual(client.post("/api/print-direct", json={"product": BASE, "format": "full"}).status_code, 400)


class MigrationTests(unittest.TestCase):
    def test_legacy_preset_with_misclassified_items(self):
        record = {
            "id": "abc",
            "layout": {"editor_state": {
                "originalPdfPage": 12,
                "background": {"x": -11.7, "y": -11.6},
                "texts": [
                    {"role": "title", "text": "אתיופיה"},
                    {"role": "tastes", "text": "חזק, אש"},
                    {"role": "roast_level", "text": "קלייה כהה"},
                    {"role": "weight_value", "text": "|"},
                    {"role": "tastes", "text": "תערובת 70% ערביקה"},
                    {"role": "process_label", "text": "עיבוד:"},
                    {"role": "roast_date", "text": "07/09/2026"},
                    {"role": "roast_date", "text": ""},
                    {"role": "kosher", "text": "כשרות חתם סופר"},
                ],
            }},
        }
        db = core.migrate_presets({"layouts": [record]})
        product = db["products"][0]
        self.assertEqual(product["title"], "אתיופיה")
        self.assertEqual(product["tastes"], "חזק, אש")
        self.assertEqual(product["bean"], "blend:70")
        self.assertEqual(product["process"], "")
        self.assertEqual(product["page"], 12)
        self.assertEqual((db["calibration"]["bg_x"], db["calibration"]["bg_y"]), (-11.7, -11.6))

    def test_real_bundled_presets_migrate(self):
        presets = json.loads((ROOT / "grid" / "layout_presets.json").read_text(encoding="utf-8"))
        db = core.migrate_presets(presets)
        self.assertTrue(db["products"])
        for product in db["products"]:
            self.assertIn(product["page"], core.PAGES)
            self.assertNotIn("|", product["tastes"].strip()[:1])


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        grid = TMP / "grid"
        grid.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "grid" / "layout_presets.json", grid / "layout_presets.json")
        cls.client = layout_dev_app.app.test_client()

    def test_save_update_delete_keeps_other_products(self):
        before = self.client.get("/api/state").get_json()["products"]
        created = self.client.post("/api/products", json={"product": dict(BASE, title="בדיקה")}).get_json()["product"]
        updated = self.client.post("/api/products", json={"product": dict(created, roast="קלייה בהירה")}).get_json()["product"]
        self.assertEqual(updated["id"], created["id"])
        products = self.client.get("/api/state").get_json()["products"]
        self.assertEqual(len(products), len(before) + 1)
        self.assertEqual(self.client.delete(f"/api/products/{created['id']}").status_code, 200)
        after = self.client.get("/api/state").get_json()["products"]
        self.assertEqual(sorted(p["id"] for p in after), sorted(p["id"] for p in before))
        self.assertTrue(list((TMP / "grid" / "backups").glob("labels_db-*.json")))

    def test_legacy_database_is_not_modified(self):
        legacy = TMP / "grid" / "layout_presets.json"
        original = legacy.read_bytes()
        self.client.get("/api/state")
        self.client.post("/api/products", json={"product": dict(BASE, title="x")})
        self.assertEqual(legacy.read_bytes(), original)

    def test_title_required(self):
        response = self.client.post("/api/products", json={"product": dict(BASE, title="  ")})
        self.assertEqual(response.status_code, 400)

    def test_full_format_preview_and_print(self):
        payload = {"product": dict(BASE, description="תיאור"), "daily": DAILY, "format": "full"}
        preview = self.client.post("/api/preview", json=payload).get_json()
        self.assertTrue(preview["image"].startswith("data:image/png;base64,"))
        pdf = self.client.post("/api/print.pdf", json=dict(payload, copies=3))
        import pypdfium2 as pdfium

        self.assertEqual(len(pdfium.PdfDocument(pdf.data)), 3)

    def test_label_formats_route_to_the_right_renderer(self):
        import pypdfium2 as pdfium

        self.client = layout_dev_app.app.test_client()

        def page_size(fmt):
            pdf = self.client.post("/api/print.pdf", json={"product": BASE, "daily": DAILY, "format": fmt}).data
            w, h = pdfium.PdfDocument(pdf)[0].get_size()
            text = pdfium.PdfDocument(pdf)[0].get_textpage().get_text_range()
            return (round(w / core.MM), round(h / core.MM)), text

        self.assertEqual(page_size("preprinted")[0], (100, 100))
        import pypdfium2 as pdfium

        def sheet_pages(**extra):
            pdf = self.client.post("/api/print.pdf", json=dict({"product": BASE, "format": "sheet"}, **extra)).data
            return len(pdfium.PdfDocument(pdf))

        self.assertEqual(sheet_pages(), 1)                       # default: one full sheet
        self.assertEqual(sheet_pages(copies=2), 2)               # counter is in sheets
        self.assertEqual(sheet_pages(copies=1, start=7), 1)      # partly used sheet is finished, not spilled
        self.assertEqual(page_size("sheet")[0], (210, 297))
        self.assertEqual(page_size("full")[0], (50, 100))
        size, text = page_size("surprise")
        self.assertEqual(size, (50, 100))
        self.assertNotIn("2026", text)
        self.assertIn("2026", page_size("full")[1])

    def test_description_is_saved(self):
        saved = self.client.post("/api/products", json={"product": dict(BASE, title="עם תיאור", description="  שורה  ")}).get_json()["product"]
        self.assertEqual(saved["description"], "שורה")
        self.client.delete(f"/api/products/{saved['id']}")

    def test_preview_and_print(self):
        payload = {"product": BASE, "daily": DAILY}
        preview = self.client.post("/api/preview", json=payload).get_json()
        self.assertTrue(preview["image"].startswith("data:image/png;base64,"))
        pdf = self.client.post("/api/print.pdf", json=dict(payload, copies=2))
        self.assertEqual(pdf.mimetype, "application/pdf")


if __name__ == "__main__":
    unittest.main()
