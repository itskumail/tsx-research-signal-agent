"""Offline browser availability/cleanup tests; no broker or Chrome actions."""

import json
import subprocess
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from browser_research import (CanadianResearchBrowser, BrowserCommandError,
                              optional_browser_status, check_paper_session, browser_lock)


class BrowserTests(unittest.TestCase):
    def setUp(self):
        blocked = patch("browser_research.subprocess.run",
                        side_effect=AssertionError("Unexpected external subprocess in offline browser test"))
        guard = blocked.start()
        self.addCleanup(blocked.stop)
        # Fail even if a production fail-soft handler swallowed the exception.
        self.addCleanup(guard.assert_not_called)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = patch("browser_research.ROOT", Path(temp.name))
        root.start()
        self.addCleanup(root.stop)
        recovery = patch.dict("os.environ", {"OPENCLAW_BROWSER_AUTORESTART": "false",
                                              "OPENCLAW_PAPER_RECONNECT": "false",
                                              "OPENCLAW_PAPER_SAVED_LOGIN": "false"})
        recovery.start()
        self.addCleanup(recovery.stop)
        # Keep notification tests independent of the real clock's 10:05-10:58 window.
        reminder = patch("browser_research.options_reminder_due", return_value=None)
        reminder.start()
        self.addCleanup(reminder.stop)

    def make_browser(self):
        with patch.dict("os.environ", {"OPENCLAW_SSH_TARGET": "trader@192.0.2.16",
                                       "OPENCLAW_CLI_PATH": "/home/trader/bin/openclaw"}):
            return CanadianResearchBrowser()

    def test_status_uses_only_dedicated_profile_and_bounded_authenticated_ssh(self):
        browser = self.make_browser()
        response = {"profile": "canadian-stocks", "running": True, "cdpReady": True, "pageReady": True}
        with patch("browser_research.subprocess.run", return_value=Mock(
                returncode=0, stdout=json.dumps(response))) as run:
            self.assertTrue(browser.check()["available"])
        args = run.call_args.args[0]
        self.assertIn("BatchMode=yes", args)
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("--browser-profile canadian-stocks", args[-1])
        self.assertIn(" status ", args[-1])
        self.assertNotIn(" start ", args[-1])
        self.assertLessEqual(run.call_args.kwargs["timeout"], 35)
        self.assertGreater(run.call_args.kwargs["timeout"], 30)

    def test_transient_read_retried_once_without_private_diagnostics(self):
        browser = self.make_browser()
        response = {"profile": browser.PROFILE, "running": True, "cdpReady": True, "pageReady": True}
        with patch("browser_research.time.sleep"), patch("browser_research.subprocess.run", side_effect=[
                Mock(returncode=255, stderr="secret broker URL and account details"),
                Mock(returncode=0, stdout=json.dumps(response))]) as run:
            self.assertTrue(browser.check()["available"])
        self.assertEqual(run.call_count, 2)

    def test_failed_reads_record_only_safe_reason_and_step(self):
        browser = self.make_browser()
        with patch("browser_research.time.sleep"), patch("browser_research.subprocess.run", return_value=Mock(
                returncode=255, stderr="private token, URL, account number")) as run:
            state = browser.keepalive()
        self.assertEqual(state, {"status": "control_unavailable", "reason": "ssh_connection_failed",
                                 "failed_step": "status"})
        self.assertEqual(run.call_count, 2)
        self.assertNotIn("private", json.dumps(state))

    def test_navigation_is_not_retried_after_uncertain_result(self):
        browser = self.make_browser()
        with patch("browser_research.subprocess.run", return_value=Mock(
                returncode=1, stderr="RPC timeout with private URL")) as run:
            with self.assertRaises(BrowserCommandError):
                browser._command("navigate", "https://example.org/", "--target-id", "tab")
        self.assertEqual(run.call_count, 1)

    def test_profile_mismatch_is_not_retried(self):
        browser = self.make_browser()
        with patch("browser_research.subprocess.run", return_value=Mock(
                returncode=0, stdout='{"profile":"windows"}')) as run:
            self.assertEqual(browser.check()["reason"], "profile_mismatch")
        self.assertEqual(run.call_count, 1)

    def test_actual_closed_browser_is_distinct_from_control_failure(self):
        browser = self.make_browser()
        with patch.object(browser, "_command", return_value={"profile": browser.PROFILE, "running": False}):
            self.assertEqual(browser.keepalive()["status"], "browser_unavailable")

    def test_ready_or_mismatched_profile_is_not_recovered(self):
        browser = self.make_browser()
        for health in ({"available": True}, {"available": False, "reason": "profile_mismatch"}):
            with patch.object(browser, "check", return_value=health), \
                    patch("browser_launcher.ensure_local_browser") as launcher:
                self.assertEqual(browser.ensure_available(), health)
                launcher.assert_not_called()

    def test_recovery_rechecks_the_dedicated_profile(self):
        browser = self.make_browser()
        with patch.object(browser, "check", side_effect=[
                {"available": False, "reason": "cdp_not_ready"}, {"available": True}]) as check, \
                patch("browser_launcher.ensure_local_browser", return_value={"status": "started", "launched": True}):
            self.assertEqual(browser.ensure_available(), {"available": True, "browser_restarted": True})
            self.assertEqual(check.call_count, 2)

    def test_running_profile_with_failed_attachment_is_not_duplicated(self):
        browser = self.make_browser()
        with patch.object(browser, "check", return_value={"available": False, "reason": "cdp_not_ready"}), \
                patch("browser_launcher.ensure_local_browser", return_value={"status": "ready", "launched": False}), \
                patch.object(browser, "_command", return_value={"tabs": [{"targetId": "ibkr"}]}) as command:
            self.assertEqual(browser.ensure_available()["reason"], "attach_not_ready")
            command.assert_called_once_with("tabs")

    def test_empty_running_profile_opens_only_the_portal_entrypoint(self):
        browser = self.make_browser()
        with patch.object(browser, "check", side_effect=[
                {"available": False, "reason": "cdp_not_ready"},
                {"available": False, "reason": "cdp_not_ready"}, {"available": True}]), \
                patch("browser_launcher.ensure_local_browser", return_value={"status": "ready", "launched": False}), \
                patch.object(browser, "_command", side_effect=[{"tabs": []}, {"ok": True}]) as command:
            self.assertTrue(browser.ensure_available()["available"])
            self.assertEqual([c.args for c in command.call_args_list], [
                ("tabs",), ("open", "https://ndcdyn.interactivebrokers.com/portal/")])

    def test_failed_recovery_does_not_operate_browser(self):
        browser = self.make_browser()
        with patch.object(browser, "check", return_value={"available": False, "reason": "cdp_not_ready"}) as check, \
                patch("browser_launcher.ensure_local_browser", return_value={
                    "status": "failed", "reason": "local_endpoint_unknown"}), \
                patch.object(browser, "_command") as command:
            self.assertEqual(browser.ensure_available()["recovery_reason"], "local_endpoint_unknown")
            self.assertEqual(check.call_count, 1)
            command.assert_not_called()

    def test_public_research_does_not_require_broker_login(self):
        browser = self.make_browser()
        order = []
        def researcher(connected):
            order.append("research")
            return {"public": "evidence"}
        with patch.object(browser, "check", return_value={"available": True}), \
                patch.object(browser, "paper_session", side_effect=lambda: order.append("paper") or
                             {"status": "login_required"}), \
                patch.object(browser, "cleanup", side_effect=lambda: order.append("cleanup")), \
                patch.object(browser, "_portfolio_navigation") as navigate, patch("builtins.print"):
            self.assertEqual(browser.run_research(researcher), {"public": "evidence"})
        # Logged-out Paper never blocks research; the activity pass comes after it and clicks nothing.
        self.assertEqual(order, ["research", "cleanup", "paper"])
        navigate.assert_not_called()

    def test_research_ends_with_verified_paper_activity(self):
        browser = self.make_browser()
        with tempfile.TemporaryDirectory() as temp, \
                patch("browser_research.ROOT", Path(temp)), \
                patch.object(browser, "check", return_value={"available": True}), \
                patch.object(browser, "paper_session", return_value={"status": "ready"}), \
                patch.object(browser, "cleanup"), \
                patch.object(browser, "_portfolio_navigation", return_value=True) as navigate, \
                patch("builtins.print"):
            self.assertEqual(browser.run_research(lambda connected: {"ok": 1}), {"ok": 1})
            rows = (Path(temp) / "logs" / "browser_monitor_history.jsonl").read_text().splitlines()
        navigate.assert_called_once()
        self.assertEqual(json.loads(rows[-1])["source"], "session_research")

    def test_keepalive_waits_for_research_then_records_busy_if_still_held(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            lock = Path(temp) / "browser.lock"
            with patch("browser_research.ROOT", Path(temp)), browser_lock(lock) as held:
                self.assertTrue(held)
                with patch("browser_research.browser_lock",
                           side_effect=lambda wait_seconds=0: browser_lock(lock, wait_seconds=0.2, poll_seconds=0.1)) as lock_call:
                    self.assertEqual(check_paper_session(state_path=path, notify=False), {"status": "busy"})
                self.assertEqual(lock_call.call_args.kwargs["wait_seconds"], CanadianResearchBrowser.KEEPALIVE_LOCK_WAIT)
            row = json.loads(path.with_name("browser_monitor_history.jsonl").read_text().splitlines()[-1])
        self.assertEqual(row["status"], "busy")
        self.assertFalse(path.exists())

    def test_recovered_login_page_never_receives_keepalive_navigation(self):
        browser = self.make_browser()
        with patch.object(browser, "check", side_effect=[
                {"available": False, "reason": "cdp_not_ready"}, {"available": True}]), \
                patch("browser_launcher.ensure_local_browser", return_value={"status": "started", "launched": True}), \
                patch.object(browser, "_command", return_value={"tabs": [
                    {"targetId": "ibkr", "url": "https://ndcdyn.interactivebrokers.com/sso/Login"}]}) as command, \
                patch.object(browser, "_portfolio_navigation") as navigate:
            self.assertEqual(browser.keepalive(), {"status": "login_required"})
            command.assert_called_once_with("tabs")
            navigate.assert_not_called()

    @staticmethod
    def probe(current, x=300, y=45, ok=True, reason=None):
        result = {'ok': ok, 'current': current, 'x': x, 'y': y}
        if reason:
            result = {'ok': False, 'reason': reason}
        return {'result': result}

    def test_keepalive_really_clicks_positions_then_returns_to_dashboard(self):
        browser = self.make_browser()
        browser.keeper = "ibkr"
        with patch("browser_research.time.sleep"), \
                patch.object(browser, "check", return_value={"available": True}), \
                patch.object(browser, "paper_session", return_value={"status": "ready"}), \
                patch.object(browser, "_command", side_effect=[
                    self.NO_WARNING,
                    self.probe('Dashboard', 300, 45), {"ok": True}, self.probe('Positions'),
                    self.probe('Positions', 210, 45), {"ok": True}, self.probe('Dashboard')]) as command:
            self.assertEqual(browser.keepalive(), {"status": "ready", 'activity_verified': True})
        calls = [c.args for c in command.call_args_list]
        self.assertEqual([c for c in calls if c[0] == 'click-coords'], [
            ('click-coords', '300', '45', '--target-id', 'ibkr'),
            ('click-coords', '210', '45', '--target-id', 'ibkr')])
        self.assertNotIn('navigate', [c[0] for c in calls])
        self.assertIn('"Positions"', calls[1][-1])
        self.assertIn('"Dashboard"', calls[4][-1])

    NO_WARNING = {'result': {'present': False}}

    def test_probe_scripts_never_click_by_themselves_and_are_fully_expanded(self):
        for source in (CanadianResearchBrowser.TAB_PROBE, CanadianResearchBrowser.SESSION_PROBE):
            script = CanadianResearchBrowser._script(source)
            self.assertNotIn('.click(', script)
            self.assertIn('elementFromPoint', script)
            self.assertIn('paper trading account', script)
            for placeholder in ('SESSION_WARNING_FN', 'DIALOG_SELECTOR', 'STAY_LABELS'):
                self.assertNotIn(placeholder, script)

    def test_session_warning_stay_button_is_clicked_once_then_tabs(self):
        browser = self.make_browser()
        browser.keeper = "ibkr"
        with patch("browser_research.time.sleep"), \
                patch.object(browser, "check", return_value={"available": True}), \
                patch.object(browser, "paper_session", return_value={"status": "ready"}), \
                patch.object(browser, "_command", side_effect=[
                    {'result': {'present': True, 'ok': True, 'x': 640, 'y': 400}}, {"ok": True},
                    self.NO_WARNING,
                    self.probe('Dashboard'), {"ok": True}, self.probe('Positions'),
                    self.probe('Positions', 210, 45), {"ok": True}, self.probe('Dashboard')]) as command:
            self.assertEqual(browser.keepalive(), {"status": "ready", 'activity_verified': True,
                                                   'session_extended': True})
        clicks = [c.args for c in command.call_args_list if c.args[0] == 'click-coords']
        self.assertEqual(clicks[0], ('click-coords', '640', '400', '--target-id', 'ibkr'))
        self.assertEqual(len(clicks), 3)

    def test_unknown_session_button_or_uncleared_warning_never_retries(self):
        browser = self.make_browser()
        browser.keeper = "ibkr"
        unknown = {'result': {'present': True, 'ok': False, 'reason': 'session_warning_button_unknown'}}
        with patch.object(browser, "_command", return_value=unknown) as command:
            with self.assertRaisesRegex(BrowserCommandError, 'session_warning_button_unknown'):
                browser._portfolio_navigation()
        self.assertNotIn('click-coords', [c.args[0] for c in command.call_args_list])
        shown = {'result': {'present': True, 'ok': True, 'x': 5, 'y': 5}}
        with patch("browser_research.time.sleep"), \
                patch.object(browser, "_command", side_effect=[shown, {"ok": True}] + [shown] * 4) as command:
            with self.assertRaisesRegex(BrowserCommandError, 'session_warning_not_cleared'):
                browser._portfolio_navigation()
        self.assertEqual([c.args[0] for c in command.call_args_list].count('click-coords'), 1)

    def test_other_portal_page_returns_to_dashboard_before_clicking(self):
        browser = self.make_browser()
        browser.keeper = "ibkr"
        page = "https://ndcdyn.interactivebrokers.com/portal/?loginType=2#/quote/39984079/option/option.chain"
        with patch("browser_research.time.sleep"), \
                patch.object(browser, "check", return_value={"available": True}), \
                patch.object(browser, "paper_session", return_value={"status": "ready"}), \
                patch.object(browser, "_command", side_effect=[
                    self.NO_WARNING, self.probe(None, reason='tab_strip_not_unique'), {'result': page}, {"ok": True},
                    self.probe('Dashboard'), {"ok": True}, self.probe('Positions'),
                    self.probe('Positions', 210, 45), {"ok": True}, self.probe('Dashboard')]) as command:
            self.assertEqual(browser.keepalive(), {"status": "ready", 'activity_verified': True,
                                                   'returned_to_dashboard': True})
        navigations = [c.args for c in command.call_args_list if c.args[0] == 'navigate']
        self.assertEqual(navigations, [('navigate', page.split('#')[0] + '#/dashboard/', '--target-id', 'ibkr')])

    def test_return_to_dashboard_only_for_missing_tab_strip_on_portal(self):
        browser = self.make_browser()
        browser.keeper = "ibkr"
        with patch.object(browser, "_command", side_effect=[
                self.NO_WARNING, self.probe(None, reason='dialog_requires_attention')]) as command:
            with self.assertRaisesRegex(BrowserCommandError, 'dialog_requires_attention'):
                browser._portfolio_navigation()
        self.assertNotIn('navigate', [c.args[0] for c in command.call_args_list])
        with patch.object(browser, "_command", side_effect=[
                self.NO_WARNING, self.probe(None, reason='tab_strip_not_unique'),
                {'result': 'https://ndcdyn.interactivebrokers.com/sso/Login'}]) as command:
            with self.assertRaisesRegex(BrowserCommandError, 'paper_navigation_not_verified'):
                browser._portfolio_navigation()
        self.assertNotIn('navigate', [c.args[0] for c in command.call_args_list])

    def test_already_on_positions_still_clicks_both_tabs(self):
        browser = self.make_browser()
        browser.keeper = "ibkr"
        with patch("browser_research.time.sleep"), patch.object(browser, "_command", side_effect=[
                self.NO_WARNING,
                self.probe('Positions'), {"ok": True}, self.probe('Positions'),
                self.probe('Positions'), {"ok": True}, self.probe('Dashboard')]) as command:
            self.assertTrue(browser._portfolio_navigation())
        self.assertEqual([c.args[0] for c in command.call_args_list].count('click-coords'), 2)

    def test_unconfirmed_click_is_not_retried_or_counted_as_activity(self):
        browser = self.make_browser()
        browser.keeper = 'paper'
        with patch("browser_research.time.sleep"), patch.object(browser, '_command', side_effect=[
                self.NO_WARNING, self.probe('Dashboard'), {"ok": True}] + [self.probe('Dashboard')] * 4) as command:
            with self.assertRaisesRegex(BrowserCommandError, 'positions_click_unconfirmed'):
                browser._portfolio_navigation()
        self.assertEqual([c.args[0] for c in command.call_args_list].count('click-coords'), 1)

    def test_guard_denial_never_clicks(self):
        for reason in ('dialog_requires_attention', 'authentication_required',
                       'paper_navigation_not_verified', 'tab_strip_not_unique', 'tab_covered'):
            browser = self.make_browser()
            browser.keeper = 'paper'
            with self.subTest(reason=reason), \
                    patch.object(browser, '_command', return_value=self.probe(None, reason=reason)) as command:
                with self.assertRaisesRegex(BrowserCommandError, reason):
                    browser._portfolio_navigation()
                self.assertEqual(command.call_count, 1)

    def test_unsafe_probe_output_is_not_reported_or_clicked(self):
        browser = self.make_browser()
        browser.keeper = 'paper'
        for result in ({'result': {'ok': False, 'reason': 'Account U123 balance 5,000'}},
                       {'result': {'ok': True, 'current': 'Dashboard', 'x': '1; rm', 'y': 4}},
                       {'result': 'page text'}):
            with self.subTest(result=result), patch.object(browser, '_command', return_value=result) as command:
                with self.assertRaises(BrowserCommandError) as caught:
                    browser._portfolio_navigation()
                self.assertNotIn('U123', str(caught.exception))
                self.assertNotIn('click-coords', [c.args[0] for c in command.call_args_list])

    def test_portal_is_preferred_over_leftover_login_tab(self):
        browser = self.make_browser()
        browser.keeper = 'login'
        with patch.object(browser, '_command', side_effect=[{'tabs': [
                {'targetId': 'login', 'url': 'https://ndcdyn.interactivebrokers.com/sso/Login'},
                {'targetId': 'paper', 'url': 'https://ndcdyn.interactivebrokers.com/portal/'}]},
                {'result': 'This is a Paper Trading account for simulated trading.'}]):
            self.assertEqual(browser.paper_session()['status'], 'ready')
        self.assertEqual(browser.keeper, 'paper')

    def test_monitor_history_contains_only_safe_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(CanadianResearchBrowser, 'keepalive',
                return_value={'status': 'ready', 'activity_verified': True}), \
                patch('telegram_notify.send') as send:
            path = Path(temp) / 'state.json'
            check_paper_session(state_path=path, notify=False)
            check_paper_session(state_path=path, notify=False)
            rows = [json.loads(line) for line in path.with_name('browser_monitor_history.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]['activity_verified'])
        self.assertFalse(rows[0]['notify_enabled'])
        self.assertIn('previous_check_gap_seconds', rows[1])
        self.assertNotIn('url', rows[1])
        send.assert_not_called()

    def test_navigation_failure_keeps_verified_paper_state(self):
        browser = self.make_browser()
        with patch.object(browser, "check", return_value={"available": True}), \
                patch.object(browser, "paper_session", return_value={"status": "ready"}), \
                patch.object(browser, "_portfolio_navigation", side_effect=BrowserCommandError("command_timeout", "click")):
            self.assertEqual(browser.keepalive(), {"status": "navigation_failed", "paper_session_status": "ready",
                                                  "reason": "command_timeout", "failed_step": "click"})

    def test_reconnect_disabled_preserves_disconnected_read_only_navigation(self):
        browser = self.make_browser()
        with patch.object(browser, "check", return_value={"available": True}), \
                patch.object(browser, "paper_session", return_value={"status": "paper_disconnected"}), \
                patch.object(browser, "_portfolio_navigation") as navigate, \
                patch.object(browser, "_reconnect_paper") as reconnect:
            self.assertEqual(browser.keepalive(), {"status": "paper_disconnected"})
        reconnect.assert_not_called()
        navigate.assert_called_once()

    def test_enabled_reconnect_only_applies_to_disconnected_paper(self):
        for status in ("ready", "login_required", "paper_not_verified", "ibkr_tab_missing"):
            browser = self.make_browser()
            with self.subTest(status=status), patch.dict("os.environ", {"OPENCLAW_PAPER_RECONNECT": "true"}), \
                    patch.object(browser, "check", return_value={"available": True}), \
                    patch.object(browser, "paper_session", return_value={"status": status}), \
                    patch.object(browser, "_portfolio_navigation"), \
                    patch.object(browser, "_reconnect_paper") as reconnect:
                self.assertEqual(browser.keepalive(), {"status": status})
                reconnect.assert_not_called()

    def test_enabled_reconnect_success_continues_verified_portfolio_navigation(self):
        browser = self.make_browser()
        success = {"status": "ready", "reconnect_attempted": True, "reconnect_succeeded": True}
        with patch.dict("os.environ", {"OPENCLAW_PAPER_RECONNECT": "true"}), \
                patch.object(browser, "check", return_value={"available": True}), \
                patch.object(browser, "paper_session", side_effect=[{"status": "paper_disconnected"}, {"status": "ready"}]), \
                patch.object(browser, "_reconnect_paper", return_value=success) as reconnect, \
                patch.object(browser, "_portfolio_navigation") as navigate:
            self.assertEqual(browser.keepalive(), success)
        reconnect.assert_called_once()
        navigate.assert_called_once()

    def test_reconnect_guard_failure_never_clicks_or_navigates(self):
        browser = self.make_browser()
        browser.keeper = "ibkr"
        for reason in ("reconnect_not_portal", "reconnect_paper_not_verified", "reconnect_authentication_required",
                       "reconnect_dialog_requires_attention", "reconnect_competing_session",
                       "reconnect_warning_missing", "reconnect_control_not_unique"):
            with self.subTest(reason=reason), patch.object(browser, "_reconnect_probe", return_value={
                    "eligible": False, "attempted": False, "reason": reason}) as probe:
                self.assertEqual(browser._reconnect_paper()["reason"], reason)
                probe.assert_called_once_with()

    def test_reconnect_success_clears_persistent_attempt_block(self):
        browser = self.make_browser()
        browser.keeper = "ibkr"
        with patch.object(browser, "_reconnect_probe", side_effect=[
                {"eligible": True, "attempted": False}, {"eligible": True, "attempted": True}]) as probe, \
                patch.object(browser, "paper_session", return_value={"status": "ready"}):
            self.assertTrue(browser._reconnect_paper()["reconnect_succeeded"])
        self.assertEqual([c.kwargs for c in probe.call_args_list], [{}, {"click": True}])
        from browser_research import ROOT
        self.assertFalse(json.loads((ROOT / "logs/browser_reconnect_state.json").read_text())["blocked"])

    def test_uncertain_reconnect_is_not_retried_in_another_process(self):
        browser = self.make_browser()
        browser.keeper = "ibkr"
        with patch.object(browser, "_reconnect_probe", side_effect=[
                {"eligible": True}, BrowserCommandError("command_timeout", "evaluate")]) as probe:
            result = browser._reconnect_paper()
            self.assertEqual(result["reason"], "command_timeout")
            self.assertEqual(self.make_browser()._reconnect_paper()["reason"], "reconnect_attempt_exhausted")
            self.assertEqual(probe.call_count, 2)

    def test_reconnect_never_retries_mutating_evaluate(self):
        browser = self.make_browser()
        browser.keeper = "ibkr"
        with patch.object(browser, "_command") as observation, \
                patch.object(browser, "_command_once", side_effect=BrowserCommandError("command_timeout", "evaluate")) as once:
            with self.assertRaises(BrowserCommandError):
                browser._reconnect_probe(click=True)
        observation.assert_not_called()
        once.assert_called_once()
        self.assertIn("const perform = true", once.call_args.args[-1])

    def test_reconnect_stops_on_sso_or_unverified_result(self):
        for status in ("login_required", "paper_not_verified", "ibkr_tab_missing"):
            browser = self.make_browser()
            with self.subTest(status=status), patch.object(browser, "_reconnect_probe", side_effect=[
                    {"eligible": True}, {"eligible": True, "attempted": True}]) as probe, \
                    patch.object(browser, "paper_session", return_value={"status": status}) as session:
                self.assertEqual(browser._reconnect_paper(), {"status": status, "reconnect_attempted": True})
                self.assertEqual(probe.call_count, 2)
                session.assert_called_once()
            browser._write_reconnect_state(False)

    def test_reconnect_stops_when_click_revalidation_blocks(self):
        browser = self.make_browser()
        with patch.object(browser, "_reconnect_probe", side_effect=[
                {"eligible": True}, {"eligible": False, "attempted": False,
                                     "reason": "reconnect_authentication_required"}]), \
                patch.object(browser, "paper_session") as session:
            self.assertEqual(browser._reconnect_paper()["reason"], "reconnect_authentication_required")
        session.assert_not_called()

    def test_failed_reconnect_remains_blocked_until_verified_ready(self):
        browser = self.make_browser()
        with patch("browser_research.time.sleep"), patch.object(browser, "_reconnect_probe", side_effect=[
                {"eligible": True}, {"eligible": True, "attempted": True},
                {"eligible": True}, {"eligible": True}, {"eligible": True}]), \
                patch.object(browser, "paper_session", return_value={"status": "paper_disconnected"}) as session:
            self.assertEqual(browser._reconnect_paper()["reason"], "reconnect_not_restored")
            self.assertEqual(session.call_count, 3)
        self.assertEqual(browser._reconnect_paper()["reason"], "reconnect_attempt_exhausted")
        with patch.object(browser, "check", return_value={"available": True}), \
                patch.object(browser, "paper_session", return_value={"status": "ready"}), \
                patch.object(browser, "_portfolio_navigation"):
            self.assertEqual(browser.keepalive(), {"status": "ready"})
        from browser_research import ROOT
        self.assertFalse(json.loads((ROOT / "logs/browser_reconnect_state.json").read_text())["blocked"])

    def test_reconnect_state_write_failure_prevents_click(self):
        browser = self.make_browser()
        with patch.object(browser, "_reconnect_probe", return_value={"eligible": True}) as probe, \
                patch.object(browser, "_write_reconnect_state", side_effect=OSError("private detail")):
            result = browser._reconnect_paper()
        self.assertEqual(probe.call_count, 1)
        self.assertNotIn("private", json.dumps(result))

    def test_malformed_reconnect_state_never_authorizes_click(self):
        from browser_research import ROOT
        path = ROOT / "logs/browser_reconnect_state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        for saved in ([], {}, {"blocked": 1}):
            path.write_text(json.dumps(saved))
            browser = self.make_browser()
            with self.subTest(saved=saved), patch.object(browser, "_reconnect_probe") as probe:
                self.assertEqual(browser._reconnect_paper()["reason"], "reconnect_state_unavailable")
                probe.assert_not_called()

    def test_meaningfully_changed_failure_sends_new_notice(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(CanadianResearchBrowser, "keepalive", side_effect=[
                    {"status": "control_unavailable", "reason": "ssh_connection_failed", "failed_step": "status"},
                    {"status": "control_unavailable", "reason": "ssh_connection_failed", "failed_step": "status"},
                    {"status": "control_unavailable", "reason": "command_timeout", "failed_step": "status"}]), \
                patch("telegram_notify.send", return_value=True) as send:
            for _ in range(3):
                check_paper_session(state_path=Path(temp) / "state.json")
        self.assertEqual(send.call_count, 2)
        self.assertIn("does not establish", send.call_args.args[0])

    def test_changed_recovery_failure_is_not_hidden_by_previous_notice(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(CanadianResearchBrowser, "keepalive", side_effect=[
                    {"status": "browser_unavailable", "reason": "cdp_not_ready", "failed_step": "status",
                     "recovery_reason": "restart_cooldown"},
                    {"status": "browser_unavailable", "reason": "cdp_not_ready", "failed_step": "status",
                     "recovery_reason": "restart_cooldown"},
                    {"status": "browser_unavailable", "reason": "cdp_not_ready", "failed_step": "status",
                     "recovery_reason": "windows_login_required"}]), \
                patch("telegram_notify.send", return_value=True) as send:
            for _ in range(3):
                check_paper_session(state_path=Path(temp) / "state.json")
            self.assertEqual(send.call_count, 2)
            self.assertIn("windows_login_required", send.call_args.args[0])

    def test_unavailable_sources_fail_soft(self):
        for failure in (FileNotFoundError("ssh"), subprocess.TimeoutExpired("ssh", 20),
                        RuntimeError("gateway unavailable")):
            with self.subTest(failure=type(failure).__name__):
                browser = self.make_browser()
                with patch.object(browser, "_command", side_effect=failure):
                    self.assertFalse(browser.check()["available"])

    def test_closed_or_incomplete_browser_is_not_ready(self):
        browser = self.make_browser()
        for payload in ({"running": False}, {"running": True, "cdpReady": False},
                        {"running": True, "cdpReady": True, "pageReady": False}):
            with patch.object(browser, "_command", return_value=payload):
                self.assertFalse(browser.check()["available"])

    def test_malformed_response_is_unavailable(self):
        browser = self.make_browser()
        for output in ("not json", "[]", '{"ok":false}', '{"error":"failed"}'):
            with patch("browser_research.subprocess.run", return_value=Mock(returncode=0, stdout=output)):
                self.assertFalse(browser.check()["available"])

    def test_disabled_check_does_not_touch_browser(self):
        with patch.dict("os.environ", {"OPENCLAW_BROWSER_CHECK": "false"}), \
                patch("browser_research.CanadianResearchBrowser") as browser:
            self.assertEqual(optional_browser_status()["status"], "disabled")
            browser.assert_not_called()

    def test_invalid_configuration_is_optional(self):
        with patch.dict("os.environ", {"OPENCLAW_BROWSER_CHECK": "true",
                                       "OPENCLAW_SSH_TARGET": "-oProxyCommand=bad"}):
            self.assertFalse(optional_browser_status()["available"])

    def test_health_check_alone_does_not_close_tabs(self):
        browser = self.make_browser()
        with patch.object(browser, "_command", return_value={"profile": "canadian-stocks",
                                                            "running": True, "cdpReady": True,
                                                            "pageReady": True}) as command:
            browser.check()
        command.assert_called_once_with("status")

    def test_research_cleans_up_on_success_and_failure(self):
        for fails in (False, True):
            browser = self.make_browser()
            with patch.object(browser, "check", return_value={"available": True}), \
                    patch.object(browser, "cleanup") as cleanup, \
                    patch.object(browser, "_activity_after_research") as activity:
                def research():
                    with browser.research() as connected:
                        self.assertIs(connected, browser)
                        if fails:
                            raise RuntimeError("research failed")
                if fails:
                    with self.assertRaisesRegex(RuntimeError, "research failed"):
                        research()
                else:
                    research()
                cleanup.assert_called_once()
                activity.assert_called_once()

    def test_no_cleanup_when_browser_research_is_skipped(self):
        browser = self.make_browser()
        with patch.object(browser, "check", return_value={"available": False}), \
                patch.object(browser, "cleanup") as cleanup:
            with browser.research() as connected:
                self.assertIsNone(connected)
            cleanup.assert_not_called()

    def test_cleanup_failure_does_not_mask_research_failure(self):
        browser = self.make_browser()
        with patch.object(browser, "check", return_value={"available": True}), \
                patch.object(browser, "cleanup", side_effect=OSError("cleanup failed")), \
                patch.object(browser, "_activity_after_research") as activity:
            with self.assertRaisesRegex(RuntimeError, "research failed"):
                with browser.research():
                    raise RuntimeError("research failed")
        activity.assert_called_once()

    def test_cleanup_closes_all_original_tabs_and_preserves_blank(self):
        browser = self.make_browser()
        with patch.object(browser, "_command", side_effect=[
                {"tabs": [{"targetId": "ibkr-tab"}, {"targetId": "chart-tab"}]},
                {"targetId": "blank-tab"}, {"ok": True}, {"ok": True},
                {"tabs": [{"targetId": "blank-tab"}]},
        ]) as command:
            self.assertEqual(browser.cleanup(), {"closed": 2, "complete": True})
        self.assertEqual([call.args for call in command.call_args_list],
                         [("tabs",), ("open", "about:blank"), ("close", "ibkr-tab"),
                          ("close", "chart-tab"), ("tabs",)])

    def test_blank_failure_does_not_close_last_browser_tab(self):
        browser = self.make_browser()
        with patch.object(browser, "_command", side_effect=[
                {"tabs": [{"targetId": "ibkr-tab"}]}, RuntimeError("blank failed"),
        ]) as command:
            self.assertFalse(browser.cleanup()["complete"])
        self.assertFalse(any(call.args[0] == "close" for call in command.call_args_list))

    def test_close_failure_still_attempts_other_tabs_and_reports_incomplete(self):
        browser = self.make_browser()
        with patch.object(browser, "_command", side_effect=[
                {"tabs": [{"targetId": "ibkr-tab"}, {"targetId": "chart-tab"}]},
                {"targetId": "blank-tab"}, RuntimeError("close failed"), {"ok": True},
                {"tabs": [{"targetId": "ibkr-tab"}, {"targetId": "blank-tab"}]},
        ]):
            self.assertEqual(browser.cleanup(), {"closed": 1, "complete": False})

    def test_other_browser_profile_is_rejected_before_closing_tabs(self):
        browser = self.make_browser()
        with patch("browser_research.subprocess.run", side_effect=[
                Mock(returncode=0, stdout=json.dumps({"tabs": [{"targetId": "claw-tab"}]})),
                Mock(returncode=0, stdout=json.dumps({"targetId": "blank-tab", "resolvedProfile": "windows"})),
        ]) as run:
            self.assertFalse(browser.cleanup()["complete"])
        self.assertEqual(run.call_count, 2)
        self.assertFalse(any(" close " in call.args[0][-1] for call in run.call_args_list))

    def test_cleanup_deadline_is_bounded(self):
        browser = self.make_browser()
        with patch("browser_research.time.monotonic", side_effect=[0, 0, 91]), \
                patch.object(browser, "_command", return_value={"tabs": [{"targetId": "ibkr-tab"}]}) as command:
            self.assertFalse(browser.cleanup()["complete"])
        command.assert_called_once_with("tabs", timeout=90)

    def test_optional_research_returns_evidence_or_empty_without_blocking(self):
        browser = self.make_browser()
        with patch.object(browser, "check", return_value={"available": True}), \
                patch.object(browser, "cleanup") as cleanup, \
                patch.object(browser, "_activity_after_research") as activity:
            self.assertEqual(browser.run_research(lambda connected: {"TOU": "observed facts"}),
                             {"TOU": "observed facts"})
            self.assertEqual(browser.run_research(Mock(side_effect=RuntimeError("research failed"))), {})
            self.assertEqual(cleanup.call_count, 2)
            self.assertEqual(activity.call_count, 2)

    def test_deferred_research_does_not_probe_or_close_manual_tabs(self):
        browser = self.make_browser()
        with patch.object(browser, "check") as check, patch.object(browser, "cleanup") as cleanup:
            self.assertEqual(browser.run_research(), {})
            check.assert_not_called()
            cleanup.assert_not_called()

    def test_cleanup_preserves_existing_ibkr_and_does_not_open_blank(self):
        browser = self.make_browser()
        with patch.object(browser, "_command", side_effect=[
                {"tabs": [{"targetId": "ibkr", "url": "https://ndcdyn.interactivebrokers.com/portal/"},
                          {"targetId": "chart", "url": "https://www.m-x.ca/"}]},
                {"ok": True}, {"tabs": [{"targetId": "ibkr"}]}]) as command:
            self.assertEqual(browser.cleanup(), {"closed": 1, "complete": True})
        self.assertEqual([c.args for c in command.call_args_list], [("tabs",), ("close", "chart"), ("tabs",)])

    def test_login_page_is_not_inspected_or_operated(self):
        browser = self.make_browser()
        with patch.object(browser, "_command", return_value={"tabs": [
                {"targetId": "ibkr", "url": "https://ndcdyn.interactivebrokers.com/sso/Login"}]}) as command:
            self.assertEqual(browser.paper_session()["status"], "login_required")
        command.assert_called_once_with("tabs")

    def test_saved_login_opt_out_never_operates_authentication(self):
        browser = self.make_browser()
        with patch.object(browser, 'ensure_available', return_value={'available': True}), \
                patch.object(browser, 'paper_session', return_value={'status': 'login_required'}), \
                patch('paper_login.attempt') as attempt:
            self.assertEqual(browser.keepalive()['status'], 'login_required')
        attempt.assert_not_called()

    def test_saved_login_opt_in_returns_verified_paper_before_navigation(self):
        browser = self.make_browser()
        with patch.dict('os.environ', {'OPENCLAW_PAPER_SAVED_LOGIN': 'true'}), \
                patch.object(browser, 'ensure_available', return_value={'available': True}), \
                patch.object(browser, 'paper_session', side_effect=[{'status': 'login_required'}, {'status': 'ready'}]), \
                patch('paper_login.attempt', return_value={'status': 'ready', 'saved_login_attempted': True,
                                                           'saved_login_succeeded': True}) as attempt, \
                patch.object(browser, '_portfolio_navigation', return_value=True) as navigate:
            state = browser.keepalive()
        attempt.assert_called_once()
        navigate.assert_called_once()
        self.assertEqual(state['status'], 'ready')
        self.assertTrue(state['saved_login_succeeded'])

    def test_saved_login_denial_preserves_failure_without_portfolio_actions(self):
        browser = self.make_browser()
        failure = {'status': 'login_required', 'reason': 'paper_login_saved_fields_missing',
                   'failed_step': 'paper_saved_login'}
        with patch.dict('os.environ', {'OPENCLAW_PAPER_SAVED_LOGIN': 'true'}), \
                patch.object(browser, 'ensure_available', return_value={'available': True}), \
                patch.object(browser, 'paper_session', return_value={'status': 'login_required'}), \
                patch('paper_login.attempt', return_value=failure), \
                patch.object(browser, '_portfolio_navigation') as navigate:
            self.assertEqual(browser.keepalive(), failure)
        navigate.assert_not_called()

    def test_live_or_unverified_account_is_not_navigated(self):
        browser = self.make_browser()
        with patch.object(browser, "check", return_value={"available": True}), \
                patch.object(browser, "paper_session", return_value={"status": "paper_not_verified"}), \
                patch.object(browser, "_command") as command:
            self.assertEqual(browser.keepalive()["status"], "paper_not_verified")
        command.assert_not_called()

    def test_keepalive_notification_is_deduplicated_and_recovery_reported(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(CanadianResearchBrowser, "keepalive", side_effect=[
                    {"status": "login_required"}, {"status": "login_required"}, {"status": "ready"}]), \
                patch("telegram_notify.send", return_value=True) as send:
            path = Path(temp) / "state.json"
            for _ in range(3):
                check_paper_session(state_path=path)
            self.assertEqual(send.call_count, 2)
            state = json.loads(path.read_text())
            self.assertEqual(state["notified_status"], "ready")
            self.assertEqual(set(state), {"status", "checked_at", "notified_status"})

    def test_failed_notification_is_retried(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(CanadianResearchBrowser, "keepalive", return_value={"status": "login_required"}), \
                patch("telegram_notify.send", side_effect=[False, True]) as send:
            for _ in range(2):
                check_paper_session(state_path=Path(temp) / "state.json")
            self.assertEqual(send.call_count, 2)

    def test_browser_lock_excludes_another_worker_and_releases(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "browser.lock"
            with browser_lock(path) as first:
                with browser_lock(path) as second:
                    self.assertTrue(first)
                    self.assertFalse(second)
            with browser_lock(path) as third:
                self.assertTrue(third)


class OptionsLoginReminderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = patch('browser_research.ROOT', Path(temp.name))
        root.start()
        self.addCleanup(root.stop)

    """Once-per-trading-day reminder before the 11:00 Session 2 options scan."""

    @staticmethod
    def at(text):
        from zoneinfo import ZoneInfo
        from datetime import datetime
        return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo("America/Denver"))

    def test_due_only_when_logged_out_in_window_on_trading_day_once(self):
        from browser_research import options_reminder_due
        friday = self.at("2026-10-16T10:30")
        self.assertEqual(options_reminder_due("login_required", {}, friday), "2026-10-16")
        self.assertEqual(options_reminder_due("ibkr_tab_missing", {}, self.at("2026-10-16T10:05")), "2026-10-16")
        for status, previous, now in (
                ("ready", {}, friday),
                ("navigation_failed", {}, friday),
                ("login_required", {"options_reminder_date": "2026-10-16"}, friday),
                ("login_required", {}, self.at("2026-10-16T10:04")),
                ("login_required", {}, self.at("2026-10-16T10:58")),
                ("login_required", {}, self.at("2026-10-17T10:30"))):   # Saturday
            with self.subTest(status=status, now=now, previous=previous):
                self.assertIsNone(options_reminder_due(status, previous, now))

    def test_reminder_is_sent_once_and_remembered_but_never_without_notify(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(CanadianResearchBrowser, "keepalive", return_value={"status": "login_required"}), \
                patch("browser_research.options_reminder_due", return_value="2026-10-16"), \
                patch("telegram_notify.send", return_value=True) as send:
            path = Path(temp) / "state.json"
            state = check_paper_session(state_path=path)
            self.assertEqual(state["options_reminder_date"], "2026-10-16")
            self.assertIn("log in before then", send.call_args_list[-1].args[0])
            history = json.loads(path.with_name("browser_monitor_history.jsonl").read_text().splitlines()[-1])
            self.assertTrue(history["options_reminder_sent"])
            send.reset_mock()
            check_paper_session(state_path=path, notify=False)
            send.assert_not_called()
            self.assertEqual(json.loads(path.read_text())["options_reminder_date"], "2026-10-16")


if __name__ == "__main__":
    unittest.main()
