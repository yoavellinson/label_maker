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
import layout_dev_app  # noqa: E402

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

        def page_size(fmt):
            pdf = self.client.post("/api/print.pdf", json={"product": BASE, "daily": DAILY, "format": fmt}).data
            w, h = pdfium.PdfDocument(pdf)[0].get_size()
            text = pdfium.PdfDocument(pdf)[0].get_textpage().get_text_range()
            return (round(w / core.MM), round(h / core.MM)), text

        self.assertEqual(page_size("preprinted")[0], (100, 100))
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
