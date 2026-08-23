#!/usr/bin/env python3
"""独立的 BetContext 只读诊断工具。

该工具故意不导入跟单引擎，也不调用 ``FT_bet``。它复用会员端相同的
``transform.php`` Session 形态，按 A-E 顺序观察请求前后的 Cookie、响应头
和服务端返回字段，结果写入本地 JSON 诊断样本。

E 不是一个独立的 ``p=confirm`` 接口：当前站点协议的 confirm 实现就是对
同一个 ``FT_order_view`` 再发一次请求，所以这里明确标记为重复确认探针。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import warnings
import xml.etree.ElementTree as ET
from http.cookies import SimpleCookie
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

warnings.filterwarnings("ignore", message=r"urllib3 v2 only supports OpenSSL.*")
import requests

try:
    import urllib3  # type: ignore

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app"
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))


FORBIDDEN_PROCS = {"ft_bet", "bet"}
SENSITIVE_FORM_KEYS = {"uid", "cookie", "password", "proxy", "token", "blackbox"}
TOKEN_NAME_RE = re.compile(r"token|csrf|session|context|select|bet|ticket|nonce|sign|hash|key", re.I)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha(value: Any) -> str:
    return hashlib.sha256(_text(value).encode("utf-8", "replace")).hexdigest()


def _mask(value: Any, keep: int = 3) -> str:
    text = _text(value)
    if not text:
        return ""
    if len(text) <= keep * 2:
        return "*" * len(text)
    return f"{text[:keep]}...{text[-keep:]}"


def _parse_cookie_header(value: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in _text(value).split(";"):
        if "=" not in item:
            continue
        key, val = item.split("=", 1)
        key = key.strip()
        if key:
            out[key] = val.strip()
    return out


def _cookie_snapshot(session: requests.Session) -> Dict[str, Any]:
    rows = []
    for cookie in session.cookies:
        rows.append(
            {
                "name": str(cookie.name),
                "value_hash": _sha(cookie.value),
                "domain": str(cookie.domain or ""),
                "path": str(cookie.path or ""),
                "secure": bool(cookie.secure),
                "expires": cookie.expires,
            }
        )
    rows.sort(key=lambda row: (row["domain"], row["path"], row["name"]))
    return {
        "count": len(rows),
        "names": [row["name"] for row in rows],
        "cookies": rows,
        "jar_fingerprint": _sha(json.dumps(rows, sort_keys=True, ensure_ascii=False)),
    }


def _header_snapshot(headers: requests.structures.CaseInsensitiveDict) -> Dict[str, Any]:
    # 不记录 Cookie/Authorization 的值；Session Cookie 单独由 cookie_snapshot 记录哈希。
    out: Dict[str, Any] = {}
    for key, value in headers.items():
        low = str(key).lower()
        if low in {"cookie", "authorization", "proxy-authorization"}:
            out[str(key)] = {"redacted": True, "value_hash": _sha(value)}
        else:
            out[str(key)] = str(value)
    return out


def _set_cookie_snapshot(response: requests.Response) -> Dict[str, Any]:
    raw = response.headers.get("Set-Cookie") or ""
    parsed: List[Dict[str, Any]] = []
    if raw:
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:
            cookie = SimpleCookie()
        for morsel in cookie.values():
            parsed.append(
                {
                    "name": morsel.key,
                    "value_hash": _sha(morsel.value),
                    "attrs": {
                        key: morsel[key]
                        for key in ("domain", "path", "expires", "max-age", "secure", "httponly", "samesite")
                        if morsel[key]
                    },
                }
            )
    return {
        "present": bool(raw),
        "names": [row["name"] for row in parsed],
        "cookies": parsed,
        "raw_header_hash": _sha(raw) if raw else "",
    }


def _xml_fields(body: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]:
    """返回扁平字段、完整叶节点和疑似隐藏/token 字段名。"""
    text = _text(body)
    if not text.startswith("<"):
        return {}, [], []
    try:
        root = ET.fromstring(text)
    except Exception:
        return {}, [], []

    flat: Dict[str, Any] = {}
    leaves: List[Dict[str, Any]] = []
    hidden: List[str] = []

    def walk(node: ET.Element, path: str) -> None:
        tag = str(node.tag or "")
        current_path = f"{path}/{tag}" if path else tag
        attrs = {str(k): str(v) for k, v in node.attrib.items()}
        if attrs:
            leaves.append({"path": current_path, "tag": tag, "text": _text(node.text), "attrs": attrs})
            for key in attrs:
                if TOKEN_NAME_RE.search(key):
                    hidden.append(key)
        children = list(node)
        value = _text(node.text)
        if not children and (value or attrs):
            leaves.append({"path": current_path, "tag": tag, "text": value, "attrs": attrs})
            flat[tag] = value
            if TOKEN_NAME_RE.search(tag):
                hidden.append(tag)
        for child in children:
            walk(child, current_path)

    walk(root, "")
    return flat, leaves, sorted(set(hidden))


def _html_diagnostics(body: str) -> Dict[str, Any]:
    """提取 HTML 中可能承担上下文作用的名称，只保存值的哈希，不保存值。"""
    text = _text(body)
    if not text or not re.search(r"<!doctype|<html|<body|<script", text, re.I):
        return {"candidate_names": [], "hidden_inputs": [], "script_assignments": []}

    candidate_names = sorted(
        set(
            re.findall(
                r"(?i)\b(?:csrf|xsrf|token|context(?:_?id)?|session(?:_?id)?|nonce|select(?:ion)?(?:_?id)?|betKey|ticket(?:_?id)?|signature|sign)\b",
                text,
            )
        )
    )
    hidden_inputs: List[Dict[str, Any]] = []
    for match in re.finditer(r"(?is)<input\b([^>]*)>", text):
        attrs = match.group(1) or ""
        attr_map: Dict[str, str] = {}
        for item in re.finditer(r"(?is)([\w:-]+)\s*=\s*(['\"])(.*?)\2", attrs):
            attr_map[item.group(1).lower()] = unescape(item.group(3))
        if attr_map.get("type", "").lower() != "hidden" and not TOKEN_NAME_RE.search(attr_map.get("name", "")):
            continue
        hidden_inputs.append(
            {
                "name": attr_map.get("name", ""),
                "id": attr_map.get("id", ""),
                "value_hash": _sha(attr_map.get("value", "")) if "value" in attr_map else "",
                "value_length": len(attr_map.get("value", "")),
            }
        )

    script_assignments: List[Dict[str, Any]] = []
    for match in re.finditer(
        r"(?is)(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*([^;\n]{0,300})",
        text,
    ):
        name = match.group(1)
        expression = match.group(2).strip()
        if not TOKEN_NAME_RE.search(name):
            continue
        script_assignments.append(
            {"name": name, "expression_hash": _sha(expression), "expression_length": len(expression)}
        )
        if len(script_assignments) >= 100:
            break
    return {
        "candidate_names": candidate_names,
        "hidden_inputs": hidden_inputs,
        "script_assignments": script_assignments,
    }


def _response_summary(response: requests.Response) -> Dict[str, Any]:
    body = response.text or ""
    fields, leaves, hidden = _xml_fields(body)
    html_info = _html_diagnostics(body)
    body_limit = 200_000
    return {
        "http_status": int(response.status_code),
        "reason": str(response.reason or ""),
        "content_type": str(response.headers.get("Content-Type") or ""),
        "response_headers": _header_snapshot(response.headers),
        "set_cookie": _set_cookie_snapshot(response),
        "body_sha256": _sha(body),
        "body_length": len(body),
        "body_truncated": len(body) > body_limit,
        "response_body": body[:body_limit],
        "xml_fields": fields,
        "xml_leaf_fields": leaves,
        "hidden_or_token_field_names": hidden,
        "html_diagnostics": html_info,
        "semantic_fields": {
            key: fields.get(key)
            for key in ("gid", "GID", "rtype", "RTYPE", "con", "CON", "ratio", "RATIO", "ioratio", "IORATIO", "code", "errormsg", "msg", "fast_check", "spread", "strong")
            if key in fields
        },
    }


def _safe_form(form: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in form.items():
        if str(key).lower() in SENSITIVE_FORM_KEYS:
            out[str(key)] = {"masked": _mask(value), "value_hash": _sha(value)}
        else:
            out[str(key)] = value
    return out


def _diff_cookie_names(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    def key(row: Dict[str, Any]) -> Tuple[str, str, str]:
        return (str(row.get("domain") or ""), str(row.get("path") or "/"), str(row.get("name") or ""))

    old_rows = {key(row): row for row in before.get("cookies", []) if isinstance(row, dict)}
    new_rows = {key(row): row for row in after.get("cookies", []) if isinstance(row, dict)}
    old = set(old_rows)
    new = set(new_rows)
    added = sorted(new - old)
    removed = sorted(old - new)
    value_changed = sorted(k for k in (old & new) if old_rows[k].get("value_hash") != new_rows[k].get("value_hash"))
    metadata_changed = sorted(
        k
        for k in (old & new)
        if old_rows[k].get("value_hash") == new_rows[k].get("value_hash")
        and old_rows[k].get("expires") != new_rows[k].get("expires")
    )
    return {
        "added_cookie_keys": [list(k) for k in added],
        "removed_cookie_keys": [list(k) for k in removed],
        "changed_value_hash_keys": [list(k) for k in value_changed],
        "changed_metadata_keys": [list(k) for k in metadata_changed],
        "jar_fingerprint_changed": before.get("jar_fingerprint") != after.get("jar_fingerprint"),
    }


def _load_member(data_dir: Path, label: str = "", member_id: str = "") -> Dict[str, Any]:
    doc = json.loads((data_dir / "arsenal.json").read_text(encoding="utf-8"))
    members = doc.get("members") if isinstance(doc, dict) else []
    members = [m for m in members or [] if isinstance(m, dict)]
    wanted_label = _text(label)
    wanted_id = _text(member_id)
    candidates = [
        m
        for m in members
        if _text(m.get("status")).lower() in {"online", "healthy", "ready", "active"}
        and _text(m.get("cookie"))
        and _text(m.get("base_url"))
        and _text(m.get("uid"))
        and _text(m.get("ver"))
        and _text(m.get("role")).lower() != "quote"
        and _text(m.get("usage")).lower() != "read"
    ]
    if wanted_label:
        candidates = [m for m in candidates if _text(m.get("label") or m.get("username")) == wanted_label]
    if wanted_id:
        candidates = [m for m in candidates if _text(m.get("id")) == wanted_id]
    candidates.sort(key=lambda m: float(m.get("last_ok_at") or 0), reverse=True)
    if not candidates:
        available = [
            {
                "label": _text(m.get("label") or m.get("username")),
                "id": _mask(m.get("id"), 4),
                "status": _text(m.get("status")),
                "has_cookie": bool(_text(m.get("cookie"))),
            }
            for m in members
        ]
        raise SystemExit(f"没有找到可用的在线执行会员（候选摘要：{json.dumps(available, ensure_ascii=False)}）")
    return candidates[0]


def _make_session(member: Dict[str, Any]) -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    base = _text(member.get("base_url")).rstrip("/")
    session.headers.update(
        {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "origin": base,
            "referer": base + "/",
            "content-type": "application/x-www-form-urlencoded",
            "x-requested-with": "XMLHttpRequest",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
        }
    )
    proxy = _text(member.get("proxy"))
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    for key, value in _parse_cookie_header(_text(member.get("cookie"))).items():
        session.cookies.set(key, value)
    return session


def _post_readonly(session: requests.Session, member: Dict[str, Any], form: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
    proc = _text(form.get("p")).lower()
    if proc in FORBIDDEN_PROCS:
        raise RuntimeError("安全阻断：诊断工具禁止调用 FT_bet")
    base = _text(member.get("base_url")).rstrip("/")
    ver = _text(member.get("ver"))
    url = f"{base}/transform.php?ver={ver}"
    before = _cookie_snapshot(session)
    started = time.monotonic()
    error: Optional[Dict[str, str]] = None
    response: Optional[requests.Response] = None
    try:
        response = session.post(url, data=form, timeout=(min(5.0, timeout_s), timeout_s), verify=False)
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
    elapsed_ms = round((time.monotonic() - started) * 1000, 3)
    after = _cookie_snapshot(session)
    row: Dict[str, Any] = {
        "request_form": _safe_form(form),
        "request_url": url,
        "request_elapsed_ms": elapsed_ms,
        "cookie_before": before,
        "cookie_after": after,
        "cookie_diff": _diff_cookie_names(before, after),
        "session_headers_after": _header_snapshot(session.headers),
    }
    if error is not None:
        row["error"] = error
    elif response is not None:
        row["response"] = _response_summary(response)
    return row


def run(args: argparse.Namespace) -> Dict[str, Any]:
    data_dir = Path(args.data_dir).resolve()
    member = _load_member(data_dir, label=args.member_label, member_id=args.member_id)
    session = _make_session(member)
    uid = _text(member.get("uid"))
    ver = _text(member.get("ver"))
    base = _text(member.get("base_url")).rstrip("/")
    gid = _text(args.gid)
    gtype = _text(args.gtype).upper() or "FT"
    wtype = _text(args.wtype).upper() or "ROU"
    side = _text(args.chose_team).upper() or "H"
    if not gid:
        raise SystemExit("--gid 不能为空")
    if side not in {"H", "C", "N"}:
        raise SystemExit("--chose-team 必须是 H/C/N")

    baseline = _cookie_snapshot(session)
    result: Dict[str, Any] = {
        "tool": "diagnose_bet_context",
        "tool_version": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "read_only": True,
        "ft_bet_called": False,
        "forbidden_processes": sorted(FORBIDDEN_PROCS),
        "member": {
            "id": _mask(member.get("id"), 4),
            "label": _text(member.get("label") or member.get("username")),
            "status": _text(member.get("status")),
            "base_url": base,
            "uid": {"masked": _mask(uid), "value_hash": _sha(uid)},
            "ver": ver,
            "proxy_configured": bool(_text(member.get("proxy"))),
        },
        "probe": {
            "gid": gid,
            "gtype": gtype,
            "wtype": wtype,
            "chose_team": side,
            "showtype": _text(args.showtype).lower() or "live",
            "sport_rtype": _text(args.sport_rtype).lower() or "r",
        },
        "steps": [],
    }

    # A: 明确记录基线，不产生网络请求。
    result["steps"].append(
        {
            "step": "A",
            "name": "no_call_baseline",
            "network_called": False,
            "cookie_snapshot": baseline,
            "session_headers": _header_snapshot(session.headers),
        }
    )

    common = {"ver": ver, "langx": "zh-cn", "uid": uid}
    steps: List[Tuple[str, str, Dict[str, Any]]] = [
        (
            "B",
            "sport_menu",
            {
                **common,
                "p": "sport_menu",
                "ts": str(int(time.time() * 1000)),
                "gtype": _text(args.gtype).lower() or "ft",
                "showtype": _text(args.showtype).lower() or "live",
                "rtype": _text(args.sport_rtype).lower() or "r",
            },
        ),
        ("C", "order", {**common, "p": "order"}),
        (
            "D",
            "FT_order_view",
            {
                **common,
                "p": "FT_order_view",
                "odd_f_type": "H",
                "gid": gid,
                "gtype": gtype,
                "wtype": wtype,
                "chose_team": side,
            },
        ),
        (
            "E",
            "FT_order_view_confirm_probe",
            {
                **common,
                "p": "FT_order_view",
                "odd_f_type": "H",
                "gid": gid,
                "gtype": gtype,
                "wtype": wtype,
                "chose_team": side,
            },
        ),
    ]
    for code, name, form in steps:
        row = _post_readonly(session, member, form, float(args.timeout))
        row.update({"step": code, "name": name, "network_called": True})
        result["steps"].append(row)

    result["final_cookie_snapshot"] = _cookie_snapshot(session)
    result["final_session_headers"] = _header_snapshot(session.headers)
    result["summary"] = {
        "cookie_names_by_step": {row["step"]: (row.get("cookie_after") or row.get("cookie_snapshot") or {}).get("names", []) for row in result["steps"]},
        "cookie_changes": {row["step"]: row.get("cookie_diff", {}) for row in result["steps"] if row["step"] != "A"},
        "set_cookie_by_step": {row["step"]: ((row.get("response") or {}).get("set_cookie") or {}) for row in result["steps"] if row["step"] != "A"},
        "response_semantic_fields": {row["step"]: ((row.get("response") or {}).get("semantic_fields") or {}) for row in result["steps"] if row["step"] != "A"},
        "hidden_or_token_fields": {row["step"]: ((row.get("response") or {}).get("hidden_or_token_field_names") or []) for row in result["steps"] if row["step"] != "A"},
        "ft_bet_called": False,
    }
    return result


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="只读 BetContext 服务器上下文诊断（绝不调用 FT_bet）")
    parser.add_argument("--data-dir", default=str(APP_DIR / "data"))
    parser.add_argument("--member-label", default="", help="可选：会员 label/username；默认自动选择 online 执行会员")
    parser.add_argument("--member-id", default="", help="可选：会员内部 id")
    parser.add_argument("--gid", default="8994635")
    parser.add_argument("--gtype", default="FT")
    parser.add_argument("--wtype", default="ROU")
    parser.add_argument("--chose-team", default="H")
    parser.add_argument("--showtype", default="live")
    parser.add_argument("--sport-rtype", default="r")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--output", default="")
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = run(args)
    output = Path(args.output) if _text(args.output) else APP_DIR / "data" / "debug" / f"bet_context_diagnostic_{int(time.time() * 1000)}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output.resolve()), "member": result["member"], "probe": result["probe"], "ft_bet_called": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
