import threading
import unittest
from unittest.mock import Mock, patch

import app
import crown_runtime


class CrownLifecycleTests(unittest.TestCase):
    def test_refresh_loop_stops_without_another_poll(self):
        stopped = threading.Event()
        def refresh():
            stopped.set()
            return {"ok": True}
        with patch.object(app, "refresh_once", side_effect=refresh) as refresh_once:
            app.crown_refresh_loop(stopped)
        refresh_once.assert_called_once_with()

    def test_main_joins_refresh_before_runtime_close_and_uses_config(self):
        order = []
        server = Mock()
        server.serve_forever.side_effect = KeyboardInterrupt
        worker = Mock(ident=123)
        worker.join.side_effect = lambda: order.append("joined")
        with patch.object(app, "load_config", return_value={"crown_host": "127.0.0.1", "crown_port": 12345}), patch.object(app, "ThreadingHTTPServer", return_value=server) as factory, patch.object(app, "initialize_crown") as initialize, patch.object(app.threading, "Thread", return_value=worker), patch.object(app.signal, "signal"), patch.object(app, "close_crown", side_effect=lambda: order.append("closed")):
            app.main()
        factory.assert_called_once_with(("127.0.0.1", 12345), app.Handler)
        initialize.assert_called_once_with()
        worker.start.assert_called_once_with()
        self.assertEqual(order, ["joined", "closed"])
        server.server_close.assert_called_once_with()

    def test_runtime_joins_details_before_closing_dependencies(self):
        order = []
        worker = Mock()
        worker.join.side_effect = lambda: order.append("details")
        monitor, auth, store = Mock(), Mock(), Mock()
        monitor.close.side_effect = lambda: order.append("monitor")
        auth.close.side_effect = lambda: order.append("auth")
        store.close.side_effect = lambda: order.append("store")
        with patch.object(crown_runtime, "_more_refresh_thread", worker), patch.object(crown_runtime, "monitor", monitor), patch.object(crown_runtime, "auth", auth), patch.object(crown_runtime, "store", store):
            crown_runtime.close()
        self.assertEqual(order, ["details", "monitor", "auth", "store"])
