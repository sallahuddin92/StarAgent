import json
import unittest
import tempfile
from pathlib import Path

from app.research_mode import _extract_top_level_json_array_elements


class JsonArrayExtractionTests(unittest.TestCase):
    def test_extracts_first_elements_from_large_array_without_truncation(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "big_array.json"
            # Create a file that would break the old "slice then json.loads" approach by
            # having the first record exceed 1.5MB. The extractor should still yield it.
            big_str = "x" * (1_600_000)
            data = [
                {"id": 1, "text": big_str, "k": {"nested": [1, 2, 3]}},
                {"id": 2, "text": "small"},
                {"id": 3, "text": "small2"},
            ]
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            res = _extract_top_level_json_array_elements(p, max_elements=2, max_scan_bytes=5 * 1024 * 1024)
            self.assertTrue(res.get("ok"))
            elems = res.get("elements") or []
            self.assertEqual(len(elems), 2)
            self.assertEqual(elems[0].get("id"), 1)
            self.assertEqual(elems[1].get("id"), 2)


if __name__ == "__main__":
    unittest.main()

