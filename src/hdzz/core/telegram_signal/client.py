from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .store import load_private_json, write_private_json


class TelegramSignalClientError(RuntimeError):
    pass


ClientFactory = Callable[[str, int, str], Any]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mask_phone(phone: str) -> str:
    phone = _text(phone)
    if len(phone) <= 8:
        return "****" if phone else ""
    return f"{phone[:6]}****{phone[-4:]}"


def _default_client_factory(session_value: str, api_id: int, api_hash: str) -> Any:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    return TelegramClient(StringSession(session_value), int(api_id), str(api_hash))


def _credentials(secret: Dict[str, Any]) -> tuple:
    api_id = _text(secret.get("api_id"))
    api_hash = _text(secret.get("api_hash"))
    if not re.fullmatch(r"\d{5,12}", api_id) or int(api_id) <= 0:
        raise TelegramSignalClientError("invalid_api_id")
    if not re.fullmatch(r"[A-Fa-f0-9]{32}", api_hash):
        raise TelegramSignalClientError("invalid_api_hash")
    return int(api_id), api_hash


def save_api_credentials(path: Path, *, api_id: Any, api_hash: Any) -> Dict[str, Any]:
    current = load_private_json(Path(path))
    api_id_text = _text(api_id)
    api_hash_text = _text(api_hash)
    _credentials({"api_id": api_id_text, "api_hash": api_hash_text})
    current["api_id"] = api_id_text
    current["api_hash"] = api_hash_text
    current["updated_at"] = int(time.time())
    write_private_json(Path(path), current)
    return {"ok": True, "credentials_configured": True}


async def _send_login_code_async(
    secrets_path: Path,
    pending_path: Path,
    *,
    phone: str,
    client_factory: ClientFactory,
) -> Dict[str, Any]:
    secret = load_private_json(secrets_path)
    api_id, api_hash = _credentials(secret)
    phone = _text(phone).replace(" ", "")
    if not re.fullmatch(r"\+\d{7,15}", phone):
        raise TelegramSignalClientError("invalid_phone")

    client = client_factory(_text(secret.get("session")), api_id, api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        phone_code_hash = _text(getattr(sent, "phone_code_hash", ""))
        session_value = _text(client.session.save())
        if not phone_code_hash or not session_value:
            raise TelegramSignalClientError("send_code_incomplete")
        write_private_json(
            pending_path,
            {
                "phone": phone,
                "phone_code_hash": phone_code_hash,
                "session": session_value,
                "created_at": int(time.time()),
            },
        )
        return {"ok": True, "code_sent": True, "phone_masked": _mask_phone(phone)}
    finally:
        await client.disconnect()


def send_login_code(
    secrets_path: Path,
    pending_path: Path,
    *,
    phone: str,
    client_factory: Optional[ClientFactory] = None,
) -> Dict[str, Any]:
    return asyncio.run(
        _send_login_code_async(
            Path(secrets_path),
            Path(pending_path),
            phone=phone,
            client_factory=client_factory or _default_client_factory,
        )
    )


async def _verify_login_code_async(
    secrets_path: Path,
    pending_path: Path,
    *,
    code: str,
    password: str,
    client_factory: ClientFactory,
) -> Dict[str, Any]:
    from telethon.errors import SessionPasswordNeededError

    secret = load_private_json(secrets_path)
    pending = load_private_json(pending_path)
    api_id, api_hash = _credentials(secret)
    phone = _text(pending.get("phone"))
    code_hash = _text(pending.get("phone_code_hash"))
    pending_session = _text(pending.get("session"))
    code = re.sub(r"\s+", "", _text(code))
    if not (phone and code_hash and pending_session):
        raise TelegramSignalClientError("login_code_not_requested")
    if not re.fullmatch(r"\d{4,8}", code):
        raise TelegramSignalClientError("invalid_login_code")

    client = client_factory(pending_session, api_id, api_hash)
    await client.connect()
    try:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=code_hash)
        except SessionPasswordNeededError:
            if not _text(password):
                pending["needs_password"] = True
                write_private_json(pending_path, pending)
                return {"ok": False, "needs_password": True, "phone_masked": _mask_phone(phone)}
            await client.sign_in(password=str(password))

        if not await client.is_user_authorized():
            raise TelegramSignalClientError("telegram_authorization_failed")
        secret["session"] = _text(client.session.save())
        secret["phone"] = phone
        secret["updated_at"] = int(time.time())
        write_private_json(secrets_path, secret)
        try:
            pending_path.unlink(missing_ok=True)
        except Exception:
            pass
        return {"ok": True, "authorized": True, "phone_masked": _mask_phone(phone)}
    finally:
        await client.disconnect()


def verify_login_code(
    secrets_path: Path,
    pending_path: Path,
    *,
    code: str,
    password: str = "",
    client_factory: Optional[ClientFactory] = None,
) -> Dict[str, Any]:
    return asyncio.run(
        _verify_login_code_async(
            Path(secrets_path),
            Path(pending_path),
            code=code,
            password=password,
            client_factory=client_factory or _default_client_factory,
        )
    )


async def _list_source_dialogs_async(secrets_path: Path, client_factory: ClientFactory) -> List[Dict[str, str]]:
    secret = load_private_json(secrets_path)
    api_id, api_hash = _credentials(secret)
    session_value = _text(secret.get("session"))
    if not session_value:
        raise TelegramSignalClientError("telegram_login_required")
    client = client_factory(session_value, api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise TelegramSignalClientError("telegram_session_expired")
        dialogs = await client.get_dialogs()
        rows: List[Dict[str, str]] = []
        for dialog in dialogs:
            is_group = bool(getattr(dialog, "is_group", False))
            is_channel = bool(getattr(dialog, "is_channel", False))
            if not is_group and not is_channel:
                continue
            rows.append(
                {
                    "id": _text(getattr(dialog, "id", "")),
                    "title": _text(getattr(dialog, "title", "")) or "未命名群组",
                    "type": "group" if is_group else "channel",
                }
            )
        return rows
    finally:
        await client.disconnect()


def list_source_dialogs(secrets_path: Path, *, client_factory: Optional[ClientFactory] = None) -> List[Dict[str, str]]:
    return asyncio.run(_list_source_dialogs_async(Path(secrets_path), client_factory or _default_client_factory))


def clear_login_session(secrets_path: Path, pending_path: Path) -> Dict[str, Any]:
    secret = load_private_json(Path(secrets_path))
    secret.pop("session", None)
    secret.pop("phone", None)
    secret["updated_at"] = int(time.time())
    write_private_json(Path(secrets_path), secret)
    try:
        Path(pending_path).unlink(missing_ok=True)
    except Exception:
        pass
    return {"ok": True, "authorized": False}
