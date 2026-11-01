"""当前项目独立的皇冠账号、登录会话和只读赛事运行时。"""
from __future__ import annotations

from datetime import datetime
import json
import os
import sys
from pathlib import Path
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from crown_local.backend.crown_markets import crown_hk_pair, normalize_line
from crown_local.backend.odds import to_hk_water
from event_filters import is_excluded_league


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_config import get_path, load_config

DATA_DIR = get_path(load_config(), "crown_data_dir")
EVENTS_CACHE_PATH = DATA_DIR / "events-cache.json"
EVENTS_CACHE_VERSION = 1
EVENTS_CACHE_MAX_BYTES = 16 * 1024 * 1024
EVENTS_CACHE_MAX_EVENTS = 20000

try:
    from crown_local.backend.auth import AuthService
    from crown_local.backend.crown_member import CrownMemberMonitor
    from crown_local.backend.crown_odds import CrownOddsClient
    from crown_local.backend.storage import Store, normalize_login_url
except ImportError as exc:  # pragma: no cover - shown as a safe UI error
    AuthService = None
    CrownMemberMonitor = None
    CrownOddsClient = None
    Store = None
    IMPORT_ERROR = str(exc)
else:
    IMPORT_ERROR = ""


lock = threading.RLock()
state = {
    "events": [],
    "leagues": [],
    "updated_at": None,
    "status": "未添加账号",
    "error": None,
    "cached": False,
    "source": "当前项目本地皇冠账号",
    "diagnostics": {
        "refresh_count": 0,
        "last_refresh_duration_ms": None,
        "last_list_duration_ms": None,
        "last_snapshot_interval_ms": None,
        "details_refreshing": False,
        "last_details_duration_ms": None,
        "consecutive_failures": 0,
        "last_success_at": None,
        "last_failure_at": None,
        "last_failure_reason": None,
        "refresh_in_progress": False,
    },
}
store = None
auth = None
monitor = None

# ``get_game_more`` is a second read-only request per fixture. Keep bounded
# per-fixture caches while the base today/live feed refreshes every four seconds.
# Limit detail traffic separately so it does not crowd the base feed.
_MORE_CACHE_TTL = 30.0
_MORE_LIVE_CACHE_TTL = 10.0
_MORE_MAX_WORKERS = 1
_MORE_BATCH_SIZE = 6
_MORE_LIVE_REFRESH_LIMIT = 2
_more_cache_lock = threading.RLock()
_more_cache = {}
_more_detail_locks_lock = threading.Lock()
_more_detail_locks = {}
_more_sources_lock = threading.RLock()
_more_sources = {"client": None, "raw_format": None, "games": {}}
_more_refresh_lock = threading.Lock()
_refresh_lock = threading.Lock()
_last_snapshot_monotonic = None
_more_refresh_thread = None


def extra_markets_enabled():
    """Read the shared collection switch at request time; fail closed on bad config."""
    try:
        return load_config().get("crown_extra_markets_enabled", True) is True
    except Exception:
        return False


def _configure_browser():
    os.environ["CROWN_CDP_URL"] = str(load_config()["crown_cdp_url"])
    # Use the user's installed browser when Playwright's bundled Chromium is
    # not present. This setting is local to this process.
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if chrome.exists():
        os.environ.setdefault("HD_REPORT_BROWSER_EXECUTABLE", str(chrome))


def _cache_load_failed():
    with lock:
        diagnostics = dict(state.get("diagnostics") or {})
        diagnostics["cache_load_failed"] = True
        state["diagnostics"] = diagnostics


