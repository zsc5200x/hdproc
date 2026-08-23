from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple


CompletedAttempt = Tuple[str, str, Future]


class AttemptDispatcher:
    """Bounded executor that never runs two attempts for the same member."""

    def __init__(self, max_workers: int = 3) -> None:
        workers = max(1, int(max_workers or 1))
        self.max_workers = workers
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="follow-attempt")
        self._lock = threading.RLock()
        self._busy_members: set = set()
        self._active: Dict[Future, Tuple[str, str]] = {}
        self._keys: set = set()

    def submit(
        self,
        key: str,
        member_id: str,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Optional[Future]:
        job_key = str(key or "").strip()
        mid = str(member_id or "").strip()
        if not (job_key and mid):
            return None

        with self._lock:
            if job_key in self._keys or mid in self._busy_members:
                return None
            if len(self._active) >= self.max_workers:
                return None
            self._keys.add(job_key)
            self._busy_members.add(mid)

            def run() -> Any:
                return fn(*args, **kwargs)

            future = self._executor.submit(run)
            self._active[future] = (job_key, mid)
            return future

    def poll_completed(self) -> List[CompletedAttempt]:
        completed: List[CompletedAttempt] = []
        with self._lock:
            for future, (job_key, mid) in list(self._active.items()):
                if not future.done():
                    continue
                self._active.pop(future, None)
                self._keys.discard(job_key)
                self._busy_members.discard(mid)
                completed.append((job_key, mid, future))
        return completed

    def wait(self, future: Future, timeout: Optional[float] = None) -> Any:
        try:
            return future.result(timeout=timeout)
        finally:
            with self._lock:
                rec = self._active.pop(future, None)
                if rec:
                    job_key, mid = rec
                    self._keys.discard(job_key)
                    self._busy_members.discard(mid)

    def is_member_busy(self, member_id: str) -> bool:
        with self._lock:
            return str(member_id or "").strip() in self._busy_members

    def busy_member_ids(self) -> set:
        """Return a snapshot of members currently running a network attempt.

        The scheduler uses this snapshot before picking a member.  Previously it
        picked a busy member first and only discovered the conflict in
        ``submit()``, which made the caller fall through to the synchronous
        execution path and block the whole queue.
        """
        with self._lock:
            return set(self._busy_members)

    def is_key_active(self, key: str) -> bool:
        with self._lock:
            return str(key or "").strip() in self._keys

    def available_slots(self) -> int:
        with self._lock:
            return max(0, self.max_workers - len(self._active))

    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=bool(wait))
