import json
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class TestJsonFileCache(unittest.TestCase):
    def test_read_dict_returns_deep_copy_of_cached_value(self) -> None:
        from hdzz.common.config import JsonFileCache

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            path.write_text(json.dumps({"a": {"b": 1}}, ensure_ascii=False), encoding="utf-8")
            cache = JsonFileCache()

            first = cache.read_dict(path, {})
            first["a"]["b"] = 99
            second = cache.read_dict(path, {})

            self.assertEqual(second, {"a": {"b": 1}})

    def test_read_dict_reloads_when_file_changes(self) -> None:
        from hdzz.common.config import JsonFileCache

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            path.write_text(json.dumps({"version": 1}, ensure_ascii=False), encoding="utf-8")
            cache = JsonFileCache()

            self.assertEqual(cache.read_dict(path, {}), {"version": 1})

            time.sleep(0.01)
            path.write_text(json.dumps({"version": 2}, ensure_ascii=False), encoding="utf-8")

            self.assertEqual(cache.read_dict(path, {}), {"version": 2})


if __name__ == "__main__":
    unittest.main()
