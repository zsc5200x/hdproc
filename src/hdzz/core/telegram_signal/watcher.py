from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .ingest import MatchResolutionError, build_follow_order, find_recent_confirmed_match, resolve_signal_match
from .parser import SignalParseError, parse_telegram_bet_signal
from .credentials import resolve_telegram_credentials
from .store import (
    append_event,
    load_private_json,
    load_settings,
    read_json,
    remember_message,
    remember_order_id,
    sanitize_event_reason,
    sanitize_secret_status,
    write_private_json,
)


GamesLoader = Callable[[Dict[str, Any]], Iterable[Dict[str, Any]]]

WEBUI_GAMES_CACHE_TTL_S = 10.0
WEBUI_GAMES_REFRESH_INTERVAL_S = 5.0
WEBUI_GAMES_REQUEST_TIMEOUT_S = 6.0
WEBUI_GAMES_RETRY_GUARD_S = 1.0
LOCAL_IDENTITY_MAX_AGE_S = 120.0
TELEGRAM_POLL_INTERVAL_S = 2.0
TELEGRAM_POLL_BATCH_SIZE = 50
_WEBUI_GAMES_CACHE: Dict[Tuple[Tuple[str, ...], Tuple[str, ...]], Dict[str, Any]] = {}
_WEBUI_GAMES_INFLIGHT: Dict[Tuple[Tuple[str, ...], Tuple[str, ...]], threading.Event] = {}
_WEBUI_GAMES_LAST_ATTEMPT: Dict[Tuple[Tuple[str, ...], Tuple[str, ...]], float] = {}
_WEBUI_GAMES_CACHE_LOCK = threading.Lock()


class _TelethonUpdatesLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "likely misusing the session" in message or "matching Constructor ID" in message:
            record.msg = "telegram_protocol_sync_error"
            record.args = ()
        return True


def _install_telethon_updates_log_filter() -> None:
    logger = logging.getLogger("telethon.client.updates")
    if not any(isinstance(item, _TelethonUpdatesLogFilter) for item in logger.filters):
        logger.addFilter(_TelethonUpdatesLogFilter())


def _text(value: Any) -> str:
    return str(value or "").strip()


