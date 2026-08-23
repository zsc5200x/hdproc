import logging
import os
from pathlib import Path
from typing import Optional


def setup_basic_logging(*, name: str = "hdzz", level: str = "INFO", log_file: Optional[str] = None) -> logging.Logger:
    """
    Phase2 过渡期：先提供一个最小可用的统一 logger。
    - 不强行改动旧模块（避免大范围行为变化）
    - 新增模块优先用这里的 logger
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    lvl = getattr(logging, str(level or "INFO").upper(), logging.INFO)
    logger.setLevel(lvl)

    fmt = logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    if log_file:
        try:
            p = Path(str(log_file))
            p.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(str(p), encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except Exception:
            pass

    # 避免重复输出到 root logger
    logger.propagate = False
    # 兼容某些环境默认 buffer 导致看不到实时日志
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    return logger

