"""Telegram application credentials used by the signal client.

The application credentials are deliberately kept separate from the user
session.  A session is created after the user completes the phone/code login
and remains in the private runtime file; the API credentials below are only
used to construct the Telethon client and are never included in API responses
or log records.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Tuple


# These are Telegram application credentials, not a user's login session.
# Runtime values remain supported for backwards compatibility, but a fresh
# install no longer needs a credentials file or an API-credentials form.
DEFAULT_TELEGRAM_API_ID = 29920388
DEFAULT_TELEGRAM_API_HASH = "0f4a1868645bc75c458fda938fa2c430"


def _text(value: Any) -> str:
    return str(value or "").strip()


def resolve_telegram_credentials(secret: Dict[str, Any] | None) -> Tuple[int, str]:
    """Return valid Telegram API credentials, using built-in defaults when absent.

    Explicit non-empty values are still validated.  This preserves the
    existing error handling for a damaged or manually edited secrets file,
    while an empty/new file transparently uses the application defaults.
    """

    payload = secret if isinstance(secret, dict) else {}
    api_id = _text(payload.get("api_id")) or str(DEFAULT_TELEGRAM_API_ID)
    api_hash = _text(payload.get("api_hash")) or DEFAULT_TELEGRAM_API_HASH
    if not re.fullmatch(r"\d{5,12}", api_id) or int(api_id) <= 0:
        raise ValueError("invalid_api_id")
    if not re.fullmatch(r"[A-Fa-f0-9]{32}", api_hash):
        raise ValueError("invalid_api_hash")
    return int(api_id), api_hash


def telegram_credentials_configured(secret: Dict[str, Any] | None) -> bool:
    """Whether usable credentials are available without exposing their values."""

    try:
        resolve_telegram_credentials(secret)
    except (TypeError, ValueError):
        return False
    return True

