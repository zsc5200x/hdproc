from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Dict


def parse_xml_kv(text: str) -> Dict[str, str]:
    root = ET.fromstring(text)
    out: Dict[str, str] = {}
    for child in list(root):
        if child.tag:
            out[str(child.tag)] = (child.text or "").strip()
    return out


def parse_xml_flat(text: str) -> Dict[str, str]:
    root = ET.fromstring(text)
    out: Dict[str, str] = {}
    for el in root.iter():
        if el is root:
            continue
        tag = str(getattr(el, "tag", "") or "").strip()
        if not tag:
            continue
        val = str(getattr(el, "text", "") or "").strip()
        if val == "":
            continue
        out[tag] = val
    return out


def parse_cookie_header(cookie: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in str(cookie or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k:
            out[k] = v
    return out

