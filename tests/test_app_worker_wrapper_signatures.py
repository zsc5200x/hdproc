import inspect
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
SRC = ROOT / "src"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class TestAppWorkerWrapperSignatures(unittest.TestCase):
    def test_wrapper_mains_accept_optional_argv(self) -> None:
        modules = [
            "ag_wmc_watch",
            "follow_engine",
            "member_arsenal_watch",
            "settlement_importer",
            "telegram_bot",
            "webui_server",
        ]
        for name in modules:
            mod = __import__(name)
            sig = inspect.signature(mod.main)
            params = list(sig.parameters.values())
            self.assertGreaterEqual(len(params), 1, msg=f"{name}.main should accept argv")
            self.assertIn(params[0].kind, (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD))


if __name__ == "__main__":
    unittest.main()
