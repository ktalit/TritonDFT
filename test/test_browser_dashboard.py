import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from browser_dashboard import DASHBOARD_API_VERSION, DashboardStore, _resolve_file, _safe_files, create_app, render_workflow_plot, session_snapshot
from dashboard_mode import dashboard_mode
from dashboard_activity import append_activity, read_activity, sanitize_activity_text


class BrowserDashboardStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "sessions.sqlite3"
        self.store = DashboardStore(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def session(self, name="run", ttl=60):
        run = self.root / name
        run.mkdir()
        session_id, token = self.store.create_session(run, name, ttl)
        return run, session_id, token

    def test_session_tokens_and_ids_are_unique(self):
        _r1, id1, token1 = self.session("one")
        _r2, id2, token2 = self.session("two")
        self.assertNotEqual(id1, id2)
        self.assertNotEqual(token1, token2)
        self.assertGreaterEqual(len(token1), 40)

    def test_invalid_token_is_rejected_and_sessions_are_isolated(self):
        run1, _id1, token1 = self.session("one")
        run2, _id2, token2 = self.session("two")
        (run1 / "one.in").write_text("one", encoding="utf-8")
        (run2 / "two.in").write_text("two", encoding="utf-8")
        self.assertIsNone(self.store.get("not-a-session-token"))
        files1 = {item["name"] for item in session_snapshot(self.store, token1)["files"]}
        files2 = {item["name"] for item in session_snapshot(self.store, token2)["files"]}
        self.assertEqual(files1, {"one.in"})
        self.assertEqual(files2, {"two.in"})

    def test_file_discovery_rejects_symlink_escape_and_unknown_file_id(self):
        run, _session_id, token = self.session()
        outside = self.root / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        (run / "safe.in").write_text("safe", encoding="utf-8")
        try:
            (run / "escape.in").symlink_to(outside)
        except OSError:
            pass
        session = self.store.get(token)
        self.assertEqual([item["name"] for item in _safe_files(run)], ["safe.in"])
        with self.assertRaises(Exception):
            _resolve_file(session, "../../etc/passwd")

    def test_expiration_and_explicit_close(self):
        _run, _session_id, token = self.session(ttl=-1)
        self.assertIsNone(self.store.get(token))
        self.assertGreaterEqual(self.store.cleanup(), 1)
        _run2, _id2, token2 = self.session("two")
        self.assertTrue(self.store.close(token2))
        self.assertIsNone(self.store.get(token2))

    def test_atomic_first_decision_wins(self):
        _run, _session_id, token = self.session()
        self.store.publish_approval(token, {"plan": "test", "editable_files": []})
        self.assertTrue(self.store.submit_decision(token, {"action": "approve"}))
        self.assertFalse(self.store.submit_decision(token, {"action": "cancel"}))
        self.assertEqual(self.store.take_decision(token)["action"], "approve")

    def test_browser_app_can_be_constructed(self):
        app = create_app(self.db)
        self.assertEqual(app.title, "TritonDFT experimental browser dashboard")
        paths = {route.path for route in app.routes}
        self.assertIn("/s/{token}", paths)
        self.assertIn("/ws/{token}", paths)
        self.assertIn("/api/s/{token}/plot/{kind}", paths)
        self.assertIn("/api/s/{token}/questions", paths)
        self.assertIn("/api/s/{token}/structure.cif", paths)
        self.assertIn("/api/s/{token}/structure/open-vesta", paths)
        self.assertIn("/api/s/{token}/structure/vesta-location", paths)
        self.assertIn("/api/s/{token}/structure/open-folder", paths)

    def test_health_identifies_dashboard_code_version(self):
        response = TestClient(create_app(self.db)).get("/dashboard-healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["api_version"], DASHBOARD_API_VERSION)

    def test_dashboard_page_javascript_parses(self):
        if not shutil.which("node"):
            self.skipTest("Node.js is not installed")
        _run, _session_id, token = self.session("javascript")
        page = TestClient(create_app(self.db)).get(f"/s/{token}").text
        self.assertIn("Generated inputs", page)
        self.assertIn("reviewApprovalFile", page)
        match = re.search(r"<script>(.*)</script>", page, flags=re.DOTALL)
        self.assertIsNotNone(match)
        checked = subprocess.run(
            ["node", "--check"], input=match.group(1), text=True,
            capture_output=True, check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_structure_actions_require_a_valid_session(self):
        client = TestClient(create_app(self.db))
        for suffix in ("open-vesta", "open-folder"):
            self.assertEqual(client.post(f"/api/s/not-valid/structure/{suffix}").status_code, 404)
        response = client.post(
            "/api/s/not-valid/structure/vesta-location", json={"path": "/Applications/VESTA.app"}
        )
        self.assertEqual(response.status_code, 404)

    def test_activity_is_sanitized_and_terminal_wait_is_exposed(self):
        run, _session_id, token = self.session("activity")
        append_activity(
            run, "SSH authentication required",
            "password=hunter2 TOTP: 123456 OPENAI_API_KEY=secret /s/abcdefghijklmnopqrstuvwxyz123456",
            "waiting_terminal",
        )
        snapshot = session_snapshot(self.store, token)
        self.assertTrue(snapshot["terminal_required"])
        encoded = str(snapshot["activity"])
        for secret in ("hunter2", "123456", "secret", "abcdefghijklmnopqrstuvwxyz123456"):
            self.assertNotIn(secret, encoded)
        self.assertIn("[REDACTED]", encoded)
        append_activity(run, "SSH authentication completed", "ready", "success")
        self.assertFalse(session_snapshot(self.store, token)["terminal_required"])

    def test_activity_reader_ignores_malformed_records(self):
        run, _session_id, _token = self.session("activity-malformed")
        (run / "dashboard_activity.jsonl").write_text("not-json\n", encoding="utf-8")
        self.assertEqual(read_activity(run), [])
        self.assertNotIn("value", sanitize_activity_text("TOTP: value"))

    def test_http_page_file_edit_and_cross_session_rejection(self):
        run, _session_id, token = self.session("one")
        target = run / "scf.in"
        target.write_text("original\n", encoding="utf-8")
        _other_run, _other_id, other_token = self.session("two")
        self.store.publish_approval(token, {
            "plan": "review",
            "review_stage": "inputs",
            "editable_files": ["scf.in"],
        })
        client = TestClient(create_app(self.db))
        self.assertEqual(client.get("/s/not-valid").status_code, 404)
        self.assertEqual(client.get(f"/s/{token}").status_code, 200)
        snapshot = client.get(f"/api/s/{token}").json()
        file_id = snapshot["files"][0]["id"]
        opened = client.get(f"/api/s/{token}/file/{file_id}").json()
        self.assertTrue(opened["editable"])
        saved = client.put(
            f"/api/s/{token}/file/{file_id}",
            json={"content": "changed", "expected_sha256": opened["sha256"]},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(target.read_text(encoding="utf-8"), "changed\n")
        self.assertEqual(client.get(f"/api/s/{other_token}/file/{file_id}").status_code, 404)
        with client.websocket_connect(f"/ws/{token}") as websocket:
            self.assertEqual(websocket.receive_json()["session_id"], snapshot["session_id"])

    def test_question_results_are_session_scoped(self):
        _run, _session_id, token = self.session("one")
        _other_run, _other_id, other_token = self.session("two")
        question_id = self.store.create_question(token, "What is the energy?")
        self.store.complete_question(question_id, "answer", "verified")
        self.assertEqual(self.store.get_question(token, question_id)["answer"], "answer")
        self.assertIsNone(self.store.get_question(other_token, question_id))

    def test_headless_band_plot_uses_saved_workflow_data(self):
        run, _session_id, _token = self.session("plot")
        (run / "sample.band.gnu").write_text("0 1\n1 2\n\n0 2\n1 3\n", encoding="utf-8")
        data = render_workflow_plot(run, "bands", {
            "reference": "Absolute", "xmin": "", "xmax": "",
            "ymin": "", "ymax": "", "fwhm": "8",
        })
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))


class DashboardModeTests(unittest.TestCase):
    def test_x11_is_default_and_invalid_values_fail_safe_to_x11(self):
        self.assertEqual(dashboard_mode({}), "x11")
        self.assertEqual(dashboard_mode({"TRITONDFT_DASHBOARD_MODE": "invalid"}), "x11")

    def test_explicit_modes(self):
        for mode in ("x11", "browser", "both"):
            self.assertEqual(dashboard_mode({"TRITONDFT_DASHBOARD_MODE": mode}), mode)

    def test_user_friendly_mode_aliases(self):
        self.assertEqual(dashboard_mode({"TRITONDFT_DASHBOARD_MODE": "xwindows"}), "x11")
        self.assertEqual(dashboard_mode({"TRITONDFT_DASHBOARD_MODE": "x-windows"}), "x11")
        self.assertEqual(dashboard_mode({"TRITONDFT_DASHBOARD_MODE": "https"}), "browser")

    def test_x11_does_not_initialize_browser_and_both_initializes_each(self):
        import cluster_agent
        with patch.dict(os.environ, {"TRITONDFT_DASHBOARD_MODE": "x11"}), \
             patch.object(cluster_agent, "_launch_workflow_monitor", return_value=True) as x11, \
             patch.object(cluster_agent, "_browser_dashboard_session") as browser:
            self.assertTrue(cluster_agent._launch_configured_dashboard(self.root if hasattr(self, "root") else "."))
            x11.assert_called_once()
            browser.assert_not_called()
        with patch.dict(os.environ, {"TRITONDFT_DASHBOARD_MODE": "both"}), \
             patch.object(cluster_agent, "_launch_workflow_monitor", return_value=True) as x11, \
             patch.object(cluster_agent, "_browser_dashboard_session") as browser:
            browser.return_value.publish_monitor.return_value = None
            self.assertTrue(cluster_agent._launch_configured_dashboard("."))
            x11.assert_called_once()
            browser.assert_called_once()


class WorkflowBrowserCliTests(unittest.TestCase):
    def test_local_browser_cli_defaults_to_browser_without_cluster_setup(self):
        import workflow_browser_cli
        with patch.object(sys, "argv", ["workflow_browser_cli.py"]), \
             patch("builtins.input", side_effect=["quit"]), \
             patch.object(workflow_browser_cli, "_discover_workflows", return_value=[]) as discover, \
             patch.object(workflow_browser_cli, "_launch_configured_dashboard") as launch, \
             patch.dict(os.environ, {}, clear=False):
            workflow_browser_cli.main()
            self.assertEqual(os.environ["TRITONDFT_DASHBOARD_MODE"], "browser")
            discover.assert_called_once_with("tmp")
            launch.assert_not_called()

    def test_number_is_a_shortcut_for_open_number(self):
        import workflow_browser_cli
        with patch.object(sys, "argv", ["workflow_browser_cli.py"]), \
             patch("builtins.input", side_effect=["2", "quit"]), \
             patch.object(workflow_browser_cli, "_discover_workflows", return_value=[]), \
             patch.object(workflow_browser_cli, "_resolve_workflow_to_open", return_value="tmp/run-two") as resolve, \
             patch.object(workflow_browser_cli, "_launch_configured_dashboard") as launch:
            workflow_browser_cli.main()
            resolve.assert_called_once_with("2", "tmp")
            launch.assert_called_once_with("tmp/run-two")


if __name__ == "__main__":
    unittest.main()
