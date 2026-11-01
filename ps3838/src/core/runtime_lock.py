"""Coordinate runtime relocation with scheduled maintenance."""

from contextlib import contextmanager
import fcntl
from pathlib import Path


@contextmanager
def runtime_data_lock(root: Path, *, exclusive: bool = False):
    # Keep this inode outside directories moved by the migration.
    path = root / ".runtime-migration.lock"
    with path.open("a", encoding="utf-8") as handle:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(handle, mode | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("运行数据迁移或维护正在进行，请完成后重试。") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
