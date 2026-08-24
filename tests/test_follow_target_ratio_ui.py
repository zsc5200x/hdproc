import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestFollowTargetRatioUi(unittest.TestCase):
    def test_follow_settings_replaces_recent_records_with_target_ratios(self) -> None:
        page = (ROOT / "app" / "webui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="target-ratios-container"', page)
        self.assertIn("单账号比例", page)
        self.assertNotIn("近期跟单记录", page)
        self.assertNotIn('id="follow-results-tbody"', page)
        self.assertNotIn('id="follow-refresh-results"', page)

    def test_follow_settings_includes_global_only_live_toggle(self) -> None:
        page = (ROOT / "app" / "webui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="follow-only-live"', page)
        self.assertIn('aria-pressed="false"', page)
        self.assertIn("只跟滚球", page)
        self.assertIn("早盘订单仍接收并记录，但不执行跟单", page)
        self.assertNotIn("只接收滚球订单", page)

    def test_app_renders_and_saves_target_ratio_percent(self) -> None:
        script = (ROOT / "app" / "webui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function renderTargetRatios(targets)", script)
        self.assertIn('row.className = "target-ratio-row"', script)
        self.assertIn("renderTargetRatios(s.targets || [])", script)
        self.assertIn("Array.isArray(state.orders)", script)
        self.assertIn("Array.isArray(state.availableTargets)", script)
        self.assertIn("loadTargets", script)
        self.assertIn("远端交易节点", script)
        self.assertIn('document.querySelectorAll(".target-ratio-row[data-name]")', script)
        self.assertIn("ratio_percent", script)

    def test_app_renders_and_saves_global_only_live(self) -> None:
        script = (ROOT / "app" / "webui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function setFollowOnlyLiveValue(value)", script)
        self.assertIn('const onlyLive    = $("follow-only-live")?.getAttribute("aria-pressed") === "true";', script)
        self.assertIn("only_live: onlyLive", script)
        self.assertIn("早盘订单仍会接收并记录，但不会执行跟单", script)
        self.assertIn("早盘和滚球订单都会接收并执行跟单", script)

    def test_target_ratio_global_button_switches_to_custom_ratio(self) -> None:
        script = (ROOT / "app" / "webui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("const setTargetRatioCustom = () =>", script)
        self.assertIn("input.value = String(globalRatio);", script)
        self.assertIn('modeBtn.textContent = custom ? "单独" : "全局";', script)
        self.assertIn("input.focus();", script)
        self.assertIn("input.select();", script)

    def test_target_filter_ui_includes_follow_disable_toggle(self) -> None:
        script = (ROOT / "app" / "webui" / "app.js").read_text(encoding="utf-8")

        self.assertIn('class="target-enabled-btn', script)
        self.assertIn("禁跟", script)
        self.assertIn("item.enabled = enabled;", script)

    def test_target_filter_disable_toggle_treats_zero_ratio_as_disabled(self) -> None:
        script = (ROOT / "app" / "webui" / "app.js").read_text(encoding="utf-8")
        start = script.index("function renderTargetFilters(targets)")
        end = script.index("function renderTargetRatios(targets)", start)
        target_filter_script = script[start:end]

        self.assertIn(
            "const enabled = rule.enabled !== false && normalizeTargetRatioPercent(rule.ratio_percent) !== 0;",
            target_filter_script,
        )


if __name__ == "__main__":
    unittest.main()
