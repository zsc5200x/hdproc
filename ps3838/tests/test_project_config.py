import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import project_config


class ProjectConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_file = self.root / "config.json"
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        for name, value in (("PROJECT_DIR", self.root), ("DEFAULT_CONFIG_PATH", self.config_file)):
            patcher = patch.object(project_config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_config(self, values, path=None):
        path = path or self.config_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values), encoding="utf-8")
        return path

    def test_optional_default_file_and_paths(self):
        config = project_config.load_config()
        for key, path in {
            "capture_path": "capture.jsonl", "events_file": "events_list.csv",
            "merge_db_path": "fixture_merge.sqlite3", "run_dir": ".run",
            "log_dir": "logs", "crown_data_dir": "crown/data",
        }.items():
            with self.subTest(key=key):
                self.assertEqual(project_config.get_path(config, key), self.root / path)
        self.assertEqual(config["crown_cdp_url"], config["cdp_url"])
        self.assertEqual(config["dashboard_url"], "http://127.0.0.1:8787")

    def test_defaults_are_not_shared_between_loads(self):
        config = project_config.load_config()
        config["target_hosts"].append("changed.invalid")
        self.assertNotIn("changed.invalid", project_config.load_config()["target_hosts"])

    def test_selected_file_takes_precedence_over_default_file(self):
        self.write_config({"dashboard_port": 9001})
        selected = self.write_config({"dashboard_port": 9002}, self.root / "selected.json")
        os.environ["PS3838_CONFIG"] = str(selected)
        self.assertEqual(project_config.load_config()["dashboard_port"], 9002)

    def test_environment_takes_precedence_over_file(self):
        self.write_config({"cdp_url": "http://file:9222", "dashboard_port": 9001,
                           "capture_path": "from-file.jsonl", "run_dir": "file-run"})
        os.environ.update({"CDP_URL": "http://shared:9222", "CAPTURE_CDP_URL": "http://specific:9223",
                           "DASHBOARD_PORT": "9002", "CAPTURE_OUTPUT": "from-env.jsonl", "RUN_DIR": "env-run"})
        config = project_config.load_config()
        self.assertEqual(config["cdp_url"], "http://specific:9223")
        self.assertEqual(config["crown_cdp_url"], "http://specific:9223")
        self.assertEqual(config["dashboard_port"], 9002)
        self.assertEqual(project_config.get_path(config, "capture_path"), self.root / "from-env.jsonl")
        self.assertEqual(project_config.get_path(config, "run_dir"), self.root / "env-run")

    def test_crown_cdp_specific_override(self):
        self.write_config({"cdp_url": "http://shared:9222", "crown_cdp_url": "http://crown-file:9223"})
        self.assertEqual(project_config.load_config()["crown_cdp_url"], "http://crown-file:9223")
        os.environ["CROWN_CDP_URL"] = "http://crown-env:9224"
        config = project_config.load_config()
        self.assertEqual(config["crown_cdp_url"], "http://crown-env:9224")
        self.assertEqual(config["cdp_url"], "http://shared:9222")

    def test_relative_config_is_relative_to_project(self):
        selected = self.write_config({"dashboard_port": 9100}, self.root / "configs" / "test.json")
        os.environ["PS3838_CONFIG"] = "configs/test.json"
        previous = Path.cwd()
        other = self.root / "unrelated"
        other.mkdir()
        try:
            os.chdir(other)
            self.assertEqual(project_config.config_path(), selected)
            self.assertEqual(project_config.load_config()["dashboard_port"], 9100)
        finally:
            os.chdir(previous)

    def test_explicit_missing_config_is_an_error(self):
        os.environ["PS3838_CONFIG"] = str(self.root / "missing.json")
        with self.assertRaisesRegex(RuntimeError, "无法读取配置文件"):
            project_config.load_config()

    def test_malformed_json_is_an_error(self):
        self.config_file.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "无法读取配置文件"):
            project_config.load_config()

    def test_non_object_config_is_an_error(self):
        for value in ([], None, "unexpected", 1):
            with self.subTest(value=value):
                self.write_config(value)
                with self.assertRaisesRegex(RuntimeError, "顶层必须是 JSON 对象"):
                    project_config.load_config()

    def test_invalid_ports_are_rejected(self):
        for key in ("dashboard_port", "crown_port"):
            for value in (0, 65536, -1, True, 1.5, "bad", None):
                with self.subTest(key=key, value=value):
                    self.write_config({key: value})
                    with self.assertRaisesRegex(RuntimeError, key):
                        project_config.load_config()

    def test_invalid_hosts_are_rejected(self):
        for value in ("", " ", "bad host", "http://127.0.0.1"):
            with self.subTest(value=value):
                self.write_config({"dashboard_host": value})
                with self.assertRaisesRegex(RuntimeError, "dashboard_host"):
                    project_config.load_config()

    def test_legacy_aliases_are_normalized(self):
        aliases = {"events_path": "legacy.csv", "output": "live.csv", "latest_output": "latest.csv",
                   "normalized_output": "normalized.csv", "fixture_merge_db_path": "legacy.sqlite3"}
        self.write_config(aliases)
        config = project_config.load_config()
        for canonical, legacy in (("events_file", "events_path"), ("live_snapshot_file", "output"),
                                  ("latest_output_file", "latest_output"), ("normalized_output_file", "normalized_output"),
                                  ("merge_db_path", "fixture_merge_db_path")):
            with self.subTest(key=canonical):
                self.assertEqual(project_config.get_path(config, canonical), self.root / aliases[legacy])
        self.assertEqual(config["fixture_merge_db_path"], config["merge_db_path"])
        self.assertEqual(project_config.get_path(config, "fixture_merge_db_path"), self.root / "legacy.sqlite3")

    def test_canonical_path_wins_over_legacy_alias(self):
        self.write_config({"merge_db_path": "canonical.db", "fixture_merge_db_path": "old.db"})
        self.assertEqual(project_config.get_path(project_config.load_config(), "merge_db_path"), self.root / "canonical.db")
        os.environ.update({"FIXTURE_MERGE_DB_PATH": "legacy-env.db", "MERGE_DB_PATH": "canonical-env.db"})
        self.assertEqual(project_config.get_path(project_config.load_config(), "merge_db_path"), self.root / "canonical-env.db")

    def test_runtime_directory_environment_overrides(self):
        os.environ.update({"RUN_DIR": "var/run", "LOG_DIR": "var/logs", "CROWN_DATA_DIR": "var/crown"})
        config = project_config.load_config()
        for key, expected in (("run_dir", "var/run"), ("log_dir", "var/logs"), ("crown_data_dir", "var/crown")):
            self.assertEqual(project_config.get_path(config, key), self.root / expected)

    def test_service_url_handles_bind_addresses(self):
        self.assertEqual(project_config.service_url({"dashboard_host": "0.0.0.0", "dashboard_port": 9001}, "dashboard"), "http://127.0.0.1:9001")
        self.assertEqual(project_config.service_url({"crown_host": "::", "crown_port": 9002}, "crown"), "http://[::1]:9002")
        self.assertEqual(project_config.service_url({"crown_host": "::", "crown_port": 9002}, "crown", connect=False), "http://[::]:9002")
        with self.assertRaises(ValueError):
            project_config.service_url({}, "unknown")

    def test_target_hosts_accept_environment_list(self):
        os.environ["CAPTURE_TARGET_HOSTS"] = " .EXAMPLE.COM, second.example, "
        self.assertEqual(project_config.target_hosts(project_config.load_config()), ("example.com", "second.example"))

    def test_relative_and_absolute_custom_paths(self):
        self.assertEqual(project_config.get_path({"capture_path": "data/raw.jsonl"}, "capture_path"), self.root / "data/raw.jsonl")
        absolute = self.root / "external" / "raw.jsonl"
        self.assertEqual(project_config.get_path({"capture_path": str(absolute)}, "capture_path"), absolute)

    def test_shell_output_round_trips_without_execution(self):
        marker = self.root / "unexpected-command-ran"
        dangerous = "quotes ' and spaces $(touch " + str(marker) + ") " + chr(96) + "touch " + str(marker) + chr(96) + "\nnext-line"
        self.write_config({"capture_path": dangerous, "run_dir": "space ' run", "log_dir": "space logs",
                           "crown_data_dir": "space crown", "unlisted_secret": "not-for-shell"})
        config = project_config.load_config()
        exported = project_config.shell_config(config)
        script = self.root / "read-env.py"
        script.write_text("import json, os\nprint(json.dumps(dict(os.environ)))\n", encoding="utf-8")
        result = subprocess.run(["/bin/bash", "-c", 'eval "$1"; exec "$2" "$3"', "test", exported, sys.executable, str(script)],
                                env={}, text=True, capture_output=True, check=True)
        exported_env = json.loads(result.stdout)
        for key, config_key in (("CAPTURE_OUTPUT", "capture_path"), ("RUN_DIR", "run_dir"),
                                ("LOG_DIR", "log_dir"), ("CROWN_DATA_DIR", "crown_data_dir")):
            self.assertEqual(exported_env[key], str(project_config.get_path(config, config_key)))
        self.assertNotIn("unlisted_secret", exported)
        self.assertNotIn("not-for-shell", exported)
        self.assertFalse(marker.exists())

    def test_missing_optional_default_is_not_reexported_as_explicit_file(self):
        exported = project_config.shell_config(project_config.load_config())
        self.assertIn("export PS3838_CONFIG=''", exported)


if __name__ == "__main__":
    unittest.main()
