"""单账号前台命令行。后台开发可直接复用 client 和 OrderStore。"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from .client import Ps3838Client, Ps3838SessionError, Ps3838LoginInterrupted, Ps3838LoginWindowClosed
from .storage import InstanceLock, OrderStore, atomic_json, is_running, read_json

PROJECT = Path(__file__).resolve().parent.parent


def load_config(path: Path):
    cfg = read_json(path)
    if not isinstance(cfg, dict):
        raise ValueError("配置不存在，请先运行 python run.py init")
    name = str(cfg.get("name") or "ps3838").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("name 只能含英文字母、数字、下划线或连字符")
    cfg["name"] = name
    cfg["username"] = os.environ.get("PS38_USERNAME", str(cfg.get("username") or ""))
    cfg["password"] = os.environ.get("PS38_PASSWORD", str(cfg.get("password") or ""))
    base = str(cfg.get("base_url") or "https://www.ps38ag.com").rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or parsed.netloc != "www.ps38ag.com" or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("当前版本只支持已验证的 https://www.ps38ag.com 接口")
    cfg["base_url"] = base
    cfg["interval_seconds"] = float(cfg.get("interval_seconds", 15))
    if not math.isfinite(cfg["interval_seconds"]) or cfg["interval_seconds"] < 5:
        raise ValueError("interval_seconds 必须是不小于 5 的有限数值")
    for key in ("headless", "minimize_after_login"):
        if key in cfg and not isinstance(cfg[key], bool):
            raise ValueError(f"{key} 必须是 JSON 布尔值")
    if cfg.get("browser_channel", "") not in ("", "chromium", "chrome", "msedge"):
        raise ValueError("browser_channel 可为空或 chromium/chrome/msedge")
    return cfg


def safe_error(exc, cfg):
    text = str(exc)
    for key in ("password", "x_intellifend_secret"):
        if cfg.get(key):
            text = text.replace(str(cfg[key]), "[已隐藏]")
    return text[:500]


def run_watch(cfg, directory: Path, *, once=False, force=False, client_factory=Ps3838Client):
    stopped = False
    last_state = {}
    state_path = directory / "status.json"
    previous_handlers = {}

    def stop(*_):
        nonlocal stopped
        stopped = True

    def publish(status, message, **extra):
        nonlocal last_state
        state = {"status": status, "message": message, "pid": os.getpid(), "at": time.time(), **extra}
        last_state = state
        atomic_json(state_path, state)
        print(message, flush=True)

    with InstanceLock(directory / "worker.lock"):
        store = OrderStore(directory, cfg["name"])
        client = client_factory(cfg, directory / "profile")
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, stop)
        try:
            publish("login", "正在打开平博登录窗口。请完成登录和 2FA，等待期间窗口会一直保留。")
            client.login(force=force, should_continue=lambda: not stopped,
                         on_wait=lambda msg: publish("login", msg))
            failures = 0
            while not stopped:
                try:
                    # 验证登录时返回的订单也要保存，避免首次正式轮询前窗口内订单消失。
                    if client.initial_payload is not None:
                        payload = client.initial_payload
                        client.initial_payload = None
                    else:
                        _, payload = client.poll()
                    events, count = store.ingest(payload)
                    publish("online", f"[{time.strftime('%H:%M:%S')}] 本轮 {count} 笔，记录 {len(events)} 个事件", count=count)
                    for event in events:
                        order = event["order"]
                        print(json.dumps({"event_id": event["event_id"], "event": event["event"],
                                          "orderId": order["orderId"], "sides": order["sides"],
                                          "stake": order["stake"], "currency": order["currency"],
                                          "changes": event["changes"]}, ensure_ascii=False), flush=True)
                    failures = 0
                    if once:
                        return 0
                    if cfg.get("minimize_after_login", False):
                        client.minimize_window()
                except Ps3838SessionError:
                    publish("login", "会话需要验证，请在原浏览器窗口继续登录/2FA。不会自动反复提交密码。")
                    client.wait_for_login(should_continue=lambda: not stopped,
                                          on_wait=lambda msg: publish("login", msg))
                    continue
                except Ps3838LoginWindowClosed:
                    raise
                except (OSError, ValueError, sqlite3.Error):
                    # 磁盘/数据库失败必须显式停止，不能标为已采集后继续丢单。
                    raise
                except Exception as exc:
                    failures += 1
                    publish("error", "读取失败：" + safe_error(exc, cfg))
                    if once:
                        return 1
                client.wait(min(60, cfg["interval_seconds"] * max(1, failures)))
            return 0
        except Ps3838LoginInterrupted:
            return 0
        except Ps3838LoginWindowClosed as exc:
            publish("offline", str(exc))
            return 1
        except Exception as exc:
            publish("error", safe_error(exc, cfg))
            raise
        finally:
            try:
                client.close()
            finally:
                store.close()
                atomic_json(state_path, {"status": "stopped", "pid": os.getpid(), "at": time.time(),
                                        "message": "采集进程已停止，订单和浏览器配置已保留", "last_state": last_state})
                for sig, handler in previous_handlers.items():
                    signal.signal(sig, handler)


def doctor(cfg):
    print("Python", sys.version.split()[0])
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        executable = Path(pw.chromium.executable_path)
        channel = cfg.get("browser_channel") or "默认 Chromium"
        print("浏览器通道：", channel)
        print("内置 Chromium：", "已安装" if executable.exists() else "未安装，请执行 python -m playwright install chromium")
        if not executable.exists() and not cfg.get("browser_channel"):
            return 1
    print("依赖检查完成；此命令不会登录或访问网站。")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="HD PS38：平博独立只读登录和抓单")
    parser.add_argument("command", choices=["init", "doctor", "login", "watch", "status"], nargs="?", default="watch")
    parser.add_argument("--config", type=Path, default=PROJECT / "config.json")
    parser.add_argument("--once", action="store_true", help="完成一次有效读取后退出")
    parser.add_argument("--relogin", action="store_true", help="清除此独立账号的登录 Cookie 后重新登录")
    args = parser.parse_args(argv)
    cfg = {}
    try:
        os.umask(0o077)
        if args.command == "init":
            if args.config.exists():
                print("配置已存在，保持原内容：", args.config)
            else:
                atomic_json(args.config, read_json(PROJECT / "config.example.json"))
                print("已创建配置：", args.config)
            return 0
        cfg = load_config(args.config)
        data_dir = Path(str(cfg.get("data_dir") or "data"))
        if not data_dir.is_absolute():
            data_dir = args.config.resolve().parent / data_dir
        directory = data_dir / cfg["name"]
        if args.command == "doctor":
            return doctor(cfg)
        if args.command == "status":
            state = read_json(directory / "status.json", {})
            running = is_running(directory)
            print(json.dumps({"running": running, "status": state if running else {"status": "stopped"}, "last_record": state}, ensure_ascii=False, indent=2))
            return 0
        return run_watch(cfg, directory, once=args.once or args.command == "login", force=args.relogin)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print("启动/运行失败：" + safe_error(exc, cfg), file=sys.stderr)
        return 1
