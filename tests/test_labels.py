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

    def test_preview_and_print(self):
        payload = {"product": BASE, "daily": DAILY}
        preview = self.client.post("/api/preview", json=payload).get_json()
        self.assertTrue(preview["image"].startswith("data:image/png;base64,"))
        pdf = self.client.post("/api/print.pdf", json=dict(payload, copies=2))
        self.assertEqual(pdf.mimetype, "application/pdf")


if __name__ == "__main__":
    unittest.main()
