from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List
from urllib import parse, request

from .ingest import MatchResolutionError, build_follow_order, find_recent_confirmed_match, resolve_signal_match
from .parser import SignalParseError, parse_telegram_bet_signal
from .store import (
    append_event,
    load_private_json,
    load_settings,
    remember_message,
    remember_order_id,
    sanitize_secret_status,
)


GamesLoader = Callable[[Dict[str, Any]], Iterable[Dict[str, Any]]]


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


def process_signal_message(
    *,
    settings: Dict[str, Any],
    message: Dict[str, Any],
    games_loader: GamesLoader,
    state_path: Path,
    event_log_path: Path,
    orders_dir: Path,
) -> Dict[str, Any]:
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

    try:
        signal = parse_telegram_bet_signal(message.get("text"))
    except SignalParseError as exc:
        result = {"status": "rejected_parse", "reason": str(exc)}
        append_event(Path(event_log_path), _event_summary("rejected_parse", message, reason=str(exc)))
        return result

    try:
        games = list(games_loader(signal))
        try:
            game = resolve_signal_match(signal, games)
        except MatchResolutionError as exc:
            if str(exc) != "number_pair_not_found":
                raise
            game = find_recent_confirmed_match(
                signal,
                Path(orders_dir),
                target_name=_text(settings.get("target_name")) or "telegram_signal",
                received_ts=int(message.get("received_ts") or time.time()),
            )
            if not game:
                raise
        order = build_follow_order(
            signal,
            game,
            chat_id=chat_id,
            message_id=message_id,
            chat_title=message.get("chat_title"),
            received_ts=int(message.get("received_ts") or time.time()),
            target_name=_text(settings.get("target_name")) or "telegram_signal",
        )
    except (MatchResolutionError, OSError, ValueError, RuntimeError) as exc:
        result = {"status": "rejected_match", "reason": str(exc)}
        append_event(
            Path(event_log_path),
            _event_summary(
                "rejected_match",
                message,
                reason=str(exc),
                order_id=signal.get("order_id"),
                league=signal.get("league"),
            ),
        )
        return result

    order_id = _text(order.get("orderId"))
    if not remember_order_id(Path(state_path), order_id):
        append_event(Path(event_log_path), _event_summary("duplicate_order", message, order_id=order_id))
        return {"status": "duplicate_order", "order_id": order_id}

    summary = {
        "order_id": order_id,
        "league": _text(order.get("league")),
        "match": _text(order.get("sides")),
        "line": _text(order.get("line")),
        "odds": _text(order.get("odds")),
        "amount": _text(order.get("stake")),
        "gid": _text(order.get("gid")),
    }
    if bool(settings.get("dry_run", True)):
        append_event(Path(event_log_path), _event_summary("dry_run", message, **summary))
        return {"status": "dry_run", "order": order}

    out_path = Path(orders_dir) / f"{_text(order.get('target')) or 'telegram_signal'}.jsonl"
    _append_order(out_path, order)
    append_event(Path(event_log_path), _event_summary("injected", message, **summary))
    return {"status": "injected", "order": order, "path": str(out_path)}


def _fetch_json(url: str, timeout_s: int = 12) -> Dict[str, Any]:
    req = request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with request.urlopen(req, timeout=max(3, int(timeout_s))) as response:
        payload = json.loads((response.read() or b"{}").decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise RuntimeError("invalid_webui_response")
    if payload.get("ok") is False:
        raise RuntimeError(_text(payload.get("message") or payload.get("error")) or "webui_request_failed")
    return payload


def load_games_from_webui(signal: Dict[str, Any], *, base_url: str) -> List[Dict[str, Any]]:
    base = _text(base_url).rstrip("/") or "http://127.0.0.1:2299"
    status = _fetch_json(f"{base}/api/chashui/status?lane=games")
    member = status.get("member") if isinstance(status.get("member"), dict) else {}
    member_id = _text(member.get("id"))
    if not member_id:
        raise RuntimeError("readonly_member_unavailable")
    modes = ["live"] if _text(signal.get("mode")) == "live" else ["today", "early"]
    games: List[Dict[str, Any]] = []
    for mode in modes:
        query = parse.urlencode({"member_id": member_id, "mode": mode})
        payload = _fetch_json(f"{base}/api/chashui/games?{query}")
        games.extend([item for item in (payload.get("games") or []) if isinstance(item, dict)])
    return games


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram source group signal listener")
    parser.add_argument("--settings", default="data/telegram_signal.json")
    parser.add_argument("--secrets", default="data/telegram_signal_secrets.json")
    parser.add_argument("--state", default=".run/telegram_signal_state.json")
    parser.add_argument("--events", default="data/telegram_signal_events.jsonl")
    parser.add_argument("--orders-dir", default="orders")
    return parser


async def _run_listener(args: argparse.Namespace) -> int:
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession

    settings_path = Path(args.settings)
    secrets_path = Path(args.secrets)
    settings = load_settings(settings_path)
    secret = load_private_json(secrets_path)
    secret_status = sanitize_secret_status(secret)
    if not settings.get("enabled"):
        raise RuntimeError("listener_disabled")
    if not secret_status.get("credentials_configured"):
        raise RuntimeError("credentials_missing")
    if not secret_status.get("session_configured"):
        raise RuntimeError("telegram_login_required")
    source_ids = [int(item) for item in (settings.get("source_chat_ids") or [])]
    if not source_ids:
        raise RuntimeError("source_chats_missing")

    client = TelegramClient(StringSession(_text(secret.get("session"))), int(secret.get("api_id")), _text(secret.get("api_hash")))
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("telegram_session_expired")

    source_titles = {
        _text(item.get("id")): _text(item.get("title"))
        for item in (settings.get("source_chats") or [])
        if isinstance(item, dict)
    }

    @client.on(events.NewMessage(chats=source_ids))
    async def on_message(event: Any) -> None:
        current_settings = load_settings(settings_path)
        message = {
            "chat_id": _text(event.chat_id),
            "message_id": _text(event.id),
            "chat_title": source_titles.get(_text(event.chat_id), ""),
            "text": _text(getattr(event, "raw_text", "")),
            "received_ts": int(time.time()),
        }
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: process_signal_message(
                settings=current_settings,
                message=message,
                games_loader=lambda signal: load_games_from_webui(signal, base_url=_text(current_settings.get("webui_url"))),
                state_path=Path(args.state),
                event_log_path=Path(args.events),
                orders_dir=Path(args.orders_dir),
            ),
        )

    print(f"telegram_signal listener started sources={len(source_ids)} dry_run={bool(settings.get('dry_run', True))}", flush=True)
    await client.run_until_disconnected()
    return 0


def main(argv: Any = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        return int(asyncio.run(_run_listener(args)))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        append_event(Path(args.events), {"status": "worker_error", "reason": str(exc)})
        print(f"telegram_signal worker error: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
