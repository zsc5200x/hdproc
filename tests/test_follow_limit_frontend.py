from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_removed_max_stake_field_and_kept_cumulative_limit_fields() -> None:
    html = (ROOT / "app" / "webui" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "app" / "webui" / "app.js").read_text(encoding="utf-8")

    assert 'id="follow-max"' not in html
    assert '"follow-max"' not in js
    assert "max_amount" not in js
    assert 'id="follow-match-limit"' in html
    assert 'id="follow-market-limit"' in html
    assert "match_limit_amount" in js
    assert "market_limit_amount" in js
