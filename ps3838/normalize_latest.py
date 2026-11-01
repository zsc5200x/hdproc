import csv
from pathlib import Path
from collections import Counter

from odds_utils import normalize_odds
from project_config import get_path, load_config

CONFIG = load_config()
SOURCE = get_path(CONFIG, "latest_output_file")
OUTPUT = get_path(CONFIG, "normalized_output_file")

fields_added = [
    "odds_format",
    "decimal_odds",
    "hong_kong_odds",
    "american_odds_source",
]

counts = Counter()
rows = []

if not SOURCE.exists():
    raise SystemExit(f"找不到待标准化文件：{SOURCE}")

with SOURCE.open(encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)

    for original in reader:
        row = dict(original)
        market = str(row.get("盘口代码", "")).strip()
        raw_text = str(row.get("原始赔率值", "")).strip()
        american = str(row.get("美式赔率", "")).strip()

        row["odds_format"] = ""
        row["decimal_odds"] = ""
        row["hong_kong_odds"] = ""
        row["american_odds_source"] = american

        normalized = normalize_odds(market, raw_text)
        row["odds_format"] = normalized["odds_format"]
        row["decimal_odds"] = normalized["decimal_odds"]
        row["hong_kong_odds"] = normalized["hong_kong_odds"]

        if normalized["报价状态"] == "有报价":
            counts[f"盘口{market}_成功"] += 1
        elif not raw_text or raw_text.lower() in ("none", "null"):
            counts[f"盘口{market}_空赔率"] += 1
        else:
            try:
                float(raw_text)
            except ValueError:
                counts[f"盘口{market}_无法解析"] += 1
            else:
                counts[
                    f"未知盘口代码_{market}"
                    if market not in ("1", "2", "3")
                    else f"盘口{market}_无效赔率"
                ] += 1
        rows.append(row)

fields = list(rows[0].keys()) if rows else []
for field in fields_added:
    if field not in fields:
        fields.append(field)

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
with OUTPUT.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)

print("========== 标准化完成 ==========")
print("输出文件：", OUTPUT)
print("总记录：", len(rows))
for key, count in sorted(counts.items()):
    print(f"{key}: {count}")

print("\n前 8 条核验样本：")
for row in rows[:8]:
    print({
        k: row.get(k)
        for k in (
            "event_id", "盘口代码", "选项代码", "盘口线",
            "原始赔率值", "odds_format",
            "decimal_odds", "hong_kong_odds",
        )
    })
