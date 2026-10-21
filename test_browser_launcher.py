"""Offline recovery tests; no actual Chrome launches or broker requests."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import browser_launcher as launcher


class LauncherTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.chrome = self.root / "chrome.exe"
        self.chrome.touch()
        self.profile = self.root / "Profile"
        self.profile.mkdir()
        self.state = self.root / "logs" / "recovery.json"
        for patcher in (patch.dict("os.environ", {"OPENCLAW_BROWSER_AUTORESTART": "true"}),
                        patch("browser_launcher._paths", return_value=(self.chrome, self.profile)),
                        patch("browser_launcher.subprocess.Popen")):
            started = patcher.start()
            self.addCleanup(patcher.stop)
            if isinstance(started, Mock):
                self.popen = started

    def recover(self):
        return launcher.ensure_local_browser(state_path=self.state)

    def test_disabled_recovery_does_not_probe_or_launch(self):
        with patch.dict("os.environ", {"OPENCLAW_BROWSER_AUTORESTART": "false"}), \
                patch("browser_launcher._endpoint_state") as endpoint:
            self.assertEqual(self.recover(), {"status": "disabled"})
            endpoint.assert_not_called()
        self.popen.assert_not_called()

    def test_existing_endpoint_is_a_noop(self):
        with patch("browser_launcher._endpoint_state", return_value="ready"), \
                patch("browser_launcher._process_state") as process:
            self.assertEqual(self.recover(), {"status": "ready", "launched": False})
            process.assert_not_called()
        self.popen.assert_not_called()

    def test_uncertain_or_invalid_endpoint_never_launches(self):
        for status in ("unknown", "invalid"):
            with self.subTest(status=status), patch("browser_launcher._endpoint_state", return_value=status), \
                    patch("browser_launcher._process_state") as process:
                self.assertEqual(self.recover()["reason"], "local_endpoint_unknown")
                process.assert_not_called()
        self.popen.assert_not_called()

    def test_missing_installation_or_existing_profile_never_launches(self):
        for chrome, profile in ((self.root / "missing.exe", self.profile),
                                (self.chrome, self.root / "missing-profile")):
            with patch("browser_launcher._paths", return_value=(chrome, profile)), \
                    patch("browser_launcher._endpoint_state", return_value="closed"):
                self.assertEqual(self.recover()["reason"], "launcher_or_profile_missing")
        self.popen.assert_not_called()

    def test_confirmed_closed_browser_launches_only_expected_profile(self):
        with patch("browser_launcher._endpoint_state", side_effect=["closed", "ready"]), \
                patch("browser_launcher._process_state", return_value={
                    "profile_running": False, "interactive_session": True}):
            self.assertEqual(self.recover(), {"status": "started", "launched": True})
        self.popen.assert_called_once()
        self.assertEqual(self.popen.call_args.args[0], [
            str(self.chrome), "--remote-debugging-port=9224", "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={self.profile}", "--no-first-run",
            "https://ndcdyn.interactivebrokers.com/portal/"])
        self.assertEqual(self.popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(self.popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(set(json.loads(self.state.read_text())), {"attempted_at"})

    def test_existing_profile_process_never_gets_killed_or_duplicated(self):
        with patch("browser_launcher._endpoint_state", return_value="closed"), \
                patch("browser_launcher._process_state", return_value={
                    "profile_running": True, "interactive_session": True}):
            self.assertEqual(self.recover()["reason"], "profile_already_running")
        self.popen.assert_not_called()

    def test_process_check_failure_is_not_proof_of_absence(self):
        with patch("browser_launcher._endpoint_state", return_value="closed"), \
                patch("browser_launcher._process_state", side_effect=RuntimeError("private diagnostics")):
            self.assertEqual(self.recover(), {"status": "failed", "reason": "local_process_check_failed"})
        self.popen.assert_not_called()

    def test_background_service_session_does_not_launch_gui(self):
        with patch("browser_launcher._endpoint_state", return_value="closed"), \
                patch("browser_launcher._process_state", return_value={
                    "profile_running": False, "interactive_session": False}):
            self.assertEqual(self.recover()["reason"], "windows_login_required")
        self.popen.assert_not_called()

    def test_failed_attempt_is_cooled_down(self):
        with patch("browser_launcher._endpoint_state", return_value="closed"), \
                patch("browser_launcher._process_state", return_value={
                    "profile_running": False, "interactive_session": True}):
            self.popen.side_effect = OSError("private launch diagnostics")
            self.assertEqual(self.recover()["reason"], "chrome_launch_failed")
            self.assertEqual(self.recover(), {"status": "cooldown", "reason": "restart_cooldown"})
        self.popen.assert_called_once()

    def test_startup_wait_has_a_deadline(self):
        with patch("browser_launcher._endpoint_state", return_value="closed"), \
                patch("browser_launcher._process_state", return_value={
                    "profile_running": False, "interactive_session": True}), \
                patch("browser_launcher.time.monotonic", side_effect=[0, 16]):
            self.assertEqual(self.recover(), {"status": "failed", "reason": "chrome_start_not_ready", "launched": True})
        self.popen.assert_called_once()

    def test_local_endpoint_accepts_only_chrome_on_expected_port(self):
        for port, browser, expected in ((9224, "Chrome/142.0", "ready"),
                                        (9222, "Chrome/142.0", "invalid"),
                                        (9224, "Other/1.0", "invalid")):
            with self.subTest(port=port, browser=browser):
                connection = Mock()
                connection.getresponse.return_value = Mock(status=200, read=Mock(return_value=json.dumps({
                    "Browser": browser, "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/browser/id"}).encode()))
                with patch("browser_launcher.http.client.HTTPConnection", return_value=connection):
                    self.assertEqual(launcher._endpoint_state(), expected)
                connection.request.assert_called_once_with("GET", "/json/version")
                connection.close.assert_called_once()

    def test_endpoint_refusal_and_timeout_are_distinguished(self):
        for error, expected in ((ConnectionRefusedError(), "closed"), (TimeoutError(), "unknown"),
                                (PermissionError(), "unknown")):
            connection = Mock()
            connection.request.side_effect = error
            with patch("browser_launcher.http.client.HTTPConnection", return_value=connection):
                self.assertEqual(launcher._endpoint_state(), expected)

    def test_process_query_returns_only_validated_booleans(self):
        response = {"profile_running": True, "interactive_session": True}
        with patch("browser_launcher.subprocess.run", return_value=Mock(
                returncode=0, stdout=json.dumps(response))) as run:
            self.assertEqual(launcher._process_state(self.profile), response)
        self.assertEqual(run.call_args.kwargs["env"]["CANADIAN_CHROME_PROFILE"], str(self.profile))
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        self.assertIn("Get-CimInstance Win32_Process", run.call_args.args[0][-1])
        self.assertNotIn("Stop-Process", run.call_args.args[0][-1])
        for output in ('{"profile_running":"false","interactive_session":true}', "[]", "not json"):
            with patch("browser_launcher.subprocess.run", return_value=Mock(returncode=0, stdout=output)):
                with self.assertRaises(ValueError):
                    launcher._process_state(self.profile)


if __name__ == "__main__":
    unittest.main()
