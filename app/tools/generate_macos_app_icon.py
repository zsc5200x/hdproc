from __future__ import annotations

import argparse
from pathlib import Path

from macos_icon import ensure_app_icon_icns, resolve_icon_icns_path, resolve_icon_svg_path


def main() -> int:
    parser = argparse.ArgumentParser(description="根据 SVG 生成 macOS AppIcon.icns")
    parser.add_argument("--source-root", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args()

    source_root = Path(args.source_root).resolve()
    svg_path = resolve_icon_svg_path(source_root)
    icns_path = resolve_icon_icns_path(source_root)

    built = ensure_app_icon_icns(source_root)
    print(f"[INFO] SVG: {svg_path}")
    print(f"[INFO] ICNS: {icns_path}")
    print(f"[INFO] 已生成图标：{built}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
