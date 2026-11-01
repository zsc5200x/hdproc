import json
import os
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch
from urllib.request import ProxyHandler

import capture


class CapturePresenceTests(unittest.TestCase):
    def test_local_dashboard_request_bypasses_global_proxy(self):
        response = Mock()
        response.read.return_value = b'{"ok":true,"removed":0}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response

        with patch("capture.build_opener", return_value=opener) as build_opener:
            result = capture.post_event_presence(
                ["100", "200"],
                "2026-10-28T09:00:00+08:00",
            )

        handler = build_opener.call_args.args[0]
        self.assertIsInstance(handler, ProxyHandler)
        self.assertEqual(handler.proxies, {})
        request = opener.open.call_args.args[0]
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {
                "eventIds": ["100", "200"],
                "observedAt": "2026-10-28T09:00:00+08:00",
                "rowKeysByEvent": {},
                "eventDetailsById": {},
            },
        )
        self.assertTrue(result["ok"])

    @unittest.skipUnless(shutil.which("node"), "需要 Node.js 校验浏览器快照脚本")
    def test_compact_snapshot_keeps_each_handicap_sign(self):
        payload = {
            "l": [
                [
                    29,
                    "Soccer",
                    [
                        [
                            1,
                            "Test League",
                            [
                                [
                                    100,
                                    "Home",
                                    "Away",
                                    0,
                                    1793143800000,
                                    None,
                                    None,
                                    None,
                                    {
                                        "0": [
                                            [
                                                [0.25, -0.25, "0-0.5"],
                                                [0, 0, "0.0"],
                                                [0.75, -0.75, "0.5-1"],
                                            ],
                                            [
                                                ["3.5", 3.5, "1.660"],
                                                ["3.0", 3, "0.787"],
                                            ],
                                            ["", "", None],
                                        ]
                                    },
                                ],
                                [
                                    200,
                                    "Underdog",
                                    "Favourite",
                                    0,
                                    1793143800000,
                                    None,
                                    None,
                                    None,
                                    {
                                        "0": [
                                            [
                                                [-0.5, 0.5, "0.5"],
                                                [0, 0, "0.0"],
                                            ],
                                            [
                                                ["2.5", 2.5, "1.020"],
                                                ["2.5", 2.5, "0.729"],
                                            ],
                                            ["", "", None],
                                        ]
                                    },
                                ]
                            ],
                        ]
                    ],
                ]
            ],
            "n": [],
        }
        node_script = r"""
const fn = (0, eval)('(' + process.env.PRESENCE_SCRIPT + ')');
global.location = { origin: 'https://www.ps3838.com' };
global.fetch = async () => ({
  ok: true,
  status: 200,
  text: async () => process.env.PRESENCE_PAYLOAD,
});
fn().then((result) => process.stdout.write(JSON.stringify(result)));
"""
        env = {
            **os.environ,
            "PRESENCE_SCRIPT": capture.PRESENCE_SCRIPT,
            "PRESENCE_PAYLOAD": json.dumps(payload),
        }
        completed = subprocess.run(
            ["node", "-e", node_script],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        details = result["eventDetailsById"]["100"]
        self.assertEqual(details["sportId"], "29")
        self.assertEqual(details["leagueId"], "1")

        # The compact feed writes the giving side as positive (home -0.25
        # arrives as +0.25); the dashboard row keys use the signed handicap.
        self.assertEqual(
            set(result["rowKeysByEvent"]["100"]),
            {
                "0|2|0|-0.25",
                "0|2|1|0.25",
                "0|2|0|0",
                "0|2|1|0",
                "0|2|0|-0.75",
                "0|2|1|0.75",
                "0|3|3|3.5",
                "0|3|4|3.5",
                "0|3|3|3",
                "0|3|4|3",
                "0|1|0|",
                "0|1|1|",
                "0|1|2|",
            },
        )
        # A home underdog receives goals: the compact pair is negative and the
        # stored home row must be the positive counterpart.
        self.assertEqual(
            set(result["rowKeysByEvent"]["200"]),
            {
                "0|2|0|0.5",
                "0|2|1|-0.5",
                "0|2|0|0",
                "0|2|1|0",
                "0|3|3|2.5",
                "0|3|4|2.5",
                "0|1|0|",
                "0|1|1|",
                "0|1|2|",
            },
        )

    @unittest.skipUnless(shutil.which("node"), "需要 Node.js 校验浏览器快照脚本")
    def test_rendered_event_row_fills_metadata_without_partial_overwrite(self):
        payload = {"l": [[29, "Soccer", []]], "n": []}
        node_script = r"""
const fn = (0, eval)('(' + process.env.PRESENCE_SCRIPT + ')');
global.location = { origin: 'https://www.ps3838.com' };
global.fetch = async () => ({
  ok: true,
  status: 200,
  text: async () => process.env.PRESENCE_PAYLOAD,
});

const leagueBox = {
  classList: { contains: (name) => name === 'league' },
  children: [
    { tagName: 'A', textContent: '' },
    {
      tagName: 'DIV',
      textContent: '您已达到可添加最爱的上限。',
    },
    { tagName: 'SPAN', textContent: '日本 - 女子职业联赛' },
  ],
};

const makeCell = ({ teams, timeText, score, date }) => {
  const row = {
    getAttribute: (name) => name === 'data-score' ? score : null,
    querySelector: (selector) => selector === '.col-time'
      ? { innerText: timeText }
      : selector === '[data-sport-id]'
        ? { getAttribute: () => '29' }
        : null,
  };
  const table = {
    parentElement: { previousElementSibling: leagueBox },
    querySelector: (selector) => selector === '[data-date]'
      ? { getAttribute: () => date }
      : null,
  };
  return {
    getAttribute: (name) => ({
      'data-event': '100',
      'data-home-team': 'Tokyo Verdy Beleza',
      'data-away-team': 'Cerezo Osaka',
      'data-league-id': '10',
      'data-league-name': 'Japan - Women Empowerment League',
    })[name] || null,
    querySelectorAll: (selector) => selector === '.team-name-container .sel'
      ? teams.map((team) => ({
          getAttribute: () => team,
          textContent: team,
        }))
      : [],
    querySelector: (selector) => selector === '[data-sport-id]'
      ? { getAttribute: () => '29' }
      : null,
    closest: (selector) => selector === 'table.events'
      ? table
      : selector === 'tr' ? row : null,
  };
};

global.document = {
  querySelectorAll: () => [
    makeCell({
      teams: ['日视东京绿茵美人队', '大阪樱花'],
      timeText: "1H 5'",
      score: '0-0',
      date: '1793178000000',
    }),
    // PS3838 repeats the event in other market rows with only one team.
    makeCell({
      teams: ['大阪樱花'],
      timeText: '',
      score: '0-0',
      date: '1793178000000',
    }),
  ],
};

fn().then((result) => process.stdout.write(JSON.stringify(result)));
"""
        env = {
            **os.environ,
            "PRESENCE_SCRIPT": capture.PRESENCE_SCRIPT,
            "PRESENCE_PAYLOAD": json.dumps(payload),
        }
        completed = subprocess.run(
            ["node", "-e", node_script],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        details = result["eventDetailsById"]["100"]

        self.assertIn("100", result["eventIds"])
        self.assertEqual(details["home"], "日视东京绿茵美人队")
        self.assertEqual(details["away"], "大阪樱花")
        self.assertEqual(details["name"], "日视东京绿茵美人队 VS 大阪樱花")
        self.assertEqual(details["sportId"], "29")
        self.assertEqual(details["leagueId"], "10")
        self.assertEqual(details["league"], "日本 - 女子职业联赛")
        self.assertEqual(details["kickoff"], 1793178000000)
        self.assertEqual(details["matchMinute"], "5'")
        self.assertEqual(details["matchPeriod"], "1H")
        self.assertNotIn("您已达到", details["league"])


if __name__ == "__main__":
    unittest.main()
