"""Open a visible Crown page in the shared Chrome without starting login."""

import argparse
import json
import time
from urllib.error import URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import ProxyHandler, Request, build_opener


def request_json(url, *, method="GET", timeout=4):
    request = Request(url, method=method)
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def choose_account(accounts):
    eligible = [
        account for account in accounts
        if account.get("kind") == "crown_odds" and account.get("enabled", True)
    ]
    if not eligible:
        return None
    return next((account for account in eligible if account.get("role") == "primary"), eligible[0])


def _is_pinnacle_host(host):
    """Return whether a tab belongs to the first (Pinnacle) data source."""
    return (
        host == "ps3838.com"
        or host.endswith(".ps3838.com")
        or host == "pinnacle.com"
        or host.endswith(".pinnacle.com")
    )


def has_account_tab(targets, account_host=None):
    """Detect an existing Crown tab even when its member domain is dynamic.

    Crown can redirect a logged-in tab from the configured entry domain to a
    member-specific host. The shared profile is dedicated to Pinnacle and
    Crown, so any other visible HTTPS page is the existing Crown tab.
    """
    expected = (account_host or "").lower().rstrip(".")
    for target in targets:
        if target.get("type") != "page":
            continue
        parsed = urlsplit(str(target.get("url") or ""))
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host:
            continue
        if expected and (host == expected or host.endswith("." + expected)):
            return True
        if not _is_pinnacle_host(host):
            return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp-url", required=True)
    parser.add_argument("--service-url", required=True)
    args = parser.parse_args()
    cdp_url = args.cdp_url.rstrip("/")
    service_url = args.service_url.rstrip("/")

    try:
        accounts_payload = request_json(f"{service_url}/api/crown/accounts")
        account = choose_account(accounts_payload.get("accounts") or [])
        if not account:
            print("未配置皇冠账号，跳过自动打开皇冠标签页。", flush=True)
            return 0

        account_host = urlsplit(str(account.get("base_url") or "")).hostname
        if not account_host:
            print("皇冠账号地址无效，跳过自动打开登录页。", flush=True)
            return 0

        if has_account_tab(request_json(f"{cdp_url}/json/list"), account_host):
            print("皇冠标签页已存在，不重复打开。", flush=True)
            return 0

        login_url = urljoin(
            str(account.get("base_url") or "").rstrip("/") + "/",
            str(account.get("login_path") or "/"),
        )
        request_json(
            f"{cdp_url}/json/new?{quote(login_url, safe='')}",
            method="PUT",
        )

        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if has_account_tab(request_json(f"{cdp_url}/json/list"), account_host):
                print("已在平博 Chrome 窗口中打开皇冠页面，请手动登录。", flush=True)
                return 0
            time.sleep(0.4)

        print("已请求打开皇冠页面，但 CDP 尚未发现对应标签页。", flush=True)
        return 1
    except (OSError, ValueError, URLError) as exc:
        print(f"打开皇冠页面失败：{exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
