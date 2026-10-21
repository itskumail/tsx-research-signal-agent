"""Offline saved-Paper-form orchestration tests; no real browser or credentials."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from browser_research import BrowserCommandError, CanadianResearchBrowser
import paper_login


def result(action, attempted=False):
    return {'eligible': True, 'attempted': attempted, 'action': action}


class SavedPaperLoginTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'login-state.json'
        self.browser = Mock(deadline=None, COMMAND_TIMEOUT=35, keeper='paper-tab')
        env = patch.dict('os.environ', {'OPENCLAW_PAPER_SAVED_LOGIN': 'true'})
        env.start()
        self.addCleanup(env.stop)
        blocked = patch('paper_login.time.sleep')
        blocked.start()
        self.addCleanup(blocked.stop)

    def test_live_mode_selects_paper_then_submits_only_after_durable_block(self):
        actions = []
        def probe(browser, action=None):
            if action:
                self.assertIs(json.loads(self.path.read_text())['blocked'], True)
                actions.append(action)
                return result(action, True)
            return result('select_paper')
        self.browser.paper_session.return_value = {'status': 'ready'}
        with patch.object(paper_login, 'probe', side_effect=probe):
            state = paper_login.attempt(self.browser, self.path)
        self.assertEqual(actions, ['select_paper', 'submit_paper'])
        self.assertTrue(state['saved_login_succeeded'])
        self.assertEqual(state['status'], 'ready')
        self.assertFalse(json.loads(self.path.read_text())['blocked'])
        self.assertEqual(set(json.loads(self.path.read_text())), {'blocked', 'checked_at'})
        self.assertIsNone(self.browser.deadline)

    def test_disabled_mode_never_inspects_credentials_or_clicks(self):
        with patch.dict('os.environ', {'OPENCLAW_PAPER_SAVED_LOGIN': 'false'}), \
                patch.object(paper_login, 'probe') as probe:
            self.assertEqual(paper_login.attempt(self.browser, self.path)['reason'], 'paper_login_disabled')
        probe.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_already_paper_never_toggles_back_to_live(self):
        self.browser.paper_session.return_value = {'status': 'paper_disconnected'}
        with patch.object(paper_login, 'probe', side_effect=[result('submit_paper'), result('submit_paper', True)]) as probe:
            state = paper_login.attempt(self.browser, self.path)
        self.assertEqual([c.args[1:] for c in probe.call_args_list], [(), ('submit_paper',)])
        self.assertEqual(state['status'], 'paper_disconnected')
        self.assertTrue(state['saved_login_succeeded'])

    def test_blocked_or_malformed_state_never_authorizes_another_click(self):
        for saved in ({'blocked': True}, {}, [], {'blocked': 'false'}):
            self.path.write_text(json.dumps(saved))
            with self.subTest(saved=saved), patch.object(paper_login, 'probe') as probe:
                state = paper_login.attempt(self.browser, self.path)
            self.assertIn(state['reason'], ('paper_login_attempt_exhausted', 'paper_login_state_unavailable'))
            probe.assert_not_called()
        self.path.write_text('malformed')
        with patch.object(paper_login, 'probe') as probe:
            self.assertEqual(paper_login.attempt(self.browser, self.path)['reason'], 'paper_login_state_unavailable')
        probe.assert_not_called()

    def test_guard_denials_click_nothing_and_store_no_credentials(self):
        for reason in paper_login.REASONS:
            with self.subTest(reason=reason), patch.object(paper_login, 'probe', return_value={
                    'eligible': False, 'reason': reason}) as probe:
                state = paper_login.attempt(self.browser, self.path)
            self.assertEqual(state['reason'], reason)
            probe.assert_called_once_with(self.browser)
            self.assertFalse(self.path.exists())

    def test_state_write_failure_prevents_both_clicks(self):
        with patch.object(paper_login, 'probe', return_value=result('select_paper')) as probe, \
                patch.object(paper_login, '_write_state', side_effect=OSError('private fixture')):
            state = paper_login.attempt(self.browser, self.path)
        probe.assert_called_once_with(self.browser)
        self.assertNotIn('private fixture', json.dumps(state))

    def test_uncertain_click_is_never_retried_on_next_check(self):
        with patch.object(paper_login, 'probe', side_effect=[result('submit_paper'),
                BrowserCommandError('command_timeout', 'paper_saved_login')]) as probe:
            state = paper_login.attempt(self.browser, self.path)
            again = paper_login.attempt(self.browser, self.path)
        self.assertEqual(probe.call_count, 2)
        self.assertTrue(state['saved_login_attempted'])
        self.assertEqual(again['reason'], 'paper_login_attempt_exhausted')
        self.assertTrue(json.loads(self.path.read_text())['blocked'])

    def test_rechecked_paper_guard_can_stop_submission(self):
        with patch.object(paper_login, 'probe', side_effect=[result('select_paper'), result('select_paper', True),
                {'eligible': False, 'attempted': False, 'reason': 'paper_login_mode_unverified'}]):
            state = paper_login.attempt(self.browser, self.path)
        self.assertEqual(state['reason'], 'paper_login_mode_unverified')
        self.browser.paper_session.assert_not_called()
        self.assertTrue(json.loads(self.path.read_text())['blocked'])

    def test_challenge_after_submission_stops_without_confirmation(self):
        self.browser.paper_session.return_value = {'status': 'login_required'}
        with patch.object(paper_login, 'probe', side_effect=[result('submit_paper'), result('submit_paper', True),
                {'eligible': False, 'reason': 'paper_login_challenge_required'}]) as probe:
            state = paper_login.attempt(self.browser, self.path)
        self.assertEqual(state['reason'], 'paper_login_challenge_required')
        self.assertEqual(probe.call_count, 3)
        self.assertTrue(json.loads(self.path.read_text())['blocked'])

    def test_unverified_portal_never_counts_as_success_or_clears_block(self):
        self.browser.paper_session.return_value = {'status': 'paper_not_verified'}
        self.browser.deadline = 999999999999
        with patch.object(paper_login, 'probe', side_effect=[result('submit_paper'), result('submit_paper', True)]) as probe:
            state = paper_login.attempt(self.browser, self.path)
        self.assertEqual(state['reason'], 'paper_login_not_restored')
        self.assertEqual(probe.call_count, 2)
        self.assertNotIn('saved_login_succeeded', state)
        self.assertTrue(json.loads(self.path.read_text())['blocked'])
        self.assertEqual(self.browser.deadline, 999999999999)

    def test_clearing_after_verified_paper_preserves_minimal_schema(self):
        self.path.write_text('{"blocked":true}')
        paper_login.clear_verified(self.path)
        saved = json.loads(self.path.read_text())
        self.assertFalse(saved['blocked'])
        self.assertEqual(set(saved), {'blocked', 'checked_at'})
        self.assertIsNotNone(datetime.fromisoformat(saved['checked_at']).tzinfo)

    def test_action_evaluate_never_uses_retry_wrapper(self):
        self.browser._command.return_value = {'result': result('select_paper')}
        self.browser._command_once.return_value = {'result': result('select_paper', True)}
        self.assertFalse(paper_login.probe(self.browser)['attempted'])
        self.assertTrue(paper_login.probe(self.browser, 'select_paper')['attempted'])
        self.browser._command.assert_called_once()
        self.browser._command_once.assert_called_once()
        self.assertNotIn('PERFORM_ACTION', self.browser._command_once.call_args.args[-1])

    def test_probe_does_not_return_unrecognized_page_payload(self):
        self.browser._command.return_value = {'result': {'eligible': False, 'reason': 'private account data',
                                                       'password': 'fixture secret'}}
        state = paper_login.probe(self.browser)
        self.assertEqual(state['reason'], 'paper_login_probe_failed')
        self.assertNotIn('private account', json.dumps(state))
        self.assertNotIn('fixture secret', json.dumps(state))


if __name__ == '__main__':
    unittest.main()
