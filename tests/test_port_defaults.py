import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8", errors="ignore")


class TestPortDefaults(unittest.TestCase):
    def test_python_webui_defaults_to_9999(self) -> None:
        app_main = read_text("app/app.py")
        webui_server = read_text("src/hdzz/api/webui/server.py")

        self.assertIn('parser.add_argument("--port", type=int, default=9999)', app_main)
        self.assertIn('parser.add_argument("--port", type=int, default=9999)', webui_server)

    def test_start_scripts_default_to_9999(self) -> None:
        mac_all = read_text("scripts/Start_AG_Matrix_ALL.command")
        mac_safe = read_text("scripts/Start_AG_Matrix_SAFE.command")
        win_fast = read_text("scripts/Start_AG_Matrix.bat")
        win_safe = read_text("scripts/Start_AG_Matrix_SAFE.bat")

        self.assertRegex(mac_all, re.compile(r'PORT="\$\{PORT:-9999\}"'))
        self.assertRegex(mac_safe, re.compile(r'PORT="\$\{PORT:-9999\}"'))
        self.assertIn('set "PORT=9999"', win_fast)
        self.assertIn('set "PORT=9999"', win_safe)


if __name__ == "__main__":
    unittest.main()
