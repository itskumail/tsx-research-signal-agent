# Analysis Scheduler Reliability

Incident: October 13, 2026, Windows trading VM 104. This is separate from the
IBKR Paper session and its optional reconnect. The existing browser monitor
could run while Windows refused every independent analysis launch.

## Verified Findings

- All six tasks were enabled with the correct owner, weekday triggers and no
  battery/idle/network blocking condition. The last completed scheduler logs
  before the incident were October 9. No October 12/13 scheduled logs existed.
- Refused launches returned `0x800710E0` before the PowerShell/Python runner
  started. Signing back into IBKR cannot fix this failure.
- The owner was logged into an RDP Windows session. Ordinary task starts failed;
  Microsoft's explicit-session `RunEx` launch succeeded. The precise underlying
  Windows session-discovery defect was not established; do not blame IBKR or
  claim a specific Windows update caused it.
- One-shot offline timed probes failed in both unified and legacy interactive
  task engines. Changing engine alone was not a remedy.
- A limited S4U background probe started automatically and finished with code 0.
  Public Yahoo data, existing SSH key, private configuration/news-session reads,
  production runner imports and a non-sending Telegram bot-auth check passed.
  No model request, broker action or message was made by those probes.
- The owner-approved Session 2 catch-up completed with code 0 and logged three
  successful stock cards and three successful priced-option cards. No Telegram
  send failures were logged. These are research alerts, not executed orders.

The owner approved the permanent repair, deployed October 13 at 11:57 Denver
time through `scheduled/repair_background_tasks.ps1`. All six installed tasks
were independently verified enabled, S4U/limited, with unchanged next run times,
working directory and 30-minute limits. `logs/task_repair_result.json` confirms
all six updates. All temporary diagnostic task definitions were removed.
The next regular production trigger was 12:30 that day; the deployment check
does not itself establish a completed post-repair production run. Do not launch
additional catch-up sessions without approval.

## Deployed Unattended Repair

S4U uses the existing owner, limited privileges and no stored Windows password.
It does not depend on selecting the owner's desktop session. It does not require
a daily UAC prompt. Administrator approval is only for one-time deployment.

[Microsoft's logon-type documentation](https://learn.microsoft.com/en-us/windows/win32/taskschd/principal-logontype)
warns that S4U lacks integrated network credentials and encrypted-file access.
The project's API-token HTTPS and key-authenticated SSH paths were verified in
this mode on this host; do not generalize that to SMB shares, EFS files, different
credentials or another machine. Reverify those dependencies after migration.

The repair helper validates the six existing names, owner, limited privilege,
actions, trigger count and absence of running analysis. It backs up task XML
under `logs/task_backup_<timestamp>/`, updates existing definitions rather than
unregistering them, keeps regular weekday times and limits, advances the start
boundary to the previously recorded next run to prevent missed-session replay,
and verifies the next run is unchanged. It does not store passwords or elevate
the analysis actions. Partial failure is recorded, not claimed as success.
`scheduled/register_tasks.ps1` now uses the same S4U/limited mode and absolute
PowerShell path for future owner-approved deployments. Do not run registration
again just to test the mode; it replaces definitions.

S4U runs in background session 0. It must never launch Chrome there. Existing
`browser_launcher.py` rejects recovery without an interactive session. Public
research may attach to an existing browser through SSH/OpenClaw; unavailable
browser evidence remains optional. The separate interactive monitor retains
scoped browser recovery. Since October 16, that monitor alone may perform the
owner-enabled [saved-form Paper sign-in](PAPER_SAVED_LOGIN.md); background
analysis tasks do not start this authentication flow. Credential entry and
challenges remain the owner's step.

## Independent Launch Health

`analysis_health.py` is called before the Paper check by the existing
`browser_research.py keepalive` CLI. No extra production schedule was created.
It reads the six task states/results with a bounded 25-second PowerShell read;
it never starts, retries, repairs or registers a trading task.

- Weekday tasks have a five-minute post-trigger grace period. Missing starts,
  nonzero completed/refused results, disabled/missing tasks and running tasks
  older than 35 minutes are reported separately from broker/browser state.
- A future task's old failure is not represented as today's missed start.
  The actual first-trigger boundary is honored, so today's repaired tasks whose
  next first trigger is tomorrow do not re-alert on pre-repair failures. After
  that boundary, missed/failed starts are still detected normally.
  Weekends do not expect weekday runs. Informational weekday holiday jobs still
  have triggers; qualified signal dispatch separately uses the XTSE calendar.
- `analysis_tasks_failed` is actionable. `analysis_check_unavailable` means the
  read failed; it does not establish a broker logout or successful analysis.
- `logs/analysis_scheduler_state.json` persists safe codes, task names and the
  notification fingerprint. Its separate OS lock prevents duplicate health
  notices. Changed failures notify once; failed sends retry; unchanged healthy
  status is quiet. A recovery from a notified failure can notify once.
- `--no-notify` disables both analysis-health and Paper-health Telegram sends.
  It does not consume the later notification or launch a catch-up job.
- The CLI emits analysis diagnostics and then Paper diagnostics. Healthy Chrome
  does not override failed analysis. Paper notices no longer promise that stock
  jobs are running merely because broker login is optional.

Task completion does not prove every data source or Telegram message succeeded,
nor guarantee qualified signals. If the monitor itself cannot start, it cannot
send helper-generated notices. This guard still needs the existing local
Codex scheduler/app, VM and execution permissions; it is not an independent OS
watchdog. Keep one active 40-minute monitor and preserve its state in migrations.

## Checks

On VM 104, inspect task state and the existing repair result without starting jobs:

```powershell
Get-ScheduledTask -TaskName 'IBKR_*' | Select-Object TaskName,State
Get-ScheduledTask -TaskName 'IBKR_*' | Get-ScheduledTaskInfo | Select-Object LastRunTime,LastTaskResult,NextRunTime
Get-Content C:\Trading\logs\task_repair_result.json
.\.venv\Scripts\python.exe -m unittest test_analysis_health test_browser_launcher test_browser_research test_market_context test_research_pipeline test_telegram_notify
```

122 offline mocked tests passed October 13. Task Scheduler operational history
was enabled with one-time administrator approval. `PYTHONUNBUFFERED=1` is now set
by the wrapper so future job progress reaches the scheduler log promptly.
Temporary probes are diagnostic only; remove their definitions after testing.
Never delete cursors, sent-alert logs or browser reconnect state to force retries.
