import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
APP_DIR = ROOT / "app"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


from hdzz.api.webui import server  # noqa: E402


class TestWebUiServerMetadata(unittest.TestCase):
    def test_build_server_file_metadata_handles_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fake_path = Path(td) / "missing_server.py"
            meta = server.build_server_file_metadata(fake_path)

        self.assertEqual(meta["server_file"], str(fake_path.resolve()))
        self.assertEqual(meta["server_mtime"], 0)


if __name__ == "__main__":
    unittest.main()
