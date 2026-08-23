from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Optional, Tuple


APP_ICON_NAME = "AppIcon.icns"
APP_ICON_SVG_NAME = "AppIcon.svg"
ICON_RELATIVE_DIR = Path("app") / "assets" / "icon"


def resolve_icon_dir(source_root: Path) -> Path:
    return (Path(source_root).resolve() / ICON_RELATIVE_DIR).resolve()


def resolve_icon_svg_path(source_root: Path) -> Path:
    return resolve_icon_dir(source_root) / APP_ICON_SVG_NAME


def resolve_icon_icns_path(source_root: Path) -> Path:
    return resolve_icon_dir(source_root) / APP_ICON_NAME


def iconset_entries() -> Iterable[Tuple[str, int]]:
    return (
        ("icon_16x16.png", 16),
        ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32),
        ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512),
        ("icon_512x512@2x.png", 1024),
    )


def _render_svg_to_png(svg_path: Path, out_png: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        subprocess.run(
            ["qlmanage", "-t", "-s", "1024", "-o", str(tmp_dir), str(svg_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        rendered = tmp_dir / f"{svg_path.name}.png"
        if not rendered.is_file():
            raise FileNotFoundError(f"未生成 PNG 预览：{rendered}")
        out_png.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rendered, out_png)


def _render_image_to_png(image_path: Path, out_png: Path) -> None:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["sips", "-s", "format", "png", str(image_path), "--out", str(out_png)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )


def build_icns_from_svg(svg_path: Path, icns_path: Path) -> Path:
    svg_path = Path(svg_path).resolve()
    icns_path = Path(icns_path).resolve()
    if not svg_path.is_file():
        raise FileNotFoundError(f"未找到 SVG 图标源文件：{svg_path}")

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        base_png = tmp_dir / "AppIcon-1024.png"
        iconset_dir = tmp_dir / "AppIcon.iconset"
        iconset_dir.mkdir(parents=True, exist_ok=True)

        _render_svg_to_png(svg_path, base_png)
        for filename, size in iconset_entries():
            out_png = iconset_dir / filename
            subprocess.run(
                ["sips", "-z", str(size), str(size), str(base_png), "--out", str(out_png)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )

        icns_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    return icns_path


def build_icns_from_image(image_path: Path, icns_path: Path) -> Path:
    image_path = Path(image_path).resolve()
    icns_path = Path(icns_path).resolve()
    if not image_path.is_file():
        raise FileNotFoundError(f"未找到图标源文件：{image_path}")

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        base_png = tmp_dir / "AppIcon-1024.png"
        iconset_dir = tmp_dir / "AppIcon.iconset"
        iconset_dir.mkdir(parents=True, exist_ok=True)

        _render_image_to_png(image_path, base_png)
        for filename, size in iconset_entries():
            out_png = iconset_dir / filename
            subprocess.run(
                ["sips", "-z", str(size), str(size), str(base_png), "--out", str(out_png)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )

        icns_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    return icns_path


def build_icns_from_source(icon_source: Path, icns_path: Path) -> Path:
    source = Path(icon_source).resolve()
    suffix = source.suffix.lower()
    if suffix == ".icns":
        icns_path = Path(icns_path).resolve()
        icns_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, icns_path)
        return icns_path
    if suffix == ".svg":
        return build_icns_from_svg(source, icns_path)
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}:
        return build_icns_from_image(source, icns_path)
    raise ValueError(f"不支持的图标格式：{source.suffix}")


def ensure_app_icon_icns(source_root: Path, *, icon_source: Optional[Path] = None, output_path: Optional[Path] = None) -> Path:
    icns_path = Path(output_path).resolve() if output_path is not None else resolve_icon_icns_path(source_root)
    if icon_source is not None:
        return build_icns_from_source(Path(icon_source), icns_path)

    if icns_path.is_file():
        return icns_path.resolve()

    svg_path = resolve_icon_svg_path(source_root)
    return build_icns_from_svg(svg_path, icns_path)
