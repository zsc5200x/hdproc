from __future__ import annotations

import math
import signal
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional, Tuple


class HardTimeout(BaseException):
    """
    A hard, non-swallowable timeout raised from SIGALRM.

    It inherits from BaseException (not Exception) on purpose:
    large parts of the codebase use broad `except Exception:` blocks which would
    otherwise accidentally swallow timeouts and keep running, defeating the
    whole point of a hard cap (and causing minutes-long queue stalls).
    """


_REQUEST_DEADLINE_MONOTONIC: ContextVar[float] = ContextVar(
    "request_deadline_monotonic",
    default=0.0,
)


def _deadline_now() -> float:
    return float(time.monotonic())


def _current_deadline() -> float:
    try:
        return float(_REQUEST_DEADLINE_MONOTONIC.get() or 0.0)
    except Exception:
        return 0.0


def _safe_reset_deadline(deadline_token) -> None:
    try:
        _REQUEST_DEADLINE_MONOTONIC.reset(deadline_token)
    except RuntimeError as e:
        # 嵌套 hard_timeout + BaseException 链路里，极少数情况下 finally 会重复触发 reset；
        # 这里把“已消费过 token”的场景视作幂等完成，避免只在清理阶段再炸一个新异常。
        if "already been used once" not in str(e):
            raise
    except Exception:
        raise


def _deadline_timeout_seconds(timeout_s: int) -> int:
    try:
        t = int(timeout_s or 0)
    except Exception:
        t = 0
    t = max(1, min(600, t))

    deadline = _current_deadline()
    if deadline <= 0.0:
        return t

    remain = float(deadline - _deadline_now())
    if remain <= 0.0:
        raise HardTimeout("hard_timeout")

    return max(1, min(t, int(math.ceil(remain))))


@contextmanager
def hard_timeout(seconds: int):
    """
    为 requests 的“总耗时”兜底：
    - requests 的 read timeout 是“无数据等待时间”，服务端若持续 drip bytes 可能导致总耗时被拉到分钟级；
    - follow_engine 是单线程队列调度，一次卡住会拖慢后续订单。

    在支持 SIGALRM 的系统上，用信号强制把单次阻塞控制在 seconds 以内。
    """
    try:
        sec = int(seconds or 0)
    except Exception:
        sec = 0
    if sec <= 0:
        yield
        return

    now = _deadline_now()
    current_deadline = _current_deadline()
    new_deadline = now + float(sec)
    if current_deadline > 0.0:
        new_deadline = min(new_deadline, current_deadline)
    deadline_token = _REQUEST_DEADLINE_MONOTONIC.set(float(new_deadline))

    # 仅主线程 + 支持 SIGALRM 时启用（Windows/子线程自动降级为 no-op）。
    # 这里不能把 yield 放进捕获初始化异常的 try 中：with 代码块抛出的
    # 普通异常会沿 yield 反向注入生成器，若被这里捕获后再次 yield，
    # contextlib.contextmanager 会报“generator didn't stop after throw()”。
    try:
        signal_supported = hasattr(signal, "SIGALRM")
        signal_enabled = threading.current_thread() is threading.main_thread() and signal_supported
    except Exception:
        signal_enabled = False
    if not signal_enabled:
        try:
            yield
        finally:
            _safe_reset_deadline(deadline_token)
        return

    old_handler = signal.getsignal(signal.SIGALRM)
    try:
        old_itimer = signal.getitimer(signal.ITIMER_REAL)
    except Exception:
        old_itimer = (0.0, 0.0)
    started = time.monotonic()

    def _handler(signum, frame):
        raise HardTimeout("hard_timeout")

    block_exc: Optional[BaseException] = None
    try:
        signal.signal(signal.SIGALRM, _handler)
        # 关键：支持嵌套 hard_timeout。
        #
        # 旧实现会无条件 setitimer(sec)，导致：
        # - 外层 hard_timeout 设为 2s；
        # - 内层 hard_timeout 设为 5s；
        # 内层会把 itimer 从 2s 覆盖成 5s，外层“本应超时”的信号永远不会触发；
        # 并且内层 finally 还可能把计时器清零，彻底丢失外层超时兜底（队列就会被卡到分钟级）。
        #
        # 这里遵循“更严格者优先”：如果当前已经存在更短的计时器，则不要延长它。
        try:
            prev_remain = float(old_itimer[0] or 0.0)
        except Exception:
            prev_remain = 0.0
        new_remain = float(sec)
        if prev_remain > 0.0:
            new_remain = min(new_remain, prev_remain)
        signal.setitimer(signal.ITIMER_REAL, max(0.0, float(new_remain)))
        try:
            yield
        except BaseException as e:
            block_exc = e
            raise
    finally:
        try:
            _safe_reset_deadline(deadline_token)
        except Exception:
            pass
        try:
            signal.signal(signal.SIGALRM, old_handler)
        except Exception:
            pass
        try:
            spent = max(0.0, float(time.monotonic() - started))
            remain = max(0.0, float(old_itimer[0] or 0.0) - spent)
            interval = float(old_itimer[1] or 0.0)
            if remain > 0.0 or interval > 0.0:
                signal.setitimer(signal.ITIMER_REAL, remain, interval)
            else:
                signal.setitimer(signal.ITIMER_REAL, 0.0)
        except Exception:
            try:
                signal.setitimer(signal.ITIMER_REAL, 0.0)
            except Exception:
                pass

        # 如果进入本 context 时已经有一个更短的 itimer（通常来自外层 hard_timeout），
        # 且本 context 实际执行时间已经超过该剩余时间，但未触发 SIGALRM（极端情况下可能发生），
        # 则补发 HardTimeout，避免“外层兜底丢失”。
        try:
            prev_remain2 = float(old_itimer[0] or 0.0)
        except Exception:
            prev_remain2 = 0.0
        try:
            spent2 = max(0.0, float(time.monotonic() - started))
        except Exception:
            spent2 = 0.0
        if block_exc is None and prev_remain2 > 0.0 and spent2 >= prev_remain2:
            raise HardTimeout("hard_timeout")


def requests_timeout(timeout_s: int) -> Tuple[int, int]:
    # requests 支持 timeout=(connect, read)；connect 单独给一个上限，避免代理/连接阶段拖太久
    t = _deadline_timeout_seconds(timeout_s)
    connect_t = min(10, t)
    read_t = t
    return (connect_t, read_t)
