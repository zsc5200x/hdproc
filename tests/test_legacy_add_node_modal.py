import unittest
from pathlib import Path


class TestLegacyAddNodeModal(unittest.TestCase):
    def test_modal_contains_mode_switch_and_login_tab_controls(self) -> None:
        page = (Path(__file__).resolve().parents[1] / "app" / "webui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("接入类型", page)
        self.assertIn("本地后台节点", page)
        self.assertIn("远端交易信号", page)
        self.assertIn("登录通道", page)
        self.assertIn("登入2", page)
        self.assertIn("登入3", page)
        self.assertIn("掉线重登间隔", page)
        self.assertIn('id="add-relogin-min-interval"', page)
        self.assertIn("监控会员", page)
        self.assertIn("订单来源（登入3下线）", page)
        self.assertIn('class="modal-box add-node-dialog"', page)
        self.assertIn('class="add-node-mode-block"', page)
        self.assertIn('class="add-node-grid"', page)
        self.assertIn('id="add-account-connection-title"', page)
        self.assertIn('id="add-account-policy-title"', page)
        self.assertIn('id="add-account-monitor-title"', page)
        self.assertIn('label class="form-label" for="add-base-url"', page)
        self.assertIn('input class="form-input" type="url" id="add-base-url"', page)
        self.assertNotIn('textarea class="form-input" id="add-base-url"', page)
        self.assertIn('aria-pressed="true" aria-controls="add-account-fields"', page)


if __name__ == "__main__":
    unittest.main()
