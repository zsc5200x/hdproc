"""严格解析皇冠会员站登录协议，不接触业务接口权限。"""
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET


MAX_RESPONSE_BYTES = 256 * 1024
KINDS = frozenset({"crown_odds"})


@dataclass(frozen=True)
class LoginEvidence:
    status: str
    message: str
    # A session identifier must never appear in reprs, public API or logs.
    uid: str = field(default="", repr=False)


def is_login_response(kind: str, url: str, request_data: str = "") -> bool:
    """Only inspect the Crown member authentication endpoint."""
    if kind != "crown_odds":
        return False
    path = urlsplit(url).path.rstrip("/")
    operation = parse_qs(request_data, keep_blank_values=True).get("p", [])
    return path == "/transform_nl.php" and operation == ["chk_login"]


def _session_id(value) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        return ""
    if any(ord(char) < 32 for char in value):
        return ""
    return value


def parse_member_login_domain(url: str, http_status: int, body: str,
                              request_data: str = "") -> str:
    """Read the member entry site's observed domain handoff, never a redirect alone."""
    try:
        path = urlsplit(url).path
    except ValueError:
        return ""
    if (path != "/transform_nl.php"
            or parse_qs(request_data, keep_blank_values=True).get("p") != ["check_login_domain"]
            or not 200 <= http_status < 300
            or len(body.encode("utf-8")) > MAX_RESPONSE_BYTES
            or "<!DOCTYPE" in body.upper() or "<!ENTITY" in body.upper()):
        return ""
    try:
        root = ET.fromstring(body)
        if (root.tag != "serverresponse" or len(root.findall("code")) != 1
                or root.findtext("code") != "664" or len(root.findall("new_domain")) != 1):
            return ""
        if len(root.find("code")) or len(root.find("new_domain")):
            return ""
        domain = root.findtext("new_domain", "").strip()
        if not domain or any(ord(char) < 32 for char in domain):
            return ""
        from .storage import normalize_base_url
        return normalize_base_url(domain)
    except (ET.ParseError, ValueError, TypeError):
        return ""


def parse_login_response(kind: str, url: str, http_status: int, body: str,
                         request_data: str = "") -> LoginEvidence | None:
    """Return redacted Crown login evidence, never arbitrary upstream response text."""
    if not is_login_response(kind, url, request_data):
        return None
    if not 200 <= http_status < 300:
        return LoginEvidence("error", "登录接口返回 HTTP 错误，请在浏览器检查登录页面。")
    if len(body.encode("utf-8")) > MAX_RESPONSE_BYTES:
        return LoginEvidence("error", "登录响应超过安全解析大小，请在浏览器检查。")
    body = body.strip()
    if "<!DOCTYPE" in body.upper() or "<!ENTITY" in body.upper():
        return LoginEvidence("error", "会员登录 XML 含不支持的声明，已停止解析。")
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, ValueError):
        return LoginEvidence("awaiting_manual", "会员登录响应不是已核验的 XML，请检查页面提示。")
    if root.tag != "serverresponse":
        return LoginEvidence("error", "会员登录 XML 根节点未识别。")
    for name in ("status", "msg", "uid"):
        if len(root.findall(name)) != 1:
            return LoginEvidence("error", "会员登录 XML 状态字段缺失或重复。")
    uid = _session_id(root.findtext("uid"))
    if root.findtext("status") == "200" and root.findtext("msg") == "100" and uid:
        return LoginEvidence("session_received", "已收到会员会话，需检查正常登录页面；盘口读取权限尚未验证。", uid)
    return LoginEvidence("awaiting_manual", "会员端未返回已核验会话，请在浏览器检查登录或二次验证。")
