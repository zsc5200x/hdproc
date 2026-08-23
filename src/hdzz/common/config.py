from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def read_json(path: Path) -> Dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def atomic_write_json(path: Path, obj: Dict[str, Any]) -> None:
    """
    多进程安全的原子写入（尽量避免写坏 JSON）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    last_err: Optional[Exception] = None
    for _ in range(20):
        try:
            tmp.replace(path)
            return
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err


class JsonFileCache:
    def __init__(self) -> None:
        self._entries: Dict[str, Tuple[Tuple[int, int], Dict[str, Any]]] = {}

    def read_dict(self, path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
        key = str(Path(path).resolve())
        try:
            stat = Path(path).stat()
            sig = (int(stat.st_mtime_ns), int(stat.st_size))
        except Exception:
            self._entries.pop(key, None)
            return copy.deepcopy(default)

        cached = self._entries.get(key)
        if cached and cached[0] == sig:
            return copy.deepcopy(cached[1])

        try:
            obj = json.loads(Path(path).read_text(encoding="utf-8-sig") or "{}")
            if not isinstance(obj, dict):
                obj = copy.deepcopy(default)
        except Exception:
            obj = copy.deepcopy(default)

        self._entries[key] = (sig, obj)
        return copy.deepcopy(obj)
