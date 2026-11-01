import json
import csv
from pathlib import Path

from project_config import get_path, load_config

CONFIG = load_config()
SOURCE = get_path(CONFIG, "capture_path")
OUTPUT = get_path(CONFIG, "latest_output_file")

DEFAULT_TARGETS = {
    "1639325365": "沃罗涅日火炬 VS 格罗兹尼",
    "1639310378": "切尔诺莫雷 VS 索非亚九月",
    "1639303346": "国际米兰U23 VS 斯卡法蒂斯",
    "1639308068": "阿尔泰 VS 吉达俱乐部",
    "1639311921": "US比斯克拉 VS 阿尔及尔",
    "1639307510": "法马利康 VS UD莱里亚",
    "1639314009": "佩纳菲尔 VS 维萨拉",
    "1639314111": "里奥阿维 VS 里斯本竞技",
    "1639347138": "潘塞莱科斯 VS 尼基沃罗",
    "1639340450": "卡兹玛 VS 阿吉曼",
}

configured_targets = CONFIG.get("targets")
if isinstance(configured_targets, dict) and configured_targets:
    TARGETS = {
        str(key): str(value) for key, value in configured_targets.items()
    }
else:
    TARGETS = DEFAULT_TARGETS

metadata = {}
latest = {}
errors = 0

if not SOURCE.exists():
    raise SystemExit(f"找不到采集文件：{SOURCE}")

def scan_metadata(node, source):
    if not isinstance(node, list):
        return

    if (
        len(node) >= 9
        and str(node[0]) in TARGETS
        and isinstance(node[1], str)
        and isinstance(node[2], str)
        and isinstance(node[8], dict)
    ):
        eid = str(node[0])
        metadata[eid] = {
            "home": node[1],
            "away": node[2],
            "kickoff_ms": node[4],
            "source": source,
        }

    for child in node:
        if isinstance(child, list):
            scan_metadata(child, source)

def scan_updates(node, captured_at):
    if not isinstance(node, list):
        return

    if (
        len(node) >= 13
        and str(node[12]) in TARGETS
        and all(isinstance(node[i], (int, float))
                for i in (0, 1, 2))
    ):
        eid = str(node[12])
        line = node[3]

        key = (
            eid,
            node[0],
            node[1],
            node[2],
            json.dumps(line),
        )

        price = node[5]
        try:
            valid = price is not None and float(price) > 0
        except (ValueError, TypeError):
            valid = False

        latest[key] = {
            "event_id": eid,
            "比赛": TARGETS[eid],
            "采集时间": captured_at,
            "阶段代码": node[0],
            "盘口代码": node[1],
            "选项代码": node[2],
            "盘口线": line,
            "美式赔率": node[4],
            "原始赔率值": price,
            "报价状态": "有报价" if valid else "空值或暂停",
        }

    for child in node:
        if isinstance(child, list):
            scan_updates(child, captured_at)

with SOURCE.open(encoding="utf-8") as f:
    for line in f:
        try:
            record = json.loads(line)
            msg = record.get("payload", {})
            if isinstance(msg, str):
                msg = json.loads(msg)
        except (json.JSONDecodeError, TypeError):
            errors += 1
            continue

        if not isinstance(msg, dict):
            continue
        if msg.get("type") != "UPDATE_ODDS":
            continue

        odds = msg.get("odds") or {}

        for source in ("l", "n"):
            scan_metadata(odds.get(source) or [], source)

        scan_updates(
            odds.get("u") or [],
            record.get("captured_at", ""),
        )

fields = [
    "event_id", "比赛", "采集时间", "阶段代码",
    "盘口代码", "选项代码", "盘口线",
    "美式赔率", "原始赔率值", "报价状态",
]

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
with OUTPUT.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(sorted(
        latest.values(),
        key=lambda x: (
            x["event_id"],
            x["盘口代码"],
            x["阶段代码"],
            x["盘口线"] is None,
            str(x["盘口线"]),
            x["选项代码"],
        ),
    ))

print("========== 导出完成 ==========")
print("采集文件：", SOURCE)
print("导出文件：", OUTPUT)
print("找到的比赛元数据：", len(metadata), "/", len(TARGETS))
print("归并后的盘口记录：", len(latest))

for eid, name in TARGETS.items():
    rows = [r for r in latest.values() if r["event_id"] == eid]
    valid = sum(r["报价状态"] == "有报价" for r in rows)
    print(f"{eid} | {name} | 当前保留盘口 {len(rows)} 条 | "
          f"有报价 {valid} 条")

print("JSON解析错误：", errors)
