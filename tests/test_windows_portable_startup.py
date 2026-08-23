import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8", errors="ignore")


class TestWindowsPortableStartup(unittest.TestCase):
    def test_root_windows_start_scripts_use_bundled_python_for_full_start(self) -> None:
        fast = read_text("scripts/Start_AG_Matrix.bat")
        safe = read_text("scripts/Start_AG_Matrix_SAFE.bat")

        self.assertIn('"%~dp0..\\\\python\\\\python.exe" app.py start --host %HOST% --port %PORT%', fast)
        self.assertIn('"%~dp0..\\\\python\\\\python.exe" app.py start --host %HOST% --port %PORT%', safe)
        self.assertNotIn("webui_server.py --host %HOST% --port %PORT%", fast)
        self.assertNotIn("webui_server.py --host %HOST% --port %PORT%", safe)

    def test_package_portable_generates_full_start_launcher(self) -> None:
        packer = read_text("app/tools/package_portable.ps1")

        self.assertIn('%~dp0python\\\\python.exe app.py start --host %HOST% --port %PORT%', packer)
        self.assertNotIn('%~dp0python\\\\python.exe webui_server.py --host %HOST% --port %PORT%', packer)

    def test_readme_portable_describes_full_start(self) -> None:
        readme = read_text("README_PORTABLE.txt")

        self.assertIn("Auto-starts the full project stack", readme)
        self.assertNotIn("Use WebUI Start/Stop to control core workers", readme)


if __name__ == "__main__":
    unittest.main()
