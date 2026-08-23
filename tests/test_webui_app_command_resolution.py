import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
APP_DIR = ROOT / "app"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from hdzz.api.webui import server  # noqa: E402


class TestWebUiAppCommandResolution(unittest.TestCase):
    def test_run_app_command_uses_source_launcher_when_cwd_has_no_app_py(self) -> None:
        calls = {}

        class _FakeProc:
            returncode = 0
            stderr = ""
            stdout = "ok"

        with patch("hdzz.api.webui.server.subprocess.run", return_value=_FakeProc()) as run_mock:
            with patch("hdzz.api.webui.server.os.getcwd", return_value="/tmp/no-app-here"):
                with patch.dict(server.os.environ, {"HDZZ_APP_DIR": "/wrong/source/root"}):
                    rc, out = server._run_app_command(["status"], timeout_s=5)

        self.assertEqual(rc, 0)
        self.assertEqual(out, "ok")
        cmd = run_mock.call_args.kwargs.get("args") or run_mock.call_args.args[0]
        self.assertEqual(cmd[:2], [sys.executable, "-u"])
        self.assertEqual(cmd[2], str((APP_DIR / "app.py").resolve()))
        self.assertEqual(cmd[3:], ["status"])
        self.assertEqual(run_mock.call_args.kwargs["env"]["HDZZ_APP_DIR"], "/tmp/no-app-here")

    def test_spawn_app_command_passes_runtime_directory_to_source_launcher(self) -> None:
        class _FakeProc:
            pass

        with patch("hdzz.api.webui.server.subprocess.Popen", return_value=_FakeProc()) as popen_mock:
            with patch("hdzz.api.webui.server.os.getcwd", return_value="/tmp/runtime-app"):
                with patch.dict(server.os.environ, {"HDZZ_APP_DIR": "/wrong/source/root"}):
                    server._spawn_app_command(["start-telegram-signal"])

        self.assertEqual(popen_mock.call_args.kwargs["env"]["HDZZ_APP_DIR"], "/tmp/runtime-app")

    def test_run_app_command_uses_bundle_executable_in_frozen_runtime(self) -> None:
        class _FakeProc:
            returncode = 0
            stderr = ""
            stdout = "ok"

        with patch("hdzz.api.webui.server.subprocess.run", return_value=_FakeProc()) as run_mock:
            with patch("hdzz.api.webui.server.is_frozen_runtime", return_value=True):
                with patch.object(server.sys, "executable", "/Applications/AG Matrix Standalone.app/Contents/MacOS/AG Matrix Standalone"):
                    rc, out = server._run_app_command(["stop"], timeout_s=5)

        self.assertEqual(rc, 0)
        self.assertEqual(out, "ok")
        cmd = run_mock.call_args.kwargs.get("args") or run_mock.call_args.args[0]
        self.assertEqual(
            cmd,
            ["/Applications/AG Matrix Standalone.app/Contents/MacOS/AG Matrix Standalone", "stop"],
        )

    def test_spawn_app_command_uses_bundle_executable_in_frozen_runtime(self) -> None:
        class _FakeProc:
            pass

        with patch("hdzz.api.webui.server.subprocess.Popen", return_value=_FakeProc()) as popen_mock:
            with patch("hdzz.api.webui.server.is_frozen_runtime", return_value=True):
                with patch.object(server.sys, "executable", "/Applications/AG Matrix Standalone.app/Contents/MacOS/AG Matrix Standalone"):
                    server._spawn_app_command(["stop", "--force"])

        cmd = popen_mock.call_args.kwargs.get("args") or popen_mock.call_args.args[0]
        self.assertEqual(
            cmd,
            ["/Applications/AG Matrix Standalone.app/Contents/MacOS/AG Matrix Standalone", "stop", "--force"],
        )


if __name__ == "__main__":
    unittest.main()
