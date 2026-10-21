"""Observe Windows analysis launches independently of broker/browser health."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TASK_TIMES = {
    'IBKR_PreMarket_US': (5, 45), 'IBKR_PreMarket_CA': (7, 0),
    'IBKR_Session_1': (8, 30), 'IBKR_Session_2': (11, 0),
    'IBKR_Session_3': (12, 30), 'IBKR_Session_4': (13, 55),
}


def read_tasks() -> list[dict]:
    names = ','.join("'" + name + "'" for name in TASK_TIMES)
    script = (
        "$ErrorActionPreference='Stop'; @(foreach($name in @(" + names + ")) {"
        "$t=Get-ScheduledTask -TaskName $name; $i=$t|Get-ScheduledTaskInfo;"
        "[pscustomobject]@{name=$name;state=[string]$t.State;"
        "last_run=$i.LastRunTime.ToString('o');result=[long]$i.LastTaskResult;"
        "start_boundary=$t.Triggers[0].StartBoundary}"
        "})|ConvertTo-Json -Compress"
    )
    powershell = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) /
                     'System32/WindowsPowerShell/v1.0/powershell.exe')
    result = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise RuntimeError('task_read_failed')
    data = json.loads(result.stdout.lstrip('\ufeff'))
    if not isinstance(data, list):
        raise ValueError('task_response_invalid')
    return data


def assess_tasks(tasks: list[dict], now: datetime) -> dict:
    local = now.astimezone(ZoneInfo('America/Denver'))
    rows = {row['name']: row for row in tasks if row.get('name') in TASK_TIMES}
    issues = []
    for name, (hour, minute) in TASK_TIMES.items():
        row = rows.get(name)
        if row is None:
            issues.append({'task': name, 'reason': 'task_missing'})
            continue
        if row.get('state') == 'Disabled':
            issues.append({'task': name, 'reason': 'task_disabled'})
            continue
        if row.get('start_boundary'):
            boundary = datetime.fromisoformat(row['start_boundary'])
            if boundary.tzinfo is None:
                boundary = boundary.replace(tzinfo=local.tzinfo)
            if local < boundary:
                continue
        due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if local.weekday() >= 5 or local < due + timedelta(minutes=5):
            continue
        started = datetime.fromisoformat(row['last_run']).astimezone(local.tzinfo)
        result = int(row['result'])
        if row.get('state') == 'Running':
            if started < local - timedelta(minutes=35):
                issues.append({'task': name, 'reason': 'task_overdue'})
        elif started < due:
            issues.append({'task': name, 'reason': 'task_not_started'})
        elif result != 0:
            issues.append({'task': name, 'reason': 'task_failed',
                           'result': f'0x{result & 0xffffffff:08X}'})
    return {'status': 'analysis_tasks_failed' if issues else 'ready', 'issues': issues}


def check_analysis_health(*, notify: bool = True, state_path: Path | None = None,
                          now: datetime | None = None) -> dict:
    from browser_research import browser_lock
    now = now or datetime.now(ZoneInfo('America/Denver'))
    state_path = state_path or ROOT / 'logs/analysis_scheduler_state.json'
    with browser_lock(state_path.with_suffix('.lock')) as acquired:
        if not acquired:
            return {'status': 'busy'}
        try:
            state = assess_tasks(read_tasks(), now)
        except Exception:
            state = {'status': 'analysis_check_unavailable', 'reason': 'task_read_failed'}
        try:
            previous = json.loads(state_path.read_text(encoding='utf-8'))
            if not isinstance(previous, dict):
                previous = {}
        except (OSError, ValueError):
            previous = {}
        fingerprint = json.dumps({k: state[k] for k in ('status', 'issues', 'reason') if k in state},
                                 sort_keys=True)
        notified = previous.get('notified_fingerprint')
        actionable = state['status'] != 'ready' or previous.get('notified_failure', False)
        if notify and actionable and fingerprint != notified:
            from telegram_notify import send
            if state['status'] == 'ready':
                message = 'Analysis scheduler: due tasks have recovered. This verifies task execution, not guaranteed stock signals or broker login.'
            elif state['status'] == 'analysis_check_unavailable':
                message = 'Analysis scheduler: task status could not be read. Browser health is not proof the independent analysis jobs are running.'
            else:
                detail = '; '.join(item['task'] + ': ' + item['reason'] +
                                   (' ' + item['result'] if 'result' in item else '')
                                   for item in state['issues'])
                message = ('Analysis scheduler needs attention: ' + detail +
                           '. These Windows task failures are separate from IBKR Paper login. '
                           'The monitor will not start catch-up trading sessions automatically.')
            if send(message, parse_mode=''):
                notified = fingerprint
                previous['notified_failure'] = state['status'] != 'ready'
        state.update(checked_at=now.isoformat(), notified_fingerprint=notified,
                     notified_failure=previous.get('notified_failure', False))
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state), encoding='utf-8')
        temporary.replace(state_path)
        return state
