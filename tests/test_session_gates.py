from __future__ import annotations

import json
import tempfile
from pathlib import Path

from hdzz.core.follower.engine import _final_session_gate
from hdzz.core.member.session_state import session_fingerprint


def _member(generation: int = 1) -> dict[str, object]:
    member: dict[str, object] = {
        "id": "member-a",
        "label": "member-a",
        "status": "online",
        "base_url": "https://example.invalid",
        "ver": "v1",
        "uid": "uid-a",
        "cookie": "SESSION=a",
        "session_stable": True,
        "session_generation": generation,
    }
    member["session_fingerprint"] = session_fingerprint(member)
    return member


def test_final_session_gate_rejects_generation_change() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        current = _member(2)
        (data_dir / "arsenal.json").write_text(json.dumps({"members": [current]}), encoding="utf-8")
        selected = _member(1)
        ok, reason, _ = _final_session_gate(
            data_dir=data_dir,
            member=selected,
            selected_generation=1,
            selected_fingerprint=str(selected["session_fingerprint"]),
        )
    assert ok is False
    assert reason == "SESSION_CHANGED_BEFORE_SUBMIT"


def test_final_session_gate_accepts_unchanged_legacy_session() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        selected = _member(1)
        (data_dir / "arsenal.json").write_text(json.dumps({"members": [selected]}), encoding="utf-8")
        ok, reason, current = _final_session_gate(
            data_dir=data_dir,
            member=selected,
            selected_generation=1,
            selected_fingerprint=str(selected["session_fingerprint"]),
        )
    assert ok is True
    assert reason == "SESSION_READY"
    assert current["id"] == "member-a"
