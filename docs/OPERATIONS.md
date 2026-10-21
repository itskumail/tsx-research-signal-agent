# Operations And Troubleshooting

Baseline: 2026-10-09. Commands run on Windows trading VM 104 unless labelled
Ubuntu or Codex. Start in `C:\Trading`; use the project venv explicitly.

## 1. Command Safety Classes

| Class | Examples | Effect |
|---|---|---|
| Offline | Named unit suite, `py_compile`, `pip check`, Git inspection | No intentional broker/provider/Telegram requests |
| Passive live observation | `browser_research.py status`, task inspection | SSH/status reads; no browser launch/navigation or Telegram |
| Scoped live recovery | `browser_research.py recover` | May reopen only the dedicated Chrome; no authentication |
| Scoped Paper session activity/authentication | `keepalive --no-notify` | Verified navigation, enabled guarded banner reconnect/saved-form Paper sign-in and state writes; no Telegram sends |
| Browser cleanup | `browser_research.py cleanup` | Closes dedicated-profile tabs except one IBKR/blank keeper |
| Live API test | `verify_deepseek.py`, `test_llm_setup.py` | Can spend API credit, contact a provider and log responses |
| Live account read | `get_portfolio.py`, `get_snapshot.py`, `test_portfolio_review.py` | Requires Gateway; output may contain private account details |
| Production dispatch | `session_runner.py`, `run_session.ps1`, `signals.py --send`, Telegram test | Real Telegram messages; can spend API credit |
| Deprecated stub | `place_order.py` | Error/exit 1 and attempts a real Telegram warning; not a harmless test |

Do not discover/run every `test_*.py` blindly. The live scripts are not offline
unit tests. Direct quote/history CLIs are Gateway clients; they do not automatically
use the production runner's independent Yahoo fallback.

## 2. Routine Baseline Checks

Normal Windows PowerShell:

```powershell
Set-Location C:\Trading
git status --short
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest test_paper_login test_options_review test_analysis_health test_browser_launcher test_browser_research test_market_context test_research_pipeline test_telegram_notify
.\.venv\Scripts\python.exe browser_research.py status
```

Expected after the October 19 market-context repair: dependency check clean, 229 tests pass,
browser `available=true` when the dedicated browser/OpenClaw are accessible.
The status does not prove IBKR login, market-data permission or live quotes.

With Node available on the test host, also run the 41 mocked guard/snapshot/transport tests:

```powershell
node --test scripts/test_paper_login.mjs scripts/test_ibkr_snapshot.mjs scripts/test_openclaw_public_review.mjs
```

