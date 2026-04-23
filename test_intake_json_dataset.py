import json
import unittest
import tempfile
from pathlib import Path

from app.intake import classify_input


class IntakeJsonDatasetTests(unittest.TestCase):
    def test_single_json_file_folder_classified_as_dataset(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "dataset.json").write_text(json.dumps([{"a": 1}, {"a": 2}]), encoding="utf-8")
            res = classify_input(str(root)).to_dict()
            self.assertEqual(res.get("input_type"), "json_dataset")
            self.assertEqual(res.get("recommended_strategy"), "json_dataset_mode")

    def test_jsonl_file_classified_as_dataset(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "data.jsonl"
            p.write_text(json.dumps({"a": 1}) + "\n" + json.dumps({"a": 2}) + "\n", encoding="utf-8")
            res = classify_input(str(p), large_file_bytes=10).to_dict()
            self.assertEqual(res.get("input_type"), "json_dataset")
            self.assertEqual(res.get("recommended_strategy"), "json_dataset_mode")

    def test_dominant_jsonl_in_folder_classified_as_dataset(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "small.md").write_text("# hello\n", encoding="utf-8")
            big = root / "dataset.jsonl"
            # Make it "dominant" under the lowered threshold.
            big.write_text((json.dumps({"x": "y"}) + "\n") * 50, encoding="utf-8")
            res = classify_input(str(root), json_dataset_min_bytes=100, dominance_ratio=0.5).to_dict()
            self.assertEqual(res.get("input_type"), "json_dataset")
            details = (res.get("details") or {})
            dom = details.get("dominant_file") or {}
            self.assertTrue(str(dom.get("rel_path") or "").endswith("dataset.jsonl"))


if __name__ == "__main__":
    unittest.main()
