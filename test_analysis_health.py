"""Offline analysis-launch monitoring tests; no scheduled tasks or messages run."""

from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import analysis_health as health


class AnalysisHealthTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 13, 11, 10, tzinfo=ZoneInfo('America/Denver'))
        self.tasks = [dict(name=name, state='Ready', result=0,
                           last_run=self.now.replace(hour=h, minute=m).isoformat())
                      for name, (h, m) in health.TASK_TIMES.items()]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'

    def check(self, **kwargs):
        with patch.object(health, 'read_tasks', return_value=self.tasks):
            return health.check_analysis_health(state_path=self.path, now=self.now, **kwargs)

    def test_successful_due_tasks_are_healthy(self):
        self.assertEqual(health.assess_tasks(self.tasks, self.now)['status'], 'ready')

    def test_windows_refused_launch_is_reported_with_safe_code(self):
        self.tasks[0]['result'] = 2147946720
        issue = health.assess_tasks(self.tasks, self.now)['issues'][0]
        self.assertEqual(issue, {'task': 'IBKR_PreMarket_US', 'reason': 'task_failed',
                                 'result': '0x800710E0'})

    def test_future_task_old_failure_is_not_today_failure(self):
        self.tasks[-1].update(result=1, last_run=(self.now - timedelta(days=1)).isoformat())
        self.assertEqual(health.assess_tasks(self.tasks, self.now)['status'], 'ready')

    def test_missing_start_detected_after_grace(self):
        self.tasks[3]['last_run'] = (self.now - timedelta(days=1)).isoformat()
        self.assertEqual(health.assess_tasks(self.tasks, self.now)['issues'][0]['reason'],
                         'task_not_started')
        early = self.now.replace(hour=11, minute=4)
        self.assertEqual(health.assess_tasks(self.tasks, early)['status'], 'ready')

    def test_repaired_future_first_trigger_does_not_realert_old_failure(self):
        self.tasks[0].update(result=2147946720,
                             start_boundary='2026-10-14T05:45:00-06:00')
        self.assertEqual(health.assess_tasks(self.tasks, self.now)['status'], 'ready')
        tomorrow = self.now.replace(day=14, hour=5, minute=51)
        issue = health.assess_tasks(self.tasks, tomorrow)['issues'][0]
        self.assertEqual(issue['reason'], 'task_not_started')

    def test_running_task_does_not_trigger_failure(self):
        self.tasks[3].update(state='Running', result=267009, last_run=self.now.isoformat())
        self.assertEqual(health.assess_tasks(self.tasks, self.now)['status'], 'ready')

    def test_overdue_task_detected(self):
        self.tasks[3].update(state='Running', last_run=(self.now - timedelta(minutes=36)).isoformat())
        self.assertEqual(health.assess_tasks(self.tasks, self.now)['issues'][0]['reason'], 'task_overdue')

    def test_missing_and_disabled_tasks_are_reported(self):
        self.tasks.pop()
        self.tasks[0]['state'] = 'Disabled'
        reasons = {x['reason'] for x in health.assess_tasks(self.tasks, self.now)['issues']}
        self.assertEqual(reasons, {'task_missing', 'task_disabled'})

    def test_weekends_do_not_expect_weekday_runs(self):
        for row in self.tasks:
            row['last_run'] = '2026-10-09T13:55:00-06:00'
        self.assertEqual(health.assess_tasks(self.tasks, self.now.replace(day=17))['status'], 'ready')

    def test_initial_healthy_is_quiet(self):
        with patch('telegram_notify.send') as send:
            self.check()
        send.assert_not_called()

    def test_failure_deduplicates_and_recovery_notifies_once(self):
        self.tasks[0]['result'] = 1
        with patch('telegram_notify.send', return_value=True) as send:
            self.check()
            self.check()
            self.tasks[0]['result'] = 0
            self.check()
            self.check()
        self.assertEqual(send.call_count, 2)

    def test_failed_delivery_retries(self):
        self.tasks[0]['result'] = 1
        with patch('telegram_notify.send', side_effect=[False, True]) as send:
            self.check()
            self.check()
        self.assertEqual(send.call_count, 2)

    def test_no_notify_does_not_suppress_later_failure_notice(self):
        self.tasks[0]['result'] = 1
        with patch('telegram_notify.send', return_value=True) as send:
            self.check(notify=False)
            self.check()
        self.assertEqual(send.call_count, 1)

    def test_permission_failure_is_unknown_not_broker_logout(self):
        with patch.object(health, 'read_tasks', side_effect=PermissionError('secret broker URL')):
            state = health.check_analysis_health(state_path=self.path, now=self.now, notify=False)
        self.assertEqual(state['status'], 'analysis_check_unavailable')
        self.assertNotIn('secret', json.dumps(state))

    def test_task_reader_is_bounded_and_never_runs_tasks(self):
        response = Mock(returncode=0, stdout=json.dumps(self.tasks))
        with patch.object(health.subprocess, 'run', return_value=response) as run:
            self.assertEqual(health.read_tasks(), self.tasks)
        self.assertEqual(run.call_args.kwargs['timeout'], 25)
        command = ' '.join(run.call_args.args[0])
        self.assertNotIn('Start-ScheduledTask', command)
        self.assertNotIn('Register-ScheduledTask', command)


if __name__ == '__main__':
    unittest.main()