These use fake SDK/CLI responses and need no accounts. The production bridge
uses VM 105's existing Node next to OpenClaw, not this Windows Node. Do not
confuse the offline test with `scripts/openclaw_completion_probe.mjs`: the probe
performs OAuth preparation on VM 105, and without `--prepare-only` it makes a
live GPT request requiring owner approval. See the route/transport procedure in
[Options And Review Repair](OPTIONS_AND_REVIEW_REPAIR.md#approved-chatgpt-route-repair).

The snapshot tests run the actual Python-embedded JavaScript with fake fetch
and timers. Browser cleanup tests isolate post-research activity and fail on
unmocked subprocess calls. New source/audit semantics and deployment limits are
recorded in [October 16 review repairs](REVIEW_REPAIRS_2026-10-16.md). Existing
scheduled processes load code on their next start; no re-registration/restart
is required. Do not interpret offline success as a live session-longevity test.
With the owner-enabled saved-form sign-in flag, `keepalive --no-notify` may now
select Paper and submit Chrome's already-filled SSO form. Read
[Saved-form Paper sign-in](PAPER_SAVED_LOGIN.md), preserve its attempt block,
and never force logout to stage a test or treat a ready-session check as a
successful credential-submission test.

The current independent-review entry point is
[External Review Packet](EXTERNAL_REVIEW_2026-10-14.md). Review bundles exclude
runtime logs/authentication/account data; do not upload the full workspace.
`logs/browser_monitor_history.jsonl` now records safe helper times, status,
verified navigation and whether notifications were enabled. A manual no-notify
row does not establish that the recurring trigger ran. No-op or unconfirmed
portfolio navigation is a failure, even if the Paper page is still accessible.

Codex's sandbox may restrict SSH/HTTPS/loopback and Python event-loop sockets.
Use normal reviewed `exec_command` elevation when those accesses are required.
Denial means the check could not run; it does not establish browser closure or
logout. Do not disable the sandbox/firewall or repeatedly retry authentication.

## 3. Check Windows Tasks Without Running Them

```powershell
Get-ScheduledTask -TaskName 'IBKR_*' | Select-Object TaskName,State
Get-ScheduledTask -TaskName 'IBKR_*' | Get-ScheduledTaskInfo | Select-Object LastRunTime,LastTaskResult,NextRunTime
```

Expected task definitions: 05:45 premarket US, 07:00 premarket CA, 08:30 S1,
11:00 S2, 12:30 S3, 13:55 S4, weekdays. Task code is in
`scheduled/register_tasks.ps1`; actual installed triggers should be inspected
after a migration. A successful process result is not proof every source was
available or every Telegram message delivered.

October 13: interactive starts were refused before the runner, despite correct
owner login. Explicit-session catch-up succeeded, and a password-free limited
background mode passed timed/network fixtures. Deployment and failure-monitor
details: [Scheduler reliability](SCHEDULER_RELIABILITY.md). Do not mistake an
enabled task or healthy browser for a successful analysis launch.
The owner-approved S4U/limited repair was deployed at 11:57 Denver time; all six
tasks were verified enabled with unchanged next times and limits. Diagnostic
task definitions were removed. Future registration uses the same background mode.

Tasks run as the existing owner with password-free `S4U` logon, limited privileges,
start-when-available and a 30-minute limit. Independent analysis does not require
desktop login after reboot. The interactive Codex/browser monitor does. Their
weekday triggers may run on holidays; dispatch uses XTSE calendar
gates. Late catch-up runs must still pass the dispatch window.

Registration replaces tasks with the same names. Do not run registration or
unregistration during a documentation check. When explicitly deploying:

```powershell
# VM 104 Administrator PowerShell, with owner approval
.\scheduled\register_tasks.ps1
```

Registration changes only future scheduling; inspect installed times and last
results afterward. Keep the retired laptop's tasks disabled. Do not activate
a second runner against the same news session/state.

## 4. Codex Paper Check

Existing automation ID: `ibkr-paper-browser-check`, active, every 40 minutes,
including nights/weekends. Its command is:

```powershell
.\.venv\Scripts\python.exe browser_research.py keepalive
```

The saved automation prompt requires normal reviewed network elevation, safe
diagnostic output, helper-only guarded Paper banner reconnect, quiet healthy/unchanged
or busy results, and deduplicated user-attention notices. Manage the automation
through the app's task tool/UI, not by editing its TOML or adding a duplicate.
Local definition is under `%USERPROFILE%\.codex\automations\ibkr-paper-browser-check\`;
this app-managed state is outside the repository and must be handled separately
when migrating the assistant or machine.

Current destination: the separate `IBKR Browser Monitor` chat. Full saved-prompt
contract, subscription-loss guidance, alternative schedulers and activation /
rollback checklist: [Monitor migration](BROWSER_MONITOR_MIGRATION.md). The Python
check makes no research-model call. Do not create an additional schedule while
the current one is active; no independent fallback scheduler is installed.
The keepalive CLI first checks analysis-task health independently, then Paper
health. It sends deduplicated failed-launch notices without starting jobs.
Preserve `logs/analysis_scheduler_state.json`; `--no-notify` suppresses both sets
of notices. A browser failure or busy lock does not suppress the task-health read.

The VM and app must be running for local scheduled work. See the
[official scheduled-task requirements](https://learn.chatgpt.com/docs/automations?surface=app).
Background analysis can attach to an existing Chrome; it must not launch GUI
Chrome in session 0. Interactive recovery remains in the separate monitor.
These jobs are not an all-night paper-check replacement. No independent Windows
paper-check service was installed.

## 5. Recovery, Navigation And Cleanup

```powershell
# May reopen the existing dedicated Chrome; safe status only
.\.venv\Scripts\python.exe browser_research.py recover

# May navigate/reconnect verified Paper under guards; no Telegram send
.\.venv\Scripts\python.exe browser_research.py keepalive --no-notify

# Closes dedicated-profile research tabs; preserves IBKR or a blank keeper
.\.venv\Scripts\python.exe browser_research.py cleanup
```

All three actions use the shared OS lock. `busy` is an expected coordination
result, not a crash; let the active research finish. Do not remove the lock file
to bypass a worker. A process exit releases the OS lock automatically.

Recovery verifies loopback 9224, the existing executable/profile and absence of
a matching Chrome process. Only a confirmed refusal and an interactive Windows
session permit launch. Unknown/permission/timeout results do not. The launcher
does not kill, reset or duplicate an existing process. Failed attempts cool down
for 600 seconds using `browser_recovery_state.json`.

It opens the normal IBKR portal entry URL, not credentials or login controls.
A running profile with an observed empty tab list may also get that portal tab.
After OpenClaw attaches, public research can work while IBKR needs manual Paper
login. Browser recovery is at the next check/research invocation, not instant.

Cleanup closes every other tab in the dedicated profile, including non-IBKR
tabs manually opened there. Treat that profile as the research workspace; keep
unrelated personal tabs in the original/another browser. Passive status checks
and independent scans without browser research do not perform cleanup.

Paper activity verifies two real tab clicks, Positions then Dashboard. The separate opt-in reconnect branch
accepts only the unique `Log In` control inside the verified disconnected-Paper
warning. Production enabled it on October 11 after an owner-authorized live test.
It stops at credentials, active dialogs, authentication pages or competing-session
confirmations; one failed/uncertain attempt is persistently blocked until a
healthy Paper session is seen. This banner-only flow remains separate from
the October 16 opt-in saved-form SSO recovery. See
[Paper reconnect](PAPER_RECONNECT.md); preserve `browser_reconnect_state.json`.
Paper navigation verifies the visible simulated-account banner first. A login
page is initially classified by its path; only the enabled saved-form guard
checks field-presence booleans and can select Paper/submit the verified form.
It returns no credential values. A live/unverified portal is not navigated.
Activity is not a guarantee against IBKR server-side expiry or maintenance.

## 6. Safe Diagnostics

### Status Interpretation

| Status | Meaning and next action |
|---|---|
| `ready` | Verified check/navigation healthy; unchanged results stay quiet |
| `busy` | Shared lock held; wait for the active worker |
| `disabled` | Corresponding helper switch disabled; not a browser failure |
| `browser_unavailable` | Dedicated control endpoint not ready; inspect recovery reason |
| `control_unavailable` | SSH/OpenClaw/attachment failure; login state is not established |
| `login_required` | Public research available; inspect safe saved-form reason/attempt flags. Missing fields, blocked attempts or challenges require owner attention |
| `ibkr_tab_missing` | Public research can run; open IBKR/Paper manually |
| `paper_not_verified` | No verified paper banner; inspect manually, do not navigate account |
| `paper_disconnected` | Paper banner exists but brokerage/data disconnected; displayed data may be delayed |
| `session_check_failed` | Account state unknown because observation failed |
| `navigation_failed` | Paper session was verified, but navigation failed; not automatic logout evidence |
| `helper_failed` | Helper itself failed; inspect safe reason/step before blaming browser |

### Failure And Recovery Codes

| Code | Check |
|---|---|
| `ssh_connection_failed` | VM .16 reachable, existing key/known host, SSH service and permission |
| `ssh_client_missing` | Windows OpenSSH client available on the runner's PATH |
| `openclaw_cli_missing` | Absolute Ubuntu CLI path after Node/OpenClaw upgrade |
| `command_timeout` | Bound exceeded; compare source/CLI health independently |
| `malformed_response` | CLI result shape/version changed; do not print raw broker response |
| `profile_mismatch` | Stop before mutations; intended profile must be `canadian-stocks` |
| `cdp_not_ready` | Dedicated OpenClaw endpoint not ready; local recovery may help |
| `attach_not_ready` | Local Chrome ready, remote attachment still unready; inspect bridge/profile/VM |
| `local_endpoint_unknown` | Timeout/permission/invalid endpoint; do not infer closure |
| `local_process_check_failed` | Process presence cannot be established; no automatic launch |
| `profile_already_running` | Existing matching process, no duplicate/kill; inspect manually |
| `launcher_or_profile_missing` | Existing Chrome executable/profile absent; migration/config issue |
| `windows_login_required` | No interactive session for GUI launch |
| `restart_cooldown` | Wait; do not erase state to force repeated launches |
| `chrome_launch_failed`, `chrome_start_not_ready` | Inspect Windows/profile health without automatic resets |
| `portfolio_link_missing` | Page layout/ref missing; current navigation needs adaptation |
| `reconnect_attempt_exhausted` | Prior reconnect failed/uncertain; inspect manually, do not reset state |
| `reconnect_state_unavailable` | Attempt-state validation/write failed; no click permitted |
| `reconnect_not_restored` | One attempt did not restore brokerage connection; user attention needed |

Health notices are deduplicated by status/reason/failed step/recovery reason.
Failed Telegram sends are retried on a later check. Initial healthy checks and
unchanged healthy checks are quiet; a recovery from a notified failure can be
reported once. Only codes and minimal timestamps are stored in health state.

## 7. Common Problems

| Symptom | Diagnosis |
|---|---|
| Bullish label on a red Canadian day | Read dated market snapshot, XIC quote time/reference date and usable status; do not compare with a five-day chart baseline or substitute US direction |
| No stock cards | Check signal window, quota, owned-name exclusion, history count, hard blockers, sector penalties and model approval for promotion lanes |
| No priced option cards | Check per-side audit, bounded IBKR/MX coverage and fallback, underlying direction, native source identity, update timestamp, DTE, positive quotes, spread and OI/volume; native CAD does not require a US dual listing |
| Research works, IBKR asks for login | Public pages are independent; enabled banner reconnect or guarded saved-form Paper sign-in may recover it. Missing saved fields/challenges require owner attention |
| Chrome visible but paper check failed | Check SSH/CLI/bridge and safe failed step; browser-open is not control-ready |
| DeepSeek/API missing | Validate configuration presence and SDK installation, then securely check provider credit/auth with approval |
| OpenClaw review times out | Check `ai_review_meta`, selected daily model, reserved budget and safe reason; approved small tool-free model tests are separate from collection. See [Options And Review Repair](OPTIONS_AND_REVIEW_REPAIR.md) |
| No news events | Check Telethon authorization/cursors, RSS response and ticker/alias matching; don't reset cursors automatically |
| Telegram unavailable | Inspect safe HTTP status/type and destination configuration privately; never print token-bearing request URLs |
| Gateway/web session conflict | Leave Gateway disabled for this workflow and manually inspect Paper data/session status |
| Analysis task after reboot did not run | Verify S4U/limited principal, actual trigger/result and independent task-health diagnostic; desktop login is no longer its prerequisite |

## 8. Logs And Encodings

See the [state inventory](HANDOFF.md) for exact filenames. Inspect only relevant
logs locally and redact private account/provider material before sharing.

The task action uses `powershell.exe` (Windows PowerShell); its default
`Out-File` scheduler log is generally UTF-16LE. PowerShell 7 invocation can
produce UTF-8 instead. JSON/JSONL Python logs use UTF-8. Detect the actual file's
encoding rather than treating every `.log` identically.

```powershell
# Example: task-created Windows PowerShell scheduler log, not raw broker output
Get-Content -LiteralPath 'C:\Trading\logs\scheduler_session1_<timestamp>.log' -Encoding Unicode -Tail 60
```

`skipped_*` fallback markers are audit records: with Gateway disabled/down,
analysis usually ran on independent data. Required-Gateway abort markers differ.
An enabled successful Gateway run removes only its own session marker.

Do not delete logs/cursors to fix missing signals. Clearing sent logs resets
quota/cooldown evidence; clearing cursors can replay news. Back up before any
explicit state repair and document what changed.

## 9. Fresh Python Deployment

On a new/replacement Windows machine with approved Python 3.12 and project files:

```powershell
Set-Location C:\Trading
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest test_paper_login test_browser_launcher test_browser_research test_market_context test_research_pipeline test_telegram_notify test_analysis_health test_options_review
```

`requirements-lock.txt` is the complete production package snapshot.
`requirements.txt` and the legacy `setup.ps1` install only the minimal core;
that is insufficient for all news/model/enrichment/report features. Do not
reuse `.venv` from another OS/machine or blindly upgrade every pinned package.

Restore the private `.env` and Telethon session through the owner's secure
backup, or configure missing credentials manually. Never overwrite a working
`.env` with `.env.example`. Configure Chrome/SSH/OpenClaw separately using the
VM runbook. Verify passive reads before registering/activating schedules.

## 10. OpenClaw Upgrade Checklist

1. Let active browser research finish; a busy lock is expected. Avoid upgrading
   the CLI underneath a running collection/review process.
2. Preserve OpenClaw configuration, browser profiles and native provider sign-in
   securely. Preserve the original `windows` profile.
3. Use the installed product's supported upgrade method on Ubuntu VM 105.
4. Verify executable path and version. A Node version change can move the NVM
   CLI path; update `OPENCLAW_CLI_PATH` only to the verified executable.
5. Check passive profile status from Windows, then a small public-source fixture.
6. With owner scope for live helper actions, verify `keepalive --no-notify`.
   It can perform enabled saved-form Paper sign-in/banner reconnect, not just
   navigation. Do not force logout, weaken guards or reset attempt blocks to
   test an upgrade. Missing fields/challenges require owner attention.
7. With approval for provider allowance use, check a small tool-free request
   and bounded optional review; do not make a paid call as a documentation check.
8. Confirm cleanup retains IBKR, run the offline suite, then observe normal jobs.

The original timeout was not proof of Claude logout. Native sign-in/tool-free
request worked; startup-isolated OpenClaw review and smaller excerpts improved
the path. Preserve that distinction when troubleshooting.

## 11. Backup And Restore

Back up code, private `.env`, Telethon session, `logs/` cursors/sent history,
ledger/reports, the dedicated Chrome profile, Windows shortcut/task definitions,
OpenClaw configuration/authentication and Codex automation state. Store the
private pieces encrypted with appropriate access control, separately from a
shareable code/documentation snapshot.

Chrome profile backup should be taken with Chrome safely stopped or through a
consistent VM backup. Do not copy a live profile and assume cookies/databases
are coherent. Ask before stopping an authenticated browser. OS-protected browser
credentials may not transfer to a different Windows account/machine; manual
login may be needed after restore. Do not extract saved passwords to migrate.

Restore checklist:

1. Keep old production schedules disabled; never run both machines concurrently.
2. Restore code/state at `C:\Trading`, recreate venv from the lockfile.
3. Restore secrets privately; check configuration presence without printing them.
4. Verify the background analysis principal; restore/verify the interactive
   Windows session, Chrome profile/shortcut and local ports for browser recovery.
5. Verify existing SSH key/known host and OpenClaw profile/CLI/provider.
6. Run offline tests, passive browser status and scoped no-message checks.
7. Owner manually verifies IBKR Paper login/account banner as needed.
8. Restore schedules intentionally, inspect their actual triggers and observe
   the first scheduled job before retiring the previous deployment backup.

Git includes the ledger but ignores other essential private state. A Git clone
alone is not a complete operational restore.

## 12. Change And Documentation Checklist

Before a change, inspect the dirty worktree and record relevant state safely.
Keep ownership boundaries narrow. Update the owning function/tests, then the
configuration/strategy/operations pages affected. If a model change modifies
adapter parameters, test malformed output, missing symbols, approval defaults,
timeouts and unavailable-provider behavior with mocks before live requests.

Afterward, run the named suite and dependency check, validate local Markdown
links, and state exactly which live paths were exercised. Do not claim forced
recovery, indefinite authentication, execution or profitability from mocked
tests. Preserve rollback configuration privately, not in a committed key file.
