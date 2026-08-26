import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from tools import hd_network_telemetry_sampler as sampler  # noqa: E402


LSOF = """COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
MacPacket 3178 u 10u IPv4 0x1 0t0 TCP 192.168.1.195:61206->27.44.142.81:22311 (ESTABLISHED)
MacPacket 3178 u 11u IPv4 0x2 0t0 TCP 198.18.0.4:443->127.0.0.1:51211 (ESTABLISHED)
MacPacket 3178 u 12u IPv4 0x3 0t0 TCP 127.0.0.1:1082 (LISTEN)
MacPacket 3178 u 13u IPv4 0x4 0t0 TCP 192.168.1.195:50000->8.8.8.8:443 (SYN_SENT)
"""


class NetworkSamplerPhase3BTests(unittest.TestCase):
    def test_lsof_parser_extracts_numeric_endpoints_and_states(self):
        with patch.object(sampler.shutil, "which", return_value="/usr/sbin/lsof"), patch.object(
            sampler, "_run", return_value=LSOF
        ):
            rows = sampler._parse_lsof_connections(3178)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["local_port"], 61206)
        self.assertEqual(rows[0]["remote_port"], 22311)
        self.assertEqual(rows[0]["socket_state"], "ESTABLISHED")
        self.assertEqual(rows[1]["remote_scope"], "private")
        self.assertEqual(rows[3]["socket_state"], "SYN_SENT")

    def test_public_ip_is_never_written_raw(self):
        with patch.object(sampler.shutil, "which", return_value="/usr/sbin/lsof"), patch.object(
            sampler, "_run", return_value=LSOF
        ):
            rows = sampler._parse_lsof_connections(3178)
        encoded = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn("27.44.142.81", encoded)
        self.assertNotIn("8.8.8.8", encoded)
        self.assertRegex(rows[0]["remote_peer_hash"], r"^[0-9a-f]{64}$")

    def test_fake_ip_filter_only_keeps_port_443(self):
        rows = [
            {"remote_scope": "fake", "remote_port": 443, "local_port": 51211},
            {"remote_scope": "fake", "remote_port": 80, "local_port": 51212},
            {"remote_scope": "public", "remote_port": 443, "local_port": 61206},
        ]
        filtered = [row for row in rows if row.get("remote_scope") == "fake" and row.get("remote_port") == 443]
        self.assertEqual(filtered, [rows[0]])

    def test_outbound_summary_counts_states_and_leaves_target_unknown(self):
        rows = [
            {"socket_state": "ESTABLISHED"},
            {"socket_state": "ESTABLISHED"},
            {"socket_state": "SYN_SENT"},
        ]
        summary = sampler._connection_summary(rows)
        self.assertEqual(summary["connection_count"], 3)
        self.assertEqual(summary["ESTABLISHED"], 2)
        self.assertEqual(summary["SYN_SENT"], 1)
        self.assertIsNone(summary["target_host_hash"])


if __name__ == "__main__":
    unittest.main()
