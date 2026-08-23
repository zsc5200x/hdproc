import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"members": []}
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def parse_labels_arg(labels: str) -> List[str]:
    out: List[str] = []
    for part in (labels or "").split(","):
        p = part.strip()
        if p:
            out.append(p)
    return out


def normalize_base_url(base_url: str) -> str:
    return str(base_url or "").strip().rstrip("/")


def should_patch_member(m: Dict[str, Any], labels: List[str]) -> bool:
    if not labels:
        return True
    label = str(m.get("label") or m.get("username") or "").strip()
    username = str(m.get("username") or "").strip()
    mid = str(m.get("mid") or "").strip()
    for x in labels:
        if x in {label, username, mid}:
            return True
    return False


def patch_member(m: Dict[str, Any], base_url: str, clear_session: bool) -> Tuple[Dict[str, Any], bool]:
    changed = False
    out = dict(m)
    if normalize_base_url(out.get("base_url") or "") != base_url:
        out["base_url"] = base_url
        changed = True
    if clear_session:
        for k in ("uid", "cookie", "mid", "status", "last_error"):
            if out.get(k):
                out[k] = "" if k != "status" else "unknown"
                changed = True
        if out.get("status") != "unknown":
            out["status"] = "unknown"
            changed = True
    return out, changed


def main() -> int:
    p = argparse.ArgumentParser(description="Patch data/arsenal.json member base_url and (optionally) clear session to force relogin.")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--base-url", required=True, help="e.g. https://hga027.com")
    p.add_argument("--labels", default="", help="comma-separated labels/usernames/mids; empty means all members")
    p.add_argument("--clear-session", action="store_true", help="clear uid/cookie/mid to force member_arsenal_watch relogin")
    args = p.parse_args()

    data_dir = Path(args.data_dir)
    doc_path = data_dir / "arsenal.json"
    doc = read_json(doc_path)
    members = doc.get("members")
    if not isinstance(members, list):
        print("arsenal.json 格式不正确：members 不是数组")
        return 2

    base_url = normalize_base_url(args.base_url)
    labels = parse_labels_arg(args.labels)

    changed_any = False
    out_members: List[Dict[str, Any]] = []
    patched = 0
    for m in members:
        if not isinstance(m, dict):
            continue
        if should_patch_member(m, labels):
            nm, changed = patch_member(m, base_url, bool(args.clear_session))
            out_members.append(nm)
            if changed:
                changed_any = True
            patched += 1
        else:
            out_members.append(m)

    doc["members"] = out_members
    if changed_any:
        atomic_write_json(doc_path, doc)

    print(f"patched_members={patched} changed={changed_any} base_url={base_url} clear_session={bool(args.clear_session)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