def _event_summary(status: str, message: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    event = {
        "status": str(status),
        "chat_id": _text(message.get("chat_id")),
        "message_id": _text(message.get("message_id")),
        "chat_title": _text(message.get("chat_title")),
    }
    event.update({key: value for key, value in extra.items() if value not in (None, "")})
    return event


def _append_order(path: Path, order: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(order, ensure_ascii=False, separators=(",", ":")) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _elapsed_ms(started: float) -> float:
    return round(max(0.0, time.perf_counter() - float(started)) * 1000.0, 3)


def _write_listener_health(path: Path, state: str, **extra: Any) -> None:
    payload = load_private_json(Path(path))
    payload.update({key: value for key, value in extra.items() if value not in (None, "")})
    payload.update(
        {
            "pid": os.getpid(),
            "state": _text(state) or "unknown",
            "heartbeat_at_ms": int(time.time() * 1000),
        }
    )
    if payload["state"] == "healthy":
        payload.pop("error", None)
    write_private_json(Path(path), payload)


def _load_message_cursors(path: Path) -> Dict[str, int]:
    payload = read_json(Path(path), {})
    raw = payload.get("chat_message_ids") if isinstance(payload, dict) else {}
    out: Dict[str, int] = {}
    for chat_id, message_id in (raw.items() if isinstance(raw, dict) else []):
        try:
            value = int(message_id)
        except Exception:
            continue
        if _text(chat_id) and value > 0:
            out[_text(chat_id)] = value
    return out


def _save_message_cursors(path: Path, cursors: Dict[str, int]) -> None:
    write_private_json(
        Path(path),
        {
            "chat_message_ids": {
                _text(chat_id): int(message_id)
                for chat_id, message_id in cursors.items()
                if _text(chat_id) and int(message_id) > 0
            },
            "updated_at": int(time.time()),
        },
    )


def _poll_message_window(messages: Iterable[Any], cursor: Optional[int]) -> Tuple[List[Any], int]:
    rows: List[Tuple[int, Any]] = []
    for message in messages:
        try:
            message_id = int(getattr(message, "id", 0) or 0)
        except Exception:
            continue
        if message_id > 0:
            rows.append((message_id, message))
    rows.sort(key=lambda item: item[0])
    newest = rows[-1][0] if rows else int(cursor or 0)
    if cursor is None or int(cursor or 0) <= 0:
        return [], newest
    return [message for message_id, message in rows if message_id > int(cursor)], newest


def _telegram_message_payload(message: Any, *, chat_id: str, chat_title: str) -> Dict[str, Any]:
    received_at_ms = int(time.time() * 1000)
    event_date = getattr(message, "date", None)
    try:
        telegram_sent_at_ms = int(float(event_date.timestamp()) * 1000) if event_date is not None else 0
    except Exception:
        telegram_sent_at_ms = 0
    return {
        "chat_id": _text(chat_id),
        "message_id": _text(getattr(message, "id", "")),
        "chat_title": _text(chat_title),
        "text": _text(getattr(message, "raw_text", "")),
        "received_ts": int(received_at_ms / 1000),
        "received_at_ms": received_at_ms,
        "telegram_sent_at_ms": telegram_sent_at_ms,
    }


async def _resolve_source_entities(client: Any, source_ids: Iterable[int]) -> Dict[str, Any]:
    wanted = {_text(item) for item in source_ids if _text(item)}
    resolved: Dict[str, Any] = {}
    for limit in (200, None):
        dialogs = await client.get_dialogs(limit=limit)
        for dialog in dialogs:
            chat_id = _text(getattr(dialog, "id", ""))
            if chat_id in wanted:
                resolved[chat_id] = getattr(dialog, "entity", dialog)
        if wanted.issubset(resolved):
            return resolved
    missing = sorted(wanted.difference(resolved))
    raise RuntimeError(f"source_chat_unavailable:{','.join(missing)}")


def _processing_timing(
    *,
    message: Dict[str, Any],
    process_started: float,
    process_started_at_ms: int,
    parse_ms: float = 0.0,
    games_load_ms: float = 0.0,
    games_refresh_ms: float = 0.0,
    match_ms: float = 0.0,
    build_ms: float = 0.0,
) -> Dict[str, Any]:
    processed_at_ms = int(time.time() * 1000)
    try:
        received_at_ms = int(message.get("received_at_ms") or int(float(message.get("received_ts") or 0) * 1000))
    except Exception:
        received_at_ms = 0
    try:
        telegram_sent_at_ms = int(message.get("telegram_sent_at_ms") or 0)
    except Exception:
        telegram_sent_at_ms = 0
    out: Dict[str, Any] = {
        "received_at_ms": received_at_ms or process_started_at_ms,
        "processed_at_ms": processed_at_ms,
        "processing_ms": _elapsed_ms(process_started),
        "parse_ms": round(max(0.0, float(parse_ms or 0.0)), 3),
        "games_load_ms": round(max(0.0, float(games_load_ms or 0.0)), 3),
        "match_ms": round(max(0.0, float(match_ms or 0.0)), 3),
        "build_ms": round(max(0.0, float(build_ms or 0.0)), 3),
    }
    if games_refresh_ms > 0:
        out["games_refresh_ms"] = round(float(games_refresh_ms), 3)
    if received_at_ms > 0:
        out["worker_queue_ms"] = max(0, process_started_at_ms - received_at_ms)
    if telegram_sent_at_ms > 0:
        out["telegram_delivery_ms"] = max(0, (received_at_ms or process_started_at_ms) - telegram_sent_at_ms)
    return out


def process_signal_message(
    *,
    settings: Dict[str, Any],
    message: Dict[str, Any],
    games_loader: GamesLoader,
    state_path: Path,
    event_log_path: Path,
    orders_dir: Path,
    games_refresher: Optional[GamesLoader] = None,
) -> Dict[str, Any]:
    process_started = time.perf_counter()
    process_started_at_ms = int(time.time() * 1000)
    parse_ms = 0.0
    games_load_ms = 0.0
    games_refresh_ms = 0.0
    match_ms = 0.0
    build_ms = 0.0
    match_started = 0.0

    def timings() -> Dict[str, Any]:
        return _processing_timing(
            message=message,
            process_started=process_started,
            process_started_at_ms=process_started_at_ms,
            parse_ms=parse_ms,
            games_load_ms=games_load_ms,
            games_refresh_ms=games_refresh_ms,
            match_ms=match_ms,
            build_ms=build_ms,
        )

    if not bool(settings.get("enabled")):
        return {"status": "ignored_disabled"}

    chat_id = _text(message.get("chat_id"))
    allowed = {_text(item) for item in (settings.get("source_chat_ids") or []) if _text(item)}
    if chat_id not in allowed:
        return {"status": "ignored_source"}

    message_id = _text(message.get("message_id"))
    message_key = f"{chat_id}:{message_id}"
    if not remember_message(Path(state_path), message_key):
        return {"status": "duplicate_message"}

    parse_started = time.perf_counter()
    try:
        signal = parse_telegram_bet_signal(message.get("text"))
    except SignalParseError as exc:
        parse_ms = _elapsed_ms(parse_started)
        result = {"status": "rejected_parse", "reason": str(exc)}
        timing = timings()
        result.update(timing)
        append_event(Path(event_log_path), _event_summary("rejected_parse", message, reason=str(exc), **timing))
        return result
    parse_ms = _elapsed_ms(parse_started)

    try:
        games_started = time.perf_counter()
        games = list(games_loader(signal))
        games_load_ms = _elapsed_ms(games_started)
        match_started = time.perf_counter()
        try:
            game = resolve_signal_match(signal, games)
        except MatchResolutionError as exc:
            if str(exc) not in {"number_pair_not_found", "name_match_not_found"}:
                raise
            if games_refresher is not None:
                refresh_started = time.perf_counter()
                refreshed_games = list(games_refresher(signal))
                games_refresh_ms = _elapsed_ms(refresh_started)
                if refreshed_games:
                    games = refreshed_games
                    try:
                        game = resolve_signal_match(signal, games)
                    except MatchResolutionError:
                        game = None
                else:
                    game = None
            else:
                game = None
            if not game:
                game = find_recent_confirmed_match(
                    signal,
                    Path(orders_dir),
                    target_name=_text(settings.get("target_name")) or "telegram_signal",
                    received_ts=int(message.get("received_ts") or time.time()),
                )
            if not game:
                raise
        match_ms = _elapsed_ms(match_started)
        build_started = time.perf_counter()
        order = build_follow_order(
            signal,
            game,
            chat_id=chat_id,
            message_id=message_id,
            chat_title=message.get("chat_title"),
            received_ts=int(message.get("received_ts") or time.time()),
            target_name=_text(settings.get("target_name")) or "telegram_signal",
        )
        build_ms = _elapsed_ms(build_started)
    except (MatchResolutionError, OSError, ValueError, RuntimeError) as exc:
        if match_started > 0 and match_ms <= 0:
            match_ms = _elapsed_ms(match_started)
        result = {"status": "rejected_match", "reason": str(exc)}
        timing = timings()
        result.update(timing)
        append_event(
            Path(event_log_path),
            _event_summary(
                "rejected_match",
                message,
                reason=str(exc),
                order_id=signal.get("order_id"),
                league=signal.get("league"),
                **timing,
            ),
        )
        return result

    order_id = _text(order.get("orderId"))
    if not remember_order_id(Path(state_path), order_id):
        timing = timings()
        append_event(Path(event_log_path), _event_summary("duplicate_order", message, order_id=order_id, **timing))
        return {"status": "duplicate_order", "order_id": order_id, **timing}

    summary = {
        "order_id": order_id,
        "league": _text(order.get("league")),
        "match": _text(order.get("sides")),
        "line": _text(order.get("line")),
        "odds": _text(order.get("odds")),
        "amount": _text(order.get("stake")),
        "gid": _text(order.get("gid")),
        "match_source": _text(game.get("_identity_source")) or "unknown",
    }
    if bool(settings.get("dry_run", True)):
        timing = timings()
        append_event(Path(event_log_path), _event_summary("dry_run", message, **summary, **timing))
        return {"status": "dry_run", "order": order, **timing}

    out_path = Path(orders_dir) / f"{_text(order.get('target')) or 'telegram_signal'}.jsonl"
    _append_order(out_path, order)
    timing = timings()
    append_event(Path(event_log_path), _event_summary("injected", message, **summary, **timing))
    return {"status": "injected", "order": order, "path": str(out_path), **timing}


def _cache_games(path: Path, *, max_age_s: float = LOCAL_IDENTITY_MAX_AGE_S) -> List[Dict[str, Any]]:
    """Read match identity from the local snapshot cache (no odds exposed)."""
    try:
        age_s = max(0.0, time.time() - float(Path(path).stat().st_mtime))
    except OSError:
        return []
    if age_s > max(0.0, float(max_age_s)):
        return []
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return []
    matches = document.get("matches") if isinstance(document, dict) else {}
    if not isinstance(matches, dict):
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for record in matches.values():
        if not isinstance(record, dict):
            continue
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        gid = _text(record.get("gid") or meta.get("masterGid") or meta.get("requestedGid"))
        if not gid or gid in seen:
            continue
        home = _text(meta.get("teamH"))
        away = _text(meta.get("teamC"))
        if not home or not away:
            continue
        seen.add(gid)
        out.append(
            {
                "gid": gid,
                "league": _text(meta.get("league")),
                "teamH": home,
                "teamC": away,
                "gnumH": _text(meta.get("gnumH")),
                "gnumC": _text(meta.get("gnumC")),
                "_identity_source": "snapshot_cache",
                "_identity_age_s": round(age_s, 3),
            }
        )
    return out


def _order_games(
    orders_dir: Path,
    *,
    max_files: int = 128,
    max_lines: int = 3000,
    max_age_s: float = LOCAL_IDENTITY_MAX_AGE_S,
) -> List[Dict[str, Any]]:
    """Use recently stored source orders as a short-lived identity fallback."""
    out: List[Dict[str, Any]] = []
    seen = set()
    now_value = time.time()
    try:
        paths = sorted(Path(orders_dir).glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]
    except OSError:
        return out
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()[-max_lines:]
        except OSError:
            continue
        for line in reversed(lines):
            try:
                order = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(order, dict):
                continue
            try:
                order_ts = float(order.get("ts") or 0.0)
            except Exception:
                order_ts = 0.0
            age_s = max(0.0, now_value - order_ts) if order_ts > 0 else float("inf")
            if order_ts <= 0 or order_ts > now_value + 300.0 or age_s > max(0.0, float(max_age_s)):
                continue
            raw = order.get("raw") if isinstance(order.get("raw"), dict) else {}
            gid = _text(order.get("gid") or raw.get("GID") or raw.get("_EVENT"))
            home = _text(raw.get("TEAM_H") or raw.get("teamH"))
            away = _text(raw.get("TEAM_C") or raw.get("teamC"))
            if not gid or not home or not away or gid in seen:
                continue
            seen.add(gid)
            out.append(
                {
                    "gid": gid,
                    "league": _text(order.get("league") or raw.get("LEAGUE")),
                    "teamH": home,
                    "teamC": away,
                    "gnumH": _text(raw.get("GNUM_H") or raw.get("NUM_H")),
                    "gnumC": _text(raw.get("GNUM_C") or raw.get("NUM_C")),
                    "_identity_source": "recent_order",
                    "_identity_age_s": round(age_s, 3),
                }
            )
    return out


def _dedupe_games(games: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for game in games:
        if not isinstance(game, dict):
            continue
        gid = _text(game.get("gid") or game.get("GID"))
        if not gid or gid in seen:
            continue
        seen.add(gid)
        out.append(game)
    return out


def _webui_games(
    signal: Dict[str, Any],
    base_url: str,
    *,
    force_refresh: bool = False,
    cache_ttl_s: float = WEBUI_GAMES_CACHE_TTL_S,
) -> List[Dict[str, Any]]:
    bases: List[str] = []
    for value in (base_url, os.environ.get("HDZZ_WEBUI_URL"), "http://127.0.0.1:9999", "http://127.0.0.1:2299"):
        base = _text(value).rstrip("/")
        if base and base not in bases:
            bases.append(base)
    modes = ["live"] if _text(signal.get("mode")) == "live" else ["today", "early"]
    cache_key = (tuple(bases), tuple(modes))
    now_value = time.monotonic()
    with _WEBUI_GAMES_CACHE_LOCK:
        cached = dict(_WEBUI_GAMES_CACHE.get(cache_key) or {})
        age = max(0.0, now_value - float(cached.get("stored_at") or 0.0)) if cached else float("inf")
        if cached and not force_refresh and age <= max(0.0, float(cache_ttl_s)):
            return [dict(item) for item in (cached.get("games") or []) if isinstance(item, dict)]
        last_attempt_at = float(_WEBUI_GAMES_LAST_ATTEMPT.get(cache_key) or 0.0)
        last_attempt_age = max(0.0, now_value - last_attempt_at)
        if force_refresh and last_attempt_at > 0 and last_attempt_age <= WEBUI_GAMES_RETRY_GUARD_S:
            return [dict(item) for item in (cached.get("games") or []) if isinstance(item, dict)]
        inflight = _WEBUI_GAMES_INFLIGHT.get(cache_key)
        if inflight is None:
            inflight = threading.Event()
            _WEBUI_GAMES_INFLIGHT[cache_key] = inflight
            owns_refresh = True
        else:
            owns_refresh = False
            if cached and not force_refresh:
                return [dict(item) for item in (cached.get("games") or []) if isinstance(item, dict)]

    if not owns_refresh:
        inflight.wait(timeout=15.0)
        with _WEBUI_GAMES_CACHE_LOCK:
            refreshed = dict(_WEBUI_GAMES_CACHE.get(cache_key) or {})
        source = refreshed or cached
        return [dict(item) for item in (source.get("games") or []) if isinstance(item, dict)]

    out: List[Dict[str, Any]] = []
    seen = set()
    refreshed_ok = False
    try:
        for mode in modes:
            query = urlencode({"mode": mode})
            for base in bases:
                try:
                    request = Request(
                        f"{base}/api/telegram-signal/games?{query}",
                        headers={"Accept": "application/json"},
                    )
                    with urlopen(
                        request,
                        timeout=WEBUI_GAMES_REQUEST_TIMEOUT_S,
                    ) as response:  # nosec B310 - settings only permits loopback URLs
                        payload = json.loads(response.read().decode("utf-8", errors="replace"))
                except Exception:
                    continue
                games = payload.get("games") if isinstance(payload, dict) else payload
                if not isinstance(games, list):
                    continue
                refreshed_ok = True
                for item in games:
                    if not isinstance(item, dict):
                        continue
                    gid = _text(item.get("gid") or item.get("GID"))
                    if not gid or gid in seen:
                        continue
                    seen.add(gid)
                    row = dict(item)
                    row.setdefault("_identity_source", "webui_member_cache")
                    out.append(row)
                # A successful response proves this is the active local WebUI;
                # do not repeat the same mode against fallback ports.
                break
        with _WEBUI_GAMES_CACHE_LOCK:
            if refreshed_ok:
                _WEBUI_GAMES_CACHE[cache_key] = {
                    "stored_at": time.monotonic(),
                    "games": [dict(item) for item in out],
                }
            latest = dict(_WEBUI_GAMES_CACHE.get(cache_key) or {})
        source = out if refreshed_ok else list(latest.get("games") or cached.get("games") or [])
        return [dict(item) for item in source if isinstance(item, dict)]
    finally:
        with _WEBUI_GAMES_CACHE_LOCK:
            _WEBUI_GAMES_LAST_ATTEMPT[cache_key] = time.monotonic()
            waiter = _WEBUI_GAMES_INFLIGHT.pop(cache_key, None)
            if waiter is not None:
                waiter.set()


def load_games_from_webui(
    signal: Dict[str, Any],
    *,
    base_url: str,
    data_dir: Path | None = None,
    orders_dir: Path | None = None,
    force_refresh: bool = False,
) -> List[Dict[str, Any]]:
    """Load identity-only fixtures from local cache and the optional WebUI API.

    The WebUI's legacy chashui route may intentionally return an empty list;
    local snapshots/orders remain useful for matching without triggering any
    upstream member request from the Telegram listener.
    """
    root = Path(data_dir) if data_dir is not None else Path("data")
    order_root = Path(orders_dir) if orders_dir is not None else Path("orders")
    candidates = _dedupe_games(
        [
            *_cache_games(root / "odds_snapshot_cache.json"),
            *_order_games(order_root),
        ]
    )
    if candidates and not force_refresh:
        try:
            resolve_signal_match(signal, candidates)
            return candidates
        except MatchResolutionError:
            pass
    candidates.extend(_webui_games(signal, base_url, force_refresh=force_refresh))
    return _dedupe_games(candidates)


async def _warm_live_games_cache(settings_path: Path) -> None:
    while True:
        current_settings = load_settings(Path(settings_path))
        base_url = _text(current_settings.get("webui_url"))
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: _webui_games({"mode": "live"}, base_url, force_refresh=True),
            )
        except Exception:
            pass
        await asyncio.sleep(WEBUI_GAMES_REFRESH_INTERVAL_S)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram source group signal listener")
    parser.add_argument("--settings", default="data/telegram_signal.json")
    parser.add_argument("--secrets", default="data/telegram_signal_secrets.json")
    parser.add_argument("--state", default=".run/telegram_signal_state.json")
    parser.add_argument("--cursor-state", default=".run/telegram_signal_cursor.json")
    parser.add_argument("--health", default=".run/telegram_signal_health.json")
    parser.add_argument("--events", default="data/telegram_signal_events.jsonl")
    parser.add_argument("--orders-dir", default="orders")
    return parser


async def _run_listener(args: argparse.Namespace) -> int:
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession

    _install_telethon_updates_log_filter()

    settings_path = Path(args.settings)
    secrets_path = Path(args.secrets)
    cursor_path = Path(args.cursor_state)
    health_path = Path(args.health)
    _write_listener_health(health_path, "starting", started_at_ms=int(time.time() * 1000))
    settings = load_settings(settings_path)
    secret = load_private_json(secrets_path)
    secret_status = sanitize_secret_status(secret)
    if not settings.get("enabled"):
        raise RuntimeError("listener_disabled")
    try:
        api_id, api_hash = resolve_telegram_credentials(secret)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if not secret_status.get("session_configured"):
        raise RuntimeError("telegram_login_required")
    source_ids = [int(item) for item in (settings.get("source_chat_ids") or [])]
    if not source_ids:
        raise RuntimeError("source_chats_missing")

    client = TelegramClient(StringSession(_text(secret.get("session"))), api_id, api_hash)
    _write_listener_health(health_path, "connecting")
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("telegram_session_expired")

    source_titles = {
        _text(item.get("id")): _text(item.get("title"))
        for item in (settings.get("source_chats") or [])
        if isinstance(item, dict)
    }

    cursors = _load_message_cursors(cursor_path)
    message_lock = asyncio.Lock()

    async def handle_message(message: Dict[str, Any]) -> Dict[str, Any]:
        async with message_lock:
            current_settings = load_settings(settings_path)
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: process_signal_message(
                    settings=current_settings,
                    message=message,
                    games_loader=lambda signal: load_games_from_webui(
                        signal,
                        base_url=_text(current_settings.get("webui_url")),
                        data_dir=Path(args.events).parent,
                        orders_dir=Path(args.orders_dir),
                    ),
                    games_refresher=lambda signal: load_games_from_webui(
                        signal,
                        base_url=_text(current_settings.get("webui_url")),
                        data_dir=Path(args.events).parent,
                        orders_dir=Path(args.orders_dir),
                        force_refresh=True,
                    ),
                    state_path=Path(args.state),
                    event_log_path=Path(args.events),
                    orders_dir=Path(args.orders_dir),
                ),
            )
            chat_id = _text(message.get("chat_id"))
            try:
                message_id = int(message.get("message_id") or 0)
            except Exception:
                message_id = 0
            if chat_id and message_id > int(cursors.get(chat_id) or 0):
                cursors[chat_id] = message_id
                _save_message_cursors(cursor_path, cursors)
            _write_listener_health(
                health_path,
                "healthy",
                connected=True,
                source_count=len(source_ids),
                last_message_at_ms=int(time.time() * 1000),
            )
            return result

    @client.on(events.NewMessage(chats=source_ids))
    async def on_message(event: Any) -> None:
        chat_id = _text(event.chat_id)
        await handle_message(
            _telegram_message_payload(
                event,
                chat_id=chat_id,
                chat_title=source_titles.get(chat_id, ""),
            )
        )

    source_entities = await _resolve_source_entities(client, source_ids)

    async def poll_sources_once() -> None:
        for chat_id, entity in source_entities.items():
            messages = await client.get_messages(entity, limit=TELEGRAM_POLL_BATCH_SIZE)
            pending, newest = _poll_message_window(messages, cursors.get(chat_id))
            if chat_id not in cursors and newest > 0:
                cursors[chat_id] = newest
                _save_message_cursors(cursor_path, cursors)
                continue
            for telegram_message in pending:
                await handle_message(
                    _telegram_message_payload(
                        telegram_message,
                        chat_id=chat_id,
                        chat_title=source_titles.get(chat_id, ""),
                    )
                )
            if newest > int(cursors.get(chat_id) or 0):
                cursors[chat_id] = newest
                _save_message_cursors(cursor_path, cursors)

    last_poll_error = ""

    async def poll_sources() -> None:
        nonlocal last_poll_error
        while True:
            try:
                await poll_sources_once()
                last_poll_error = ""
                _write_listener_health(
                    health_path,
                    "healthy",
                    connected=True,
                    source_count=len(source_entities),
                    last_poll_at_ms=int(time.time() * 1000),
                )
                await asyncio.sleep(TELEGRAM_POLL_INTERVAL_S)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = sanitize_event_reason("worker_error", exc)
                _write_listener_health(health_path, "degraded", connected=True, error=reason)
                if reason != last_poll_error:
                    append_event(Path(args.events), {"status": "worker_error", "reason": reason})
                    last_poll_error = reason
                await asyncio.sleep(3)

    await poll_sources_once()
    _write_listener_health(
        health_path,
        "healthy",
        connected=True,
        source_count=len(source_entities),
        last_poll_at_ms=int(time.time() * 1000),
    )
    print(
        f"telegram_signal listener started sources={len(source_ids)} dry_run={bool(settings.get('dry_run', True))} poll={TELEGRAM_POLL_INTERVAL_S}s",
        flush=True,
    )
    warmup_task = asyncio.create_task(_warm_live_games_cache(settings_path))
    poll_task = asyncio.create_task(poll_sources())
    try:
        await client.run_until_disconnected()
    finally:
        for task in (warmup_task, poll_task):
            task.cancel()
        for task in (warmup_task, poll_task):
            with suppress(asyncio.CancelledError):
                await task
        await client.disconnect()
    return 0


def main(argv: Any = None) -> int:
    args = build_arg_parser().parse_args(argv)
    terminal_errors = {
        "listener_disabled",
        "invalid_api_id",
        "invalid_api_hash",
        "telegram_login_required",
        "telegram_session_expired",
        "source_chats_missing",
        "source_chat_unavailable",
    }
    while True:
        try:
            return int(asyncio.run(_run_listener(args)))
        except KeyboardInterrupt:
            _write_listener_health(Path(args.health), "stopped", connected=False)
            return 0
        except Exception as exc:
            reason = sanitize_event_reason("worker_error", exc)
            _write_listener_health(Path(args.health), "error", connected=False, error=reason)
            append_event(Path(args.events), {"status": "worker_error", "reason": reason})
            print(f"telegram_signal worker error: {reason}", flush=True)
            if reason in terminal_errors or any(reason.startswith(f"{item}:") for item in terminal_errors):
                return 1
            print("telegram_signal listener reconnecting in 3 seconds", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    raise SystemExit(main())