def _load_event_cache():
    """Load only normalized, credential-free events from the last snapshot."""
    if not EVENTS_CACHE_PATH.is_file() or EVENTS_CACHE_PATH.is_symlink():
        return False
    try:
        if EVENTS_CACHE_PATH.stat().st_size > EVENTS_CACHE_MAX_BYTES:
            raise ValueError("cache too large")
        document = json.loads(EVENTS_CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("version") != EVENTS_CACHE_VERSION:
            raise ValueError("unsupported cache version")
        updated_at = document.get("updated_at")
        events = document.get("events")
        if not isinstance(updated_at, str) or not updated_at or len(updated_at) > 64:
            raise ValueError("invalid cache timestamp")
        datetime.fromisoformat(updated_at)
        if not isinstance(events, list) or len(events) > EVENTS_CACHE_MAX_EVENTS:
            raise ValueError("invalid cache events")
        normalized = []
        for event in events:
            if (not isinstance(event, dict)
                    or not isinstance(event.get("id"), str) or not event["id"]
                    or not isinstance(event.get("league"), str)
                    or not isinstance(event.get("home"), str)
                    or not isinstance(event.get("away"), str)
                    or not isinstance(event.get("markets"), dict)):
                raise ValueError("invalid cached event")
            normalized.append(event)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        _cache_load_failed()
        return False

    leagues = sorted({event["league"] for event in normalized if event["league"]})
    with lock:
        diagnostics = dict(state.get("diagnostics") or {})
        diagnostics.update({
            "cache_loaded": True,
            "cache_updated_at": updated_at,
            "last_success_at": updated_at,
        })
        state.update({
            "events": normalized,
            "leagues": leagues,
            "updated_at": updated_at,
            "status": "缓存数据",
            "error": None,
            "cached": True,
            "diagnostics": diagnostics,
        })
    return True


def _write_event_cache(events, updated_at):
    """Atomically persist a bounded normalized snapshot with owner-only access."""
    document = {
        "version": EVENTS_CACHE_VERSION,
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "updated_at": updated_at,
        "events": events,
    }
    encoded = json.dumps(
        document, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > EVENTS_CACHE_MAX_BYTES or len(events) > EVENTS_CACHE_MAX_EVENTS:
        raise ValueError("snapshot exceeds cache limit")
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".events-cache-", suffix=".tmp", dir=str(DATA_DIR)
    )
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, EVENTS_CACHE_PATH)
        os.chmod(EVENTS_CACHE_PATH, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _persist_event_cache(events, updated_at):
    # Unit tests inject a fake store. Restrict writes to the initialized local
    # runtime so tests can never overwrite the operator's real snapshot.
    if Store is None or store is None or auth is None or not isinstance(store, Store):
        return
    try:
        _write_event_cache(events, updated_at)
    except (OSError, ValueError, TypeError):
        with lock:
            diagnostics = dict(state.get("diagnostics") or {})
            diagnostics["cache_write_failed"] = True
            state["diagnostics"] = diagnostics


def initialize():
    global store, auth, monitor
    if store is not None or auth is not None or monitor is not None:
        return
    if Store is None or AuthService is None or CrownMemberMonitor is None:
        with lock:
            state["status"] = "运行环境未安装依赖"
            state["error"] = "请使用当前项目 .venv 启动"
        return
    _configure_browser()
    store = Store(DATA_DIR, initialize=True)
    _load_event_cache()
    auth = AuthService(store)
    monitor = CrownMemberMonitor(store, auth)
    monitor.start()


def public_accounts():
    initialize()
    if store is None:
        return []
    return [account for account in store.accounts() if account.get("kind") == "crown_odds"]


def add_account(login_url, username):
    initialize()
    if store is None:
        raise RuntimeError("当前项目运行环境未就绪")
    username = username.strip()
    normalized_url, _login_path = normalize_login_url(login_url)
    existing = next((
        account for account in store.accounts()
        if account.get("kind") == "crown_odds"
        and account.get("base_url") == normalized_url
        and account.get("username") == username
    ), None)
    payload = {
        "kind": "crown_odds",
        "base_url": login_url,
        "username": username,
        "enabled": True,
        "role": "primary",
        "login_layer": "",
        "interval": 30,
    }
    return store.save_account(payload, account_id=existing["id"] if existing else None)


def account_action(account_id, action):
    initialize()
    if auth is None or store is None:
        raise RuntimeError("当前项目运行环境未就绪")
    account = store.get_account(account_id)
    if account.get("kind") != "crown_odds":
        raise ValueError("只允许操作皇冠水位账号")
    if action == "login":
        auth.start(account_id)
    elif action == "check":
        auth.test(account_id, background=False)
    elif action == "relogin":
        try:
            auth.cancel(account_id)
        except Exception:
            pass
        store.clear_session(account_id)
        auth.start(account_id)
    elif action == "cancel":
        auth.cancel(account_id)
    elif action == "clear":
        try:
            auth.cancel(account_id)
        except Exception:
            pass
        store.clear_session(account_id)
    elif action == "delete":
        try:
            auth.cancel(account_id)
        except Exception:
            pass
        store.delete_account(account_id)
    else:
        raise ValueError("不支持的账号操作")
    return next((item for item in public_accounts() if item["id"] == account_id), None)

def _safe_reason(error):
    name = error.__class__.__name__
    if name == "MemberSessionConflict":
        return "session_conflict"
    if name == "MemberExpired":
        return "session_expired"
    if name == "MemberNeedsBrowser":
        return "session_unverified"
    if name == "OddsUnavailable":
        return getattr(error, "reason", "upstream_unavailable")
    if name == "MemberUnavailable":
        return "upstream_unavailable"
    return "crown_unavailable"


def _format_line(value):
    normalized = normalize_line(value)
    return format(normalized.normalize(), "f") if normalized is not None else ""


def _more_pair(row, left_key, right_key, raw_format):
    left_raw = _price(row.get(left_key))
    right_raw = _price(row.get(right_key))
    if not left_raw or not right_raw:
        return None
    if raw_format == "CROWN_HK":
        pair = crown_hk_pair(left_raw, right_raw)
        if not pair:
            return None
        left, right = (_format_hk(float(pair[0])), _format_hk(float(pair[1])))
    else:
        left = _format_hk(to_hk_water(left_raw, raw_format))
        right = _format_hk(to_hk_water(right_raw, raw_format))
    if not left or not right:
        return None
    return {"a": left, "b": right, "raw_a": left_raw, "raw_b": right_raw}


def _more_ids(row):
    gid = str(row.get("GID") or row.get("ID") or "")
    gidm = str(row.get("GIDM") or "")
    hgid = str(row.get("HGID") or "")
    return {
        "gid": gid,
        "gidm": gidm,
        "hgid": hgid,
        "market_id": gidm or gid,
        "master": str(row.get("MASTER") or ""),
    }


def _normalize_more_markets(rows, raw_format, mode="today", strong=""):
    """Normalize every non-zero full-time line returned by ``game_more``.

    Crown uses a compact field catalogue rather than one XML node per market.
    The ``RATIO`` fields identify lines and the matching ``IOR`` fields carry
    the two water values. We preserve the row IDs so the UI can show all
    available alternate lines without pretending that a shared ``GIDM`` is
    unique to one selection. ``strong`` is the owning game's giving side so
    alternate handicap lines keep the same direction as the main line.
    """
    result = {
        "handicap": [], "total": [], "half_handicap": [], "half_total": [],
        "moneyline": [], "rows": [],
    }
    live = str(mode or "").lower() == "live"
    seen = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        ids = _more_ids(row)
        result["rows"].append({**ids, "ptype_id": str(row.get("PTYPE_ID") or "")})
        if str(row.get("MODE") or "FT").upper() not in {"FT", "HT"} or str(row.get("PTYPE") or ""):
            continue
        token_values = {
            str(key)[6:]: value for key, value in row.items()
            if str(key).startswith("RATIO") and value not in (None, "", "0.0", "0.000")
        }
        # ``game_more`` contains many other RATIO_* fields for team totals,
        # corners and cards.  Only these ordinary full-time football markets
        # belong in the comparison table; accepting every suffix would leak
        # those side markets into the handicap/total columns.
        market_specs = {
            "": ("handicap", "IOR_RH", "IOR_RC", "R"),
            "R": ("handicap", "IOR_RH", "IOR_RC", "R"),
            "RE": ("handicap", "IOR_REH", "IOR_REC", "RE"),
            "O": ("total", "IOR_OUC", "IOR_OUH", "O"),
            "ROUO": ("total", "IOR_ROUC", "IOR_ROUH", "ROU"),
            "HR": ("half_handicap", "IOR_HRH", "IOR_HRC", "HR"),
            "HRE": ("half_handicap", "IOR_HREH", "IOR_HREC", "HRE"),
            "HOUO": ("half_total", "IOR_HOUC", "IOR_HOUH", "HOU"),
            "HROUO": ("half_total", "IOR_HROUC", "IOR_HROUH", "HROU"),
        }
        for token, raw_line in token_values.items():
            spec = market_specs.get(token)
            if spec is None:
                # ``U`` is the second name for the same O/U line.  All other
                # suffixes are non-standard side markets for this view.
                continue
            kind, left_key, right_key, market_key = spec
            line = _format_line(raw_line)
            if not line:
                continue
            if kind in {"handicap", "half_handicap"} and strong:
                line = _orient_handicap_text(line, strong=strong)
            pair = _more_pair(row, left_key, right_key, raw_format)
            if not pair:
                continue
            key = (ids["gid"], kind, market_key, line, pair["a"], pair["b"])
            if key in seen:
                continue
            seen.add(key)
            item = {
                **ids,
                **pair,
                "market_type": kind,
                "market_key": market_key,
                "period": "1H" if kind in {"half_handicap", "half_total"} else "FT",
                "line": line,
                "raw_line": line if kind in {"handicap", "half_handicap"} and strong else str(raw_line),
                "line_source": f"RATIO_{token}" if token else "RATIO",
                "odds_source": {"a": left_key, "b": right_key},
                "odds_format": raw_format,
            }
            if kind in {"total", "half_total"}:
                item.update({"over": pair["a"], "under": pair["b"]})
            else:
                item.update({"home": pair["a"], "away": pair["b"]})
            result[kind].append(item)

        moneyline = _moneyline_fields(row, live=live)
        if any(moneyline.values()):
            values = _moneyline_values(moneyline)
            result["moneyline"].append({
                **ids,
                "home": values[0],
                "draw": values[1],
                "away": values[2],
                "market_type": "moneyline",
                "odds_format": raw_format,
            })
    return result


def _more_detail_lock(gid):
    with _more_detail_locks_lock:
        return _more_detail_locks.setdefault(gid, threading.Lock())


def _cached_more_markets(client, game, mode, raw_format, force=False):
    gid = str(game.get("GID") or game.get("ID") or "")
    if not force and not extra_markets_enabled():
        return {"status": "disabled", "handicap": [], "total": [], "half_handicap": [], "half_total": [], "moneyline": [], "rows": []}
    if not gid or not callable(getattr(client, "game_more", None)):
        return {"status": "not_requested", "handicap": [], "total": [], "half_handicap": [], "half_total": [], "moneyline": [], "rows": []}
    with _more_detail_lock(gid):
        if not force and not extra_markets_enabled():
            return {"status": "disabled", "handicap": [], "total": [], "half_handicap": [], "half_total": [], "moneyline": [], "rows": []}
        now = time.monotonic()
        ttl = _MORE_LIVE_CACHE_TTL if mode == "live" else _MORE_CACHE_TTL
        cache_key = (gid, mode, raw_format)
        with _more_cache_lock:
            cached = _more_cache.get(cache_key)
            if cached and now - cached["created_at"] < ttl:
                return cached["value"]
        if not force and not extra_markets_enabled():
            return {"status": "disabled", "handicap": [], "total": [], "half_handicap": [], "half_total": [], "moneyline": [], "rows": []}
        try:
            payload = client.game_more(game, mode)
            value = _normalize_more_markets(
                payload.get("games", []),
                raw_format,
                mode=mode,
                strong=str(game.get("STRONG") or game.get("HSTRONG") or ""),
            )
            value["status"] = "loaded"
        except Exception:
            # A transient game_more failure must not erase alternate lines that
            # were already shown. Keep the last good value until a retry succeeds.
            if cached and isinstance(cached.get("value"), dict) and cached["value"].get("status") == "loaded":
                return cached["value"]
            value = {"status": "unavailable", "handicap": [], "total": [], "half_handicap": [], "half_total": [], "moneyline": [], "rows": []}
        with _more_cache_lock:
            _more_cache[cache_key] = {"created_at": time.monotonic(), "value": value}
        return value


def _register_more_sources(client, games, raw_format):
    sources = {}
    for game, mode in games:
        gid = str(game.get("GID") or game.get("ID") or "")
        if gid:
            sources[gid] = (game, mode)
    with _more_sources_lock:
        _more_sources.update({
            "client": client,
            "raw_format": raw_format,
            "games": sources,
        })


def _public_more_markets(more_markets):
    more_markets = more_markets if isinstance(more_markets, dict) else {}
    return {
        "status": more_markets.get("status", "not_requested"),
        "handicap": list(more_markets.get("handicap") or []),
        "total": list(more_markets.get("total") or []),
        "half_handicap": list(more_markets.get("half_handicap") or []),
        "half_total": list(more_markets.get("half_total") or []),
        "moneyline": list(more_markets.get("moneyline") or []),
        "rows": list(more_markets.get("rows") or []),
    }


def _publish_event_more_markets(gid, more_markets):
    published = _public_more_markets(more_markets)
    with lock:
        events = list(state.get("events") or [])
        for index, event in enumerate(events):
            if str(event.get("id") or "") != gid:
                continue
            updated = dict(event)
            updated["extra_markets"] = published
            events[index] = updated
            state["events"] = events
            diagnostics = dict(state.get("diagnostics") or {})
            diagnostics["last_event_details_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            diagnostics["last_event_details_gid"] = gid
            state["diagnostics"] = diagnostics
            break
    return published


def refresh_event_more(gid, force=False):
    """Load alternate lines for one selected fixture without overloading Crown.

    ``force`` lets an explicit per-order lookup fetch that one fixture's extra
    lines even while the global background extra-market switch is off.
    """
    gid = str(gid or "").strip()
    if not force and not extra_markets_enabled():
        return {"status": "disabled", "gid": gid, "counts": {"handicap": 0, "total": 0, "moneyline": 0}}
    with _more_sources_lock:
        source = (_more_sources.get("games") or {}).get(gid)
        client = _more_sources.get("client")
        raw_format = _more_sources.get("raw_format")
    if not gid or source is None or client is None:
        raise KeyError("赛事已更新，请稍后重试")

    # Share the background detail lock so an on-demand request never adds a
    # third concurrent Crown request while a scheduled batch is still running.
    if not _more_refresh_lock.acquire(timeout=8):
        return {"status": "loading", "gid": gid}
    try:
        loaded = _load_more_markets(client, [source], raw_format, force=force)
        value = loaded.get(gid) or {
            "status": "unavailable", "handicap": [], "total": [], "moneyline": [], "rows": [],
        }
        if value.get("status") == "loaded":
            value = _publish_event_more_markets(gid, value)
    finally:
        _more_refresh_lock.release()
    return {
        "status": value.get("status"),
        "gid": gid,
        "counts": {
            market_type: len(value.get(market_type) or [])
            for market_type in ("handicap", "total", "half_handicap", "half_total", "moneyline")
        },
    }


def _cached_more_snapshot(game, mode, raw_format):
    gid = str(game.get("GID") or game.get("ID") or "")
    if not gid:
        return None
    with _more_cache_lock:
        cached = _more_cache.get((gid, mode, raw_format))
        return cached["value"] if cached else None


def _more_markets_expired(game, mode, raw_format, now=None):
    gid = str(game.get("GID") or game.get("ID") or "")
    if not gid:
        return False
    current = time.monotonic() if now is None else now
    ttl = _MORE_LIVE_CACHE_TTL if mode == "live" else _MORE_CACHE_TTL
    with _more_cache_lock:
        cached = _more_cache.get((gid, mode, raw_format))
        return not cached or current - cached["created_at"] >= ttl


def _load_more_markets(client, games, raw_format, force=False):
    """Fetch detail rows in bounded parallelism while keeping base events safe."""
    jobs = {}
    for game, mode in games:
        gid = str(game.get("GID") or game.get("ID") or "")
        if gid and gid not in jobs:
            jobs[gid] = (game, mode)
    if not jobs or not callable(getattr(client, "game_more", None)):
        return {}
    result = {}
    worker_local = threading.local()

    def load_one(game, mode):
        worker_client = getattr(worker_local, "client", None)
        if worker_client is None:
            # urllib's opener and CookieJar are stateful.  Give each worker a
            # private client when the real Crown client exposes its session;
            # lightweight test doubles continue to use the supplied object.
            worker_client = client
            if CrownOddsClient is not None and hasattr(client, "account") and hasattr(client, "session"):
                try:
                    worker_client = CrownOddsClient(client.account, client.session, timeout=getattr(client, "timeout", 10))
                except Exception:
                    worker_client = client
            worker_local.client = worker_client
        return _cached_more_markets(worker_client, game, mode, raw_format, force=force)

    with ThreadPoolExecutor(max_workers=_MORE_MAX_WORKERS, thread_name_prefix="crown-more") as pool:
        futures = {
            pool.submit(load_one, game, mode): gid
            for gid, (game, mode) in jobs.items()
        }
        for future in as_completed(futures):
            gid = futures[future]
            try:
                result[gid] = future.result()
            except Exception:
                result[gid] = {"status": "unavailable", "handicap": [], "total": [], "half_handicap": [], "half_total": [], "moneyline": [], "rows": []}
    return result


def _more_cache_entry(game, mode, raw_format=None):
    gid = str(game.get("GID") or game.get("ID") or "")
    if not gid:
        return None
    with _more_cache_lock:
        if raw_format:
            return _more_cache.get((gid, mode, raw_format))
        return next(
            (
                cached for key, cached in _more_cache.items()
                if key[0] == gid and key[1] == mode
            ),
            None,
        )


def _select_more_refresh_batch(expired, raw_format=None):
    # Crown rate-limits game_more aggressively.  Keep the fixed-size batch,
    # but reserve most slots for fixtures that have never been loaded.  The
    # previous "all live first" policy could consume every batch forever and
    # leave ordinary matches permanently without alternate lines.
    def unique(items):
        result = []
        seen = set()
        for game, mode in items:
            gid = str(game.get("GID") or game.get("ID") or "")
            key = (gid, mode)
            if not gid or key in seen:
                continue
            seen.add(key)
            result.append((game, mode))
        return result

    def sort_key(item):
        cached = _more_cache_entry(item[0], item[1], raw_format)
        loaded = bool(
            cached
            and isinstance(cached.get("value"), dict)
            and cached["value"].get("status") == "loaded"
        )
        return (loaded, cached.get("created_at", float("-inf")) if cached else float("-inf"))

    games = unique(expired)
    live = sorted((item for item in games if item[1] == "live"), key=sort_key)
    other = sorted((item for item in games if item[1] != "live"), key=sort_key)

    selected = live[:_MORE_LIVE_REFRESH_LIMIT]
    slots = _MORE_BATCH_SIZE - len(selected)
    selected.extend(other[:slots])

    if len(selected) < _MORE_BATCH_SIZE:
        remaining = live[_MORE_LIVE_REFRESH_LIMIT:] + other[slots:]
        selected.extend(sorted(remaining, key=sort_key)[:_MORE_BATCH_SIZE - len(selected)])
    return selected[:_MORE_BATCH_SIZE]


def _start_more_markets_refresh(client, games, raw_format):
    global _more_refresh_thread
    if not extra_markets_enabled() or not games or not callable(getattr(client, "game_more", None)):
        return False
    now = time.monotonic()
    expired = [
        (game, mode) for game, mode in games
        if _more_markets_expired(game, mode, raw_format, now)
    ]
    if not expired or not _more_refresh_lock.acquire(blocking=False):
        return False
    batch = _select_more_refresh_batch(expired, raw_format)

    with lock:
        diagnostics = dict(state.get("diagnostics") or {})
        diagnostics.update({
            "details_refreshing": True,
            "details_batch_size": len(batch),
            "details_pending": max(0, len(expired) - len(batch)),
        })
        state["diagnostics"] = diagnostics

    def refresh_details():
        started = time.monotonic()
        try:
            _load_more_markets(client, batch, raw_format)
        finally:
            completed = datetime.now().astimezone().isoformat(timespec="seconds")
            duration_ms = round((time.monotonic() - started) * 1000, 1)
            with lock:
                diagnostics = dict(state.get("diagnostics") or {})
                diagnostics.update({
                    "details_refreshing": False,
                    "last_details_duration_ms": duration_ms,
                    "last_details_completed_at": completed,
                })
                state["diagnostics"] = diagnostics
            _more_refresh_lock.release()

    try:
        worker = threading.Thread(
            target=refresh_details,
            name="crown-more-refresh",
            daemon=True,
        )
        _more_refresh_thread = worker
        worker.start()
    except Exception:
        with lock:
            diagnostics = dict(state.get("diagnostics") or {})
            diagnostics["details_refreshing"] = False
            state["diagnostics"] = diagnostics
        _more_refresh_lock.release()
        return False
    return True


def refresh_once():
    if not _refresh_lock.acquire(blocking=False):
        with lock:
            diagnostics = dict(state.get("diagnostics") or {})
            diagnostics["refresh_in_progress"] = True
            state["diagnostics"] = diagnostics
        return {"ok": False, "reason": "refresh_in_progress", "in_progress": True}
    with lock:
        diagnostics = dict(state.get("diagnostics") or {})
        diagnostics["refresh_in_progress"] = True
        state["diagnostics"] = diagnostics
    try:
        return _refresh_once_locked()
    finally:
        with lock:
            diagnostics = dict(state.get("diagnostics") or {})
            diagnostics["refresh_in_progress"] = False
            state["diagnostics"] = diagnostics
        _refresh_lock.release()


def _refresh_once_locked():
    global _last_snapshot_monotonic
    refresh_started = time.monotonic()
    initialize()
    if store is None or CrownOddsClient is None:
        with lock:
            state["events"] = []
            state["leagues"] = []
            state["status"] = "运行环境未安装依赖"
            state["error"] = "请使用当前项目 .venv 启动"
            state["cached"] = False
        return {"ok": False, "reason": "runtime_unavailable"}
    accounts = [
        account for account in store.accounts()
        if account.get("kind") == "crown_odds" and account.get("enabled")
    ]
    account = next((item for item in accounts if item.get("has_session")), None)
    if not account:
        with lock:
            state["events"] = []
            state["leagues"] = []
            state["status"] = "未登录" if accounts else "未添加账号"
            state["error"] = None
            state["cached"] = False
        return {"ok": False, "reason": "no_session"}
    try:
        session = store.load_session(account["id"])
        client = CrownOddsClient(account, session)
        display_format = client.odds_format
        raw_format = client.raw_odds_format
        list_started = time.monotonic()
        today = client.list_games("today")["games"]
        live = client.list_games("live")["games"]
        list_duration_ms = round((time.monotonic() - list_started) * 1000, 1)
        source_games = [(item, "today") for item in today] + [(item, "live") for item in live]
        source_games = [
            (item, mode) for item, mode in source_games
            if not is_excluded_league(item.get("LEAGUE"))
        ]
        details_enabled = extra_markets_enabled()
        _register_more_sources(client, source_games, raw_format)
        more_by_gid = {
            str(item.get("GID") or item.get("ID") or ""): cached
            for item, mode in source_games
            if details_enabled and (cached := _cached_more_snapshot(item, mode, raw_format)) is not None
        }
        events = [
            _normalize_event(
                item, mode, display_format, raw_format,
                more_markets=more_by_gid.get(str(item.get("GID") or item.get("ID") or "")),
            )
            for item, mode in source_games
        ]
        events = list({item["id"]: item for item in events if item["id"]}.values())
        events.sort(key=lambda item: (item["league"].lower(), item["home"].lower()))
        leagues = sorted({item["league"] for item in events})
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        committed = time.monotonic()
        previous_snapshot = _last_snapshot_monotonic
        _last_snapshot_monotonic = committed
        with lock:
            diagnostics = dict(state.get("diagnostics") or {})
            diagnostics.update({
                "refresh_count": int(diagnostics.get("refresh_count") or 0) + 1,
                "last_refresh_duration_ms": round((committed - refresh_started) * 1000, 1),
                "last_list_duration_ms": list_duration_ms,
                "last_snapshot_interval_ms": (
                    round((committed - previous_snapshot) * 1000, 1)
                    if previous_snapshot is not None else None
                ),
                "consecutive_failures": 0,
                "last_success_at": timestamp,
            })
            state.update({
                "events": events,
                "leagues": leagues,
                "updated_at": timestamp,
                "status": "已连接",
                "error": None,
                "cached": False,
                "diagnostics": diagnostics,
            })
        _persist_event_cache(events, timestamp)
        if details_enabled:
            _start_more_markets_refresh(client, source_games, raw_format)
        return {"ok": True, "events": events, "leagues": leagues}
    except Exception as exc:
        reason = _safe_reason(exc)
        failed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with lock:
            diagnostics = dict(state.get("diagnostics") or {})
            diagnostics.update({
                "consecutive_failures": int(diagnostics.get("consecutive_failures") or 0) + 1,
                "last_failure_at": failed_at,
                "last_failure_reason": reason,
                "last_refresh_duration_ms": round((time.monotonic() - refresh_started) * 1000, 1),
            })
            has_previous_events = bool(state.get("events"))
            if reason in {"session_expired", "session_unverified", "session_conflict"}:
                status = "需要重新登录"
            elif has_previous_events:
                status = "数据延迟"
            else:
                status = "暂不可用"
            state.update({
                "status": status,
                "error": reason,
                "cached": has_previous_events,
                "diagnostics": diagnostics,
            })
        # A saved session can be rejected after the user has already logged
        # into a rotating Crown member tab. Queue an immediate shared-tab
        # scan instead of waiting for the monitor's long retry window.
        if reason == "session_conflict" and getattr(auth, "shared_browser_configured", False):
            try:
                auth.test(account["id"], background=True)
            except (ValueError, RuntimeError, KeyError):
                pass
        return {"ok": False, "reason": reason}


def _price(value):
    text = str(value or "").strip()
    return text if text and text not in {"0", "0.000"} else ""


def _moneyline_fields(row, *, live=False):
    """Read Crown's three-way prices for both pre-match and live feeds.

    The live feed uses the ``IOR_RM*`` field names while the pre-match feed
    uses ``IOR_M*``. Some responses contain only one form, so each value falls
    back independently and zero values never mask a valid alternate field.
    """
    row = row if isinstance(row, dict) else {}
    primary = ("IOR_RMH", "IOR_RMN", "IOR_RMC") if live else ("IOR_MH", "IOR_MN", "IOR_MC")
    fallback = ("IOR_MH", "IOR_MN", "IOR_MC") if live else ("IOR_RMH", "IOR_RMN", "IOR_RMC")
    return {
        name: _price(row.get(primary[index])) or _price(row.get(fallback[index]))
        for index, name in enumerate(("home", "draw", "away"))
    }


def _moneyline_values(fields):
    """Return Crown's 1X2 prices as Hong Kong water.

    Crown quotes three-way prices in decimal (EU) even when the member account
    displays HK water for handicap/total. Convert to HK so the merged homepage
    compares like for like; values that are already water (<= 1) are kept.
    """
    values = []
    for name in ("home", "draw", "away"):
        raw = fields.get(name) or ""
        converted = to_hk_water(raw, "EU") if raw else None
        values.append(_format_hk(converted) if converted is not None else _price(raw))
    return values


def _orient_handicap_text(raw, *, strong="", moneyline_fields=None):
    """Prefix a handicap magnitude with the giving side's sign.

    Crown's ``RATIO_*`` fields carry only the magnitude; ``STRONG`` names the
    side that gives goals (H = home, C = away). The dashboard and Pinnacle rows
    use the signed convention (giving is negative, receiving positive), so the
    line must be oriented here instead of assuming the home team always gives.
    Without ``STRONG`` the lower three-way price identifies the favourite.
    """
    text = str(raw or "").strip()
    magnitude = normalize_line(text)
    if magnitude is None or magnitude == 0:
        return text
    strong = str(strong or "").strip().upper()
    home_gives = None
    if strong in {"H", "C"}:
        home_gives = strong == "H"
    elif moneyline_fields:
        try:
            home_price = float(moneyline_fields.get("home") or 0)
            away_price = float(moneyline_fields.get("away") or 0)
        except (TypeError, ValueError):
            home_price = away_price = 0.0
        if home_price > 1 and away_price > 1 and home_price != away_price:
            home_gives = home_price < away_price
    if home_gives is None:
        return text
    return ("-" if home_gives else "+") + text.lstrip("+-")


def _signed_handicap_line(game, *, live, moneyline_fields):
    """Return the home handicap with the sign used by the dashboard rows."""
    raw = str((game.get("RATIO_RE") if live else game.get("RATIO_R")) or "").strip()
    return _orient_handicap_text(
        raw,
        strong=str(game.get("STRONG") or game.get("HSTRONG") or ""),
        moneyline_fields=moneyline_fields,
    )


def _signed_half_handicap_line(game, *, live, moneyline_fields):
    """Return the first-half home handicap with the dashboard sign."""
    raw = str((game.get("RATIO_HRE") if live else game.get("RATIO_HR")) or "").strip()
    return _orient_handicap_text(
        raw,
        strong=str(game.get("STRONG") or game.get("HSTRONG") or ""),
        moneyline_fields=moneyline_fields,
    )


def _market(line, left, right, raw_format="CROWN_HK"):
    raw_left, raw_right = _price(left), _price(right)
    if not raw_left and not raw_right:
        return []
    if raw_format == "CROWN_HK":
        pair = crown_hk_pair(raw_left, raw_right) if raw_left and raw_right else None
        left, right = (_format_hk(float(pair[0])), _format_hk(float(pair[1]))) if pair else ("", "")
    else:
        left = _format_hk(to_hk_water(raw_left, raw_format))
        right = _format_hk(to_hk_water(raw_right, raw_format))
    return [{"line": str(line or ""), "a": left, "b": right,
             "a_hk": left, "b_hk": right,
             "raw_a": raw_left, "raw_b": raw_right,
             "odds_format": raw_format}]


def _format_hk(value):
    return "" if value is None else f"{value:.2f}"


_FINISHED_PERIODS = frozenset({
    "FT", "FULL_TIME", "FULLTIME", "FINISHED", "FINAL", "ENDED", "END",
    "COMPLETE", "COMPLETED",
})


def _match_period(minute):
    """Return the match phase that precedes ``^`` in Crown's ``RETIMESET``.

    Live rows look like ``2H^97:20`` (half time pause: ``MTIME^HT``).  The
    prefix is the real phase.  ``NOW_MODEL`` is a market-model switch instead
    (``FT`` = full-time model, ``HT`` = half-time model) and is ``FT`` for every
    second-half fixture, so it must never be treated as the match period or as
    a finish signal.
    """
    text = str(minute or "").strip().upper().replace(" ", "_").replace("-", "_")
    return text.split("^", 1)[0].strip()


def _normalize_event(game, mode, display_format="HK", raw_format="CROWN_HK", more_markets=None):
    live = mode == "live"
    moneyline_fields = _moneyline_fields(game, live=live)
    handicap_line = _signed_handicap_line(
        game, live=live, moneyline_fields=moneyline_fields
    )
    half_handicap_line = _signed_half_handicap_line(
        game, live=live, moneyline_fields=moneyline_fields
    )
    if live:
        handicap = _market(handicap_line, game.get("IOR_REH"), game.get("IOR_REC"), raw_format)
        # Crown's ordinary total market exposes under on IOR_*H and over on
        # IOR_*C. Keep the public pair in over/under order for display.
        total = _market(game.get("RATIO_ROUO"), game.get("IOR_ROUC"), game.get("IOR_ROUH"), raw_format)
        half_handicap = _market(
            half_handicap_line, game.get("IOR_HREH"), game.get("IOR_HREC"), raw_format
        )
        half_total = _market(
            game.get("RATIO_HROUO"), game.get("IOR_HROUC"), game.get("IOR_HROUH"), raw_format
        )
    else:
        handicap = _market(handicap_line, game.get("IOR_RH"), game.get("IOR_RC"), raw_format)
        total = _market(game.get("RATIO_OUO"), game.get("IOR_OUC"), game.get("IOR_OUH"), raw_format)
        half_handicap = _market(
            half_handicap_line, game.get("IOR_HRH"), game.get("IOR_HRC"), raw_format
        )
        half_total = _market(
            game.get("RATIO_HOUO"), game.get("IOR_HOUC"), game.get("IOR_HOUH"), raw_format
        )
    for item in handicap + total:
        if isinstance(item, dict):
            item["period"] = "FT"
    for item in half_handicap + half_total:
        if isinstance(item, dict):
            item["period"] = "1H"
    # Crown's 1X2 feed is decimal; export HK water like the other markets.
    moneyline = _moneyline_values(moneyline_fields)
    if not any(moneyline):
        moneyline = []
    score = " - ".join(item for item in (str(game.get("SCORE_H") or ""), str(game.get("SCORE_C") or "")) if item)
    more_markets = more_markets or {
        "status": "not_requested", "handicap": [], "total": [],
        "half_handicap": [], "half_total": [], "moneyline": [], "rows": [],
    }

    # Attach Crown row identifiers to the ordinary list markets. The list
    # endpoint has the cleanest display values, while ``game_more`` exposes
    # the row/group IDs for the master line. Normalize lines so quarter-goal
    # strings such as ``1 / 1.5`` align with the more response's ``1.25``.
    # The list line is signed while the more rows carry a magnitude, so the
    # comparison uses absolute values.
    def attach_ids(items, extras):
        masters = [item for item in extras if isinstance(item, dict) and item.get("master") == "Y"]
        for item in items:
            if not isinstance(item, dict):
                continue
            item_line = normalize_line(item.get("line"))
            for master in masters:
                master_line = normalize_line(master.get("line"))
                if item_line is None or master_line is None or abs(item_line) != abs(master_line):
                    continue
                for key in ("gid", "gidm", "hgid", "market_id", "master"):
                    value = master.get(key)
                    if value and not item.get(key):
                        item[key] = value
                break

    attach_ids(handicap, more_markets.get("handicap") or [])
    attach_ids(total, more_markets.get("total") or [])
    attach_ids(half_handicap, more_markets.get("half_handicap") or [])
    attach_ids(half_total, more_markets.get("half_total") or [])
    event_id = str(game.get("GID") or game.get("ID") or "")
    for item in handicap + total + half_handicap + half_total:
        if not isinstance(item, dict) or not event_id:
            continue
        item.setdefault("gid", event_id)
        item.setdefault("market_id", event_id)
    minute = str(game.get("RETIMESET") or "")
    # NOW_MODEL is Crown's market-model switch (FT/HT), not the match period.
    # Using it here marked every second-half fixture as finished, so the page
    # hid those live matches. The RETIMESET prefix is the real phase.
    period = _match_period(minute) if live else ""
    is_finished = period in _FINISHED_PERIODS
    return {
        "id": str(game.get("GID") or game.get("ID") or ""),
        "league_id": str(game.get("LID") or ""),
        "league": str(game.get("LEAGUE") or "未命名联赛"),
        "is_live": live or str(game.get("IS_RB") or "").upper() == "Y",
        "is_finished": is_finished,
        "home": str(game.get("TEAM_H") or ""),
        "away": str(game.get("TEAM_C") or ""),
        "start_label": str(game.get("DATETIME") or ""),
        "score": score,
        "minute": minute,
        "period": period,
        "markets": {
            "handicap": handicap,
            "total": total,
            "half_handicap": half_handicap,
            "half_total": half_total,
            "moneyline": moneyline,
        },
        "extra_markets": _public_more_markets(more_markets),
        "odds_format": display_format,
        "odds_format_label": "皇冠港赔（HK）" if display_format == "HK" else f"皇冠赔率（{display_format}）",
    }


def close():
    global store, auth, monitor, _more_refresh_thread
    worker = _more_refresh_thread
    if worker is not None and worker.ident is not None and worker is not threading.current_thread():
        worker.join()
    _more_refresh_thread = None
    if monitor is not None:
        monitor.close()
    if auth is not None:
        auth.close()
    if store is not None and hasattr(store, "close"):
        store.close()
    auth = None
    monitor = None
    store = None
