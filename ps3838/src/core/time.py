"""Shared local timestamp formatting without application side effects."""
from datetime import datetime


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")

