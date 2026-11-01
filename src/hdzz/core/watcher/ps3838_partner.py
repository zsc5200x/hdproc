"""代理后台来单同步：复用 PS3838 节点的已登录 Playwright 会话。

节点登录 ps38ag.com 后，本模块直接调用代理后台的 profile / hierarchy /
bet-list 接口，把订单写入 ps3838/var/db/partner_orders.sqlite3，供
“我的赛事”和本地桥接使用，不再依赖外部 Chrome Partner 标签页。
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _ps3838_dir() -> Path:
    configured = os.environ.get("HDZZ_PS3838_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[4] / "ps3838"


PS3838_DIR = _ps3838_dir()
PARTNER_DB_PATH = Path(
    os.environ.get("HDZZ_PS3838_PARTNER_DB")
    or (PS3838_DIR / "var" / "db" / "partner_orders.sqlite3")
)
BUSINESS_TIMEZONE = timezone(timedelta(hours=8))


def business_today() -> date:
    return datetime.now(BUSINESS_TIMEZONE).date()

try:  # The merged ps3838 tree is optional in a bare HD checkout.
    if str(PS3838_DIR) not in sys.path:
        sys.path.insert(0, str(PS3838_DIR))
    import partner_orders as partner_db  # type: ignore
except Exception:  # pragma: no cover - exercised only without ps3838 tree
    partner_db = None  # type: ignore

PROFILE_PATH = "/partner-account/ddd/user-info/login-user/profile"
HIERARCHY_PATH = "/partner-reporting/ddd/report/win-loss-detail"
BET_LIST_PATH = "/partner-reporting/ddd/common/bet-list/v2"
PAGE_SIZE = 50
OPEN_INTERVAL_SECONDS = 3.0
SETTLED_INTERVAL_SECONDS = 300.0
AGENTS_REFRESH_SECONDS = 600.0
SETTLED_WINDOW_DAYS = 7


class PartnerSyncError(RuntimeError):
    pass


def default_db_path() -> Path:
    configured = os.environ.get("HDZZ_PS3838_PARTNER_DB")
    if configured:
        return Path(configured).expanduser()
    try:
        import json
        payload = json.loads((PS3838_DIR / "config.json").read_text(encoding="utf-8"))
        raw = str(payload.get("partner_orders_db_path") or "").strip()
        if raw:
            path = Path(raw).expanduser()
            return path if path.is_absolute() else PS3838_DIR / path
    except Exception:
        pass
    return PARTNER_DB_PATH


def _text(value: Any) -> str:
    return str(value or "").strip()


def _config_agents() -> List[str]:
    try:
        payload = __import__("json").loads((PS3838_DIR / "config.json").read_text(encoding="utf-8"))
        return _agent_list(payload.get("partner_agents"))
    except Exception:
        return []


def _agent_list(value: Any) -> List[str]:
    if isinstance(value, str):
        parts = value.replace("\n", ",").split(",")
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = []
    return [str(item).strip() for item in parts if str(item).strip()]


def _parse_profile(response: Dict[str, Any]) -> Dict[str, Any]:
    if int(response.get("httpStatus") or 0) != 200:
        raise PartnerSyncError(f"后台身份接口 HTTP {response.get('httpStatus')}")
    payload = response.get("json") or {}
    profile = payload.get("profile") if isinstance(payload, dict) else None
    if not isinstance(profile, dict):
        raise PartnerSyncError("后台身份接口缺少 profile")
    user_id = profile.get("userId")
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise PartnerSyncError("后台身份接口缺少有效 userId")
    return dict(profile)


def _parse_hierarchy(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    if int(response.get("httpStatus") or 0) != 200:
        raise PartnerSyncError(f"代理层级接口 HTTP {response.get('httpStatus')}")
    payload = response.get("json") or {}
    body = payload.get("body") if isinstance(payload, dict) else None
    data = (body or {}).get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise PartnerSyncError("代理层级接口缺少 data")
    agents: Dict[str, Dict[str, Any]] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        code = _text(row.get("userCode"))
        user_id = row.get("userId")
        if not code or isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            continue
        agents[code] = {
            "code": code,
            "user_id": user_id,
            "level": _text(row.get("userLevel") or row.get("level")),
            "name": _text(row.get("firstName") or row.get("name")),
        }
    return list(agents.values())


def _bet_list_body(owner_user_id: int, agent: Dict[str, Any], status: str,
                   page: int, page_size: int,
                   from_date: Optional[str] = None,
                   to_date: Optional[str] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "betListType": "DETAIL",
        "ownerUserId": str(owner_user_id),
        "playerUserId": int(agent["user_id"]),
        "selectedUserCode": _text(agent["code"]),
        "product": "SB",
        "status": status,
        "paging": {"pageNumber": page, "rowsPerPage": page_size},
    }
    if status == "SETTLED":
        body.update({
            "dateBy": "GRADED_DATE",
            "timezone": "+08:00",
            "fromDate": from_date,
            "toDate": to_date,
        })
    return body


class PartnerSync:
    """Poll the partner backend through an already logged-in Ps3838Client."""

    def __init__(self, client: Any, account: Dict[str, Any],
                 db_path: Optional[Path] = None, *, page_size: int = PAGE_SIZE) -> None:
        if partner_db is None:
            raise PartnerSyncError("未找到 ps3838/partner_orders.py，无法同步后台来单")
        self.client = client
        self.account = dict(account)
        self.db_path = Path(db_path or default_db_path())
        self.page_size = max(1, min(200, int(page_size)))
        self.store = None
        self.owner_user_id = 0
        self.agents: List[Dict[str, Any]] = []
        self.configured_agents = _agent_list(account.get("partner_agents"))
        self.next_open = 0.0
        self.next_settled = 0.0
        self.next_agents = 0.0
        self.last_open_at = 0.0
        self.last_settled_at = 0.0
        self.last_error = ""
        self.last_new = 0
        self.last_events = 0

    # -- lifecycle -----------------------------------------------------
    def close(self) -> None:
        if self.store is not None:
            try:
                self.store.close()
            finally:
                self.store = None

    def _ensure_store(self) -> None:
        if self.store is None:
            self.store = partner_db.OrderStore(self.db_path)

    def _profile(self) -> Dict[str, Any]:
        response = self.client.fetch_partner_json(PROFILE_PATH)
        profile = _parse_profile(response)
        self.owner_user_id = int(profile["userId"])
        return profile

    def refresh_agents(self) -> List[Dict[str, Any]]:
        if not self.owner_user_id:
            self._profile()
        today = business_today()
        response = self.client.fetch_partner_json(HIERARCHY_PATH, {
            "timezone": "+08:00",
            "productCodes": ["SB"],
            "fromDate": (today - timedelta(days=90)).isoformat(),
            "toDate": today.isoformat(),
            "ownerUserId": str(self.owner_user_id),
            "drillDownUserId": self.owner_user_id,
            "orderBy": {"colName": "user_code", "sort": "ASC"},
            "excludeTest": False,
            "dateBy": "GRADED_DATE",
            "searchOption": "USERNAME",
            "searchByText": "",
            "searchUserIds": "",
            "requestSearchByUser": {"searchByText": "", "selectedLevels": ["ALL"]},
            "groupType": "OWN_DOWNLINE",
            "groupId": 0,
            "userIds": None,
        })
        discovered = [agent for agent in _parse_hierarchy(response)
                      if int(agent.get("user_id") or 0) != self.owner_user_id]
        if self.configured_agents:
            wanted = set(self.configured_agents)
            agents = [agent for agent in discovered if agent["code"] in wanted]
        else:
            agents = discovered
        if not agents:
            raise PartnerSyncError("代理层级中没有可轮询的下级代理")
        self.agents = agents
        self._ensure_store()
        self.store.save_agents(agents)
        self.next_agents = time.monotonic() + AGENTS_REFRESH_SECONDS
        return agents

    # -- fetching ------------------------------------------------------
    def _fetch_pages(self, agent: Dict[str, Any], status: str,
                     from_date: Optional[str] = None,
                     to_date: Optional[str] = None) -> Tuple[List[Dict[str, Any]], int]:
        rows: List[Dict[str, Any]] = []
        identities = set()
        first_meta = None
        page = 1
        requests = 0
        while True:
            body = _bet_list_body(self.owner_user_id, agent, status, page,
                                  self.page_size, from_date, to_date)
            response = self.client.fetch_partner_json(BET_LIST_PATH, body)
            requests += 1
            valid, meta = partner_db.parse_page(response, page, self.page_size, status)
            if first_meta is not None and meta != first_meta:
                raise PartnerSyncError("翻页期间订单总数发生变化")
            first_meta = meta
            for row in valid:
                identity = partner_db.order_identity(row)
                if identity is None:
                    continue
                if identity in identities:
                    raise PartnerSyncError("分页出现重复订单")
                identities.add(identity)
            rows.extend(valid)
            total, pages, _ = meta
            if page >= max(1, pages):
                break
            if page >= 1000:
                raise PartnerSyncError("分页数超过上限")
            page += 1
        return rows, requests

    def _commit(self, agent: Dict[str, Any], status: str,
                rows: List[Dict[str, Any]], requests: int, started: float) -> Dict[str, Any]:
        self._ensure_store()
        stats = {
            "started_at": partner_db.now_iso(),
            "finished_at": partner_db.now_iso(),
            "elapsed_seconds": max(0.0, time.monotonic() - started),
            "request_count": requests,
        }
        return self.store.commit_snapshot(agent["code"], status, rows, stats)

    def poll_open(self) -> Dict[str, int]:
        if not self.agents:
            self.refresh_agents()
        total_new = total_events = 0
        for agent in self.agents:
            started = time.monotonic()
            try:
                rows, requests = self._fetch_pages(agent, "OPEN")
                result = self._commit(agent, "OPEN", rows, requests, started)
                total_new += int(result.get("new") or 0)
                total_events += int(result.get("events") or 0)
            except Exception as exc:
                self.last_error = f"{agent.get('code')}: {exc}"[:500]
                try:
                    self._ensure_store()
                    self.store.record_failure(agent.get("code") or "", "OPEN", {
                        "started_at": partner_db.now_iso(),
                        "finished_at": partner_db.now_iso(),
                        "elapsed_seconds": time.monotonic() - started,
                        "request_count": 0,
                    }, "partner_sync_error")
                except Exception:
                    pass
        self.last_new = total_new
        self.last_events = total_events
        self.last_open_at = time.time()
        self.next_open = time.monotonic() + OPEN_INTERVAL_SECONDS
        return {"new": total_new, "events": total_events}

    def poll_settled(self) -> Dict[str, int]:
        if not self.agents:
            self.refresh_agents()
        today = business_today()
        from_date = (today - timedelta(days=SETTLED_WINDOW_DAYS)).isoformat()
        to_date = today.isoformat()
        total_new = total_events = 0
        for agent in self.agents:
            started = time.monotonic()
            try:
                rows, requests = self._fetch_pages(agent, "SETTLED", from_date, to_date)
                result = self._commit(agent, "SETTLED", rows, requests, started)
                total_new += int(result.get("new") or 0)
                total_events += int(result.get("events") or 0)
            except Exception as exc:
                self.last_error = f"{agent.get('code')}: {exc}"[:500]
        self.last_settled_at = time.time()
        self.next_settled = time.monotonic() + SETTLED_INTERVAL_SECONDS
        return {"new": total_new, "events": total_events}

    def poll_due(self) -> Optional[Dict[str, Any]]:
        now = time.monotonic()
        if not self.owner_user_id or now >= self.next_agents:
            try:
                self.refresh_agents()
            except Exception:
                # A bad/expired session must not trigger a hierarchy request
                # every half second; retry after the normal discovery window.
                self.next_agents = time.monotonic() + AGENTS_REFRESH_SECONDS
                raise
        result = None
        if now >= self.next_open:
            result = self.poll_open()
        if now >= self.next_settled:
            self.poll_settled()
        return result

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "owner_user_id": self.owner_user_id,
            "agents": [agent["code"] for agent in self.agents],
            "configured_agents": self.configured_agents,
            "last_open_at": self.last_open_at,
            "last_settled_at": self.last_settled_at,
            "last_new": self.last_new,
            "last_events": self.last_events,
            "last_error": self.last_error,
        }
