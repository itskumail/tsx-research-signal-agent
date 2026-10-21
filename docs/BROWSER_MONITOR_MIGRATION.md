# Browser Monitor Continuity And Migration

Baseline: 2026-10-11. This runbook preserves the 40-minute paper-browser check
when the owner changes assistant, client, model, account access or subscription.
Read [AGENTS.md](../AGENTS.md), [Handoff](HANDOFF.md) and [Operations](OPERATIONS.md)
first. This is a deployment recipe, not an installed backup scheduler or authority
to start another job merely because an assistant reads this file.

## 1. What Runs Today

| Item | Verified baseline |
|---|---|
| Scheduler | Codex desktop app, existing chat heartbeat |
| Automation ID | `ibkr-paper-browser-check` |
| Automation name | `IBKR Paper Browser Check` |
| Dedicated monitor chat | `IBKR Browser Monitor` |
| Monitor thread ID | `<monitor-thread-id>` |
| Project | Trading PC, local checkout `C:\Trading` |
| Cadence | Every 40 minutes, 24 hours/day, all seven days, including holidays |
| State checked on October 11 | Active; same dedicated monitor chat and cadence; guarded reconnect prompt enabled |
| Last configuration verification, October 16 | Active; same ID/chat/40-minute interval; saved-form Paper exception added in place, no duplicate schedule |
| Command host | Windows trading VM 104, `192.0.2.15` |
| Control host | Ubuntu OpenClaw VM 105, `192.0.2.16` |

The earlier development chat is not the monitor's destination. Do not move the
heartbeat back into it: recurring prompts previously flooded that conversation.
Thread IDs identify this installation only; a replacement client has its own
task/chat IDs. The stable contract is the helper command and cadence.

App-managed definition: `%USERPROFILE%\.codex\automations\ibkr-paper-browser-check\automation.toml`.
Inspect through supported app tools/UI; do not rewrite the TOML to migrate.
Preserve a private backup alongside the other restore materials.

## 2. The Check Does Not Need A Research Model

```text
One active scheduler, every 40 minutes
  -> Windows VM 104, C:\Trading, existing owner account
  -> .venv\Scripts\python.exe browser_research.py keepalive
  -> independent analysis-task health read and deduplicated failure notices
  -> shared browser OS lock
  -> existing local Chrome recovery checks, if needed
  -> authenticated SSH to Ubuntu OpenClaw CLI
  -> dedicated canadian-stocks browser attachment
  -> classify IBKR page; navigate only verified Paper
  -> optional guarded disconnected-Paper banner reconnect, never credential login
  -> owner-enabled saved-form Paper sign-in, never credential entry/retrieval
  -> minimal health state and deduplicated Telegram notice
```

There is no DeepSeek, Claude or ChatGPT research request inside this helper.
The current Codex scheduler uses an assistant turn to invoke the script; that
turn still depends on the client's available access and execution permissions.
The script uses OpenClaw's browser CLI, not its `agent exec` research review.
Do not add a paid model call or repeat stock research to every heartbeat.

Changing the scheduler's assistant model does not change the API ranker or
OpenClaw supplemental reviewer. If the replacement requires an assistant model,
choose an accessible model supported by that client; Medium or High only where
reasoning is configurable, following the owner's preference. No fixed model
name is necessary for this check. A direct OS scheduler needs no LLM at all.

## 3. Subscription Or Client Loss

Do not assume an expired subscription or changed account can continue the
current assistant-backed task. Verify access, task availability, permissions
and a recent completed run in the actual client. A saved active configuration
alone does not prove checks are executing.

The six Windows analysis tasks are separate. They do not require the Codex
client to start, although their own API/provider credentials, news authorization
and Telegram access must still work. DeepSeek and OpenClaw provider access are
separate from the repository assistant's ChatGPT access; verify them independently.

For subscription-independent monitoring, the preferred future deployment is
Windows Task Scheduler directly invoking the existing helper on VM 104. That
removes the assistant scheduler/model from the check, not the OpenClaw CLI,
Windows login, networking or broker session requirements. It is NOT installed
at this baseline. An owner-requested migration must pause the old heartbeat
before activating the replacement.

Official OpenAI documentation says local scheduled tasks need the desktop app
and computer running, while web tasks cannot directly operate a local folder.
Do not assume an ordinary ChatGPT web/mobile task can execute `C:\Trading`.
Verify an authorized local execution connection before choosing any cloud client.
See [official scheduled-task documentation](https://learn.chatgpt.com/docs/automations).
Feature and account availability must be rechecked at migration time.

## 4. Exact Execution Contract

Run under the existing logged-in Windows owner on VM 104:

```powershell
# Working directory: C:\Trading
C:\Trading\.venv\Scripts\python.exe C:\Trading\browser_research.py keepalive
```

Use the absolute executable and script paths and working directory `C:\Trading`.
Do not use system Python, a new worktree, the retired laptop, a copied `.env`,
or Ubuntu's Python to run this Windows GUI recovery helper.

The CLI loads the existing private `.env`. Required resources are the project
venv, installed Windows OpenSSH client, existing owner SSH key/known host,
reachable OpenClaw VM/CLI, the dedicated Chrome installation/profile, local
Chrome control and HTTPS to Telegram. Preserve host verification, firewall scope
and the original `windows` browser profile. Never put secrets in the task prompt.

Session checks use the existing network permission process. In Codex,
use reviewed escalation only when required and permitted by the active
environment; never pass escalation arguments when the environment forbids them.
Another client's equivalent must permit SSH, loopback and HTTPS
without bypassing its security policy. Permission denial is an execution
limitation, not evidence that Chrome closed or IBKR logged out.

Interpret the final JSON, not just process exit status. The helper can exit 0
while reporting `login_required`, `navigation_failed` or another degraded state.
Return only `status`, `reason`, `failed_step`, `recovery_reason` and, when useful,
`paper_session_status`, `reconnect_attempted`, `reconnect_succeeded`,
`saved_login_attempted` and `saved_login_succeeded`.
Do not return broker page text, account values or URLs.

`logs/browser_session_state.json` stores the check time and notification
fingerprint. `logs/browser_recovery_state.json` stores recovery cooldown state.
Since October 14, `logs/browser_monitor_history.jsonl` appends safe status/time/
activity diagnostics, elapsed seconds, prior-check gap and notification-enabled
flag. Preserve it. `activity_verified` means a different observed read-only
portfolio route was reached; it does not acknowledge an IBKR idle-timer reset.
The earlier current-Portfolio click could be a no-op and has been replaced.
`last_verified_activity_at` is retained separately from plain page availability.
Manual no-notify rows are not evidence that a recurring trigger executed.
`logs/browser_reconnect_state.json` stores a blocked flag and UTC check time.
Preserve every listed state file across migration. Never clear attempt state to force another
click. Production `OPENCLAW_PAPER_RECONNECT=true` was explicitly authorized and
live-tested October 11; code/template defaults are false. Preserve the owner's
choice, or set false to disable the reconnect. See [Paper reconnect](PAPER_RECONNECT.md).
Since October 16, production `OPENCLAW_PAPER_SAVED_LOGIN=true` also permits
strict helper-only Paper selection and submission of Chrome's already-filled
SSO form. Code/template defaults remain false. Preserve the separate
`logs/browser_paper_login_state.json` block and the owner's opt-in. It contains
no credentials; do not erase it to retry. Read [Saved-form sign-in](PAPER_SAVED_LOGIN.md)
before any authentication-related test. `keepalive --no-notify` can perform this
enabled action as well as banner reconnect; it only disables Telegram sends.
`busy` and `disabled` return without a fresh
health-state write; do not mistake an older timestamp for a new check result.
Keep the shared lock; the file's existence alone is not proof of a held lock.
Since October 13 the keepalive CLI also reads the six analysis tasks before the
Paper check and can report `analysis_tasks_failed` or `analysis_check_unavailable`.
Preserve `logs/analysis_scheduler_state.json` and its deduplication state too.
It never launches a session; healthy Paper cannot override failed analysis.
See [Scheduler reliability](SCHEDULER_RELIABILITY.md). No extra schedule was added.

## 5. Reusable Task Prompt

Use this text in the replacement scheduler's saved prompt. Configure the actual
40-minute schedule separately; prose in a prompt does not register a trigger.

```text
Every 40 minutes, including nights, weekends and holidays, run the existing
research/Paper-session helper on Windows trading VM 104 from C:\Trading:
C:\Trading\.venv\Scripts\python.exe C:\Trading\browser_research.py keepalive

Read AGENTS.md, docs/OPERATIONS.md and docs/BROWSER_MONITOR_MIGRATION.md before
first use. Use this dedicated monitoring task/chat, not the development chat.
Use approved local execution with SSH access to OpenClaw VM 105, local Chrome
control access and HTTPS to Telegram. Use the environment's supported permission
process; request reviewed escalation only if required and permitted. If denied,
report that limitation without declaring Chrome closed or IBKR logged out.

Leave recovery to browser_research.py. It may reopen only the existing Canadian
Stock Chrome profile when local checks confirm it is closed and no matching
profile process exists, or open the normal IBKR portal entry if that dedicated
browser has no tabs. Never kill/reset/duplicate a browser or change the original
windows OpenClaw profile. Do not contact other chats or create schedules.

Leave the owner's authorized limited Paper reconnect to the helper, never a
separate AI login or click. With OPENCLAW_PAPER_RECONNECT=true it may click the
unique Log In control inside the disconnected-brokerage warning of an already
verified Paper portal. It rechecks the page/domain/banner and credential/dialog
guards at the click, persists a block before one non-retried attempt and verifies
the result. A failed/uncertain attempt stays blocked until healthy Paper is seen.
Never delete/reset this state to retry. See docs/PAPER_RECONNECT.md.
With OPENCLAW_PAPER_SAVED_LOGIN=true the helper also has the owner's explicit
October 16 permission to select Paper and submit an already-filled official
SSO form. It requires the checked switch, Paper-mode announcement and Simulated
Login button to agree before submission. It persists a separate attempt block
before mutation; no uncertain retry. See docs/PAPER_SAVED_LOGIN.md and preserve
logs/browser_paper_login_state.json. No credential value is returned through
browser-control tools or sent to a model; only IBKR's own form receives its
normal login submission.
Outside these helper-only exceptions, stop at authentication pages. Never
retrieve/type credentials, open password managers, select Live, handle two-factor,
CAPTCHA, active dialogs or competing-session confirmations, place orders or
change transfers/settings/subscriptions. Public research and independent analysis continue
while user attention is needed. Do not invoke a research model, paid API smoke
test, stock session or extra Telegram test.

The helper handles deduplicated Telegram health notices. Use only its safe
status, reason, failed_step, recovery_reason, paper_session_status,
reconnect_attempted, reconnect_succeeded, saved_login_attempted and
saved_login_succeeded diagnostics;
never print raw broker text or private broker URLs. Stay quiet for healthy,
unchanged or busy results. If the helper cannot start, report only a new or
meaningfully changed execution failure using persistent minimal deduplication
state provided by the replacement scheduler, not merely chat memory. Do not
claim this outer failure notifier exists unless it was implemented and tested.
Keep independent trading-analysis jobs and Telegram signal jobs unchanged.
Interpret the helper's independent analysis_scheduler diagnostic separately from
its Paper diagnostic. Never claim analysis is running from healthy Chrome alone.
The helper sends deduplicated failed-task notices; preserve its separate analysis
health state. Never start/repair/re-register analysis tasks from the heartbeat.
```

A non-AI scheduler invokes the same command directly rather than interpreting
this prompt. It must honor the same permissions, state preservation and guarded-reconnect
boundaries. Browser cleanup belongs to actual public research, not an added
heartbeat cleanup command; research retains one IBKR/blank keeper automatically.

## 6. Migration Procedure For Any Assistant

1. Read the entry point and linked guides. Inspect current scheduler identity,
   last completed run and safe browser state. Do not create another heartbeat
   just because a replacement client has ChatGPT/Claude connected.
2. Confirm the owner is requesting deployment, not only documentation. Inventory
   Codex tasks, Windows tasks and any OpenClaw/other-client jobs for this exact
   helper. Identify one intended scheduler; leave six analysis tasks untouched.
3. Preserve the existing prompt, cadence, target, health/recovery/reconnect/saved-login
   state and the owner's opt-in settings.
   Prepare the replacement DISABLED, with exact command, paths, owner account,
   permissions and 24/7 40-minute interval. No credentials in task definitions.
4. Pause the previous monitor using its supported scheduler tools/UI. Wait for
   its current run to finish. If the previous scheduler is inaccessible, obtain
   reliable confirmation it cannot run before activating a replacement.
5. Verify the replacement execution environment with the named offline suite
   and passive `browser_research.py status`. Network denial is not broker failure.
   With owner approval for navigation, use `keepalive --no-notify` once. Do not
   test `place_order.py`, credential entry or a paid provider as part of migration.
   The no-notify helper may perform enabled guarded reconnect/saved-form sign-in; do not force
   disconnection or clear attempt state to test it.
6. Activate the single replacement. Normal production `keepalive` allows the
   helper's deduplicated Telegram notices; obtain approval for any manual live
   Telegram test. Observe one scheduled completion and a later 40-minute run.
7. Confirm safe status, health timestamp where applicable, no duplicate launches,
   no duplicate scheduler and no changes to independent signal tasks. A manual
   success is not proof the recurring trigger works.
8. Update this runbook, Handoff and Operations with scheduler host/name/ID,
   installation date, login/startup requirements and observed results. Only then
   describe the replacement as deployed. Never delete state to simulate recovery.

If activation fails, disable the replacement, wait for its worker to finish and
resume the old schedule only if it remains usable. Record a monitoring gap
honestly; do not activate both to compensate for uncertainty.

## 7. Replacement Scheduler Choices

| Choice | Required capability | Important boundary |
|---|---|---|
| Codex/ChatGPT desktop with local execution | Existing local project, supported schedule, allowed shell/network access | Keep app/VM running; reuse dedicated monitor chat and existing ID when possible |
| Another AI client | Real recurring scheduling and authorized execution on VM 104 | Connected model alone is not a scheduler or Windows shell connection |
| Windows Task Scheduler, no LLM | Interactive existing owner account, exact Python action, working directory and network access | Preferred subscription-independent future option; not currently installed |
| OpenClaw on Ubuntu as scheduler | Supported scheduling plus an authorized Windows execution route | Its Windows Chrome CDP attachment alone cannot run the Windows Python launcher |
| Web/mobile chat without local execution | None suitable for this command | Can guide the owner, not directly run this helper against a local folder |

For a future Windows deployment, use one dedicated task such as
`Trading_IBKR_PaperBrowserCheck`, separate from `IBKR_*` analysis jobs. Required
settings: every 40 minutes indefinitely on all days; run only in the existing
owner's logged-in interactive session; least privileges; no overlapping runs;
start when available; a bounded limit (for example ten minutes, above the
helper's four-minute browser budget). Verify the actual registered trigger,
next-run time and results, not merely a GUI label. Do not store an account
password or install a service/session-0 workaround for Chrome recovery.

If a Windows wrapper is needed, implement and test it as separate scoped work:
preserve the Python exit code; parse the safe final JSON; distinguish broker
health from a process-start failure; deduplicate outer execution failures in a
separate minimal state file; never log secrets/raw broker bodies. The existing
helper handles its own health notices only after it starts. A task that cannot
launch Python cannot send a helper-generated failure notice. No such wrapper
or fallback task is supplied or activated by this documentation-only change.

## 8. Limits And Acceptance Checklist

The VM must be powered on and the existing Windows owner logged in. Locking or
disconnecting RDP is different from signing out. OpenClaw VM/SSH/CLI must work.
No schedule can preserve checks during VM power-off or guarantee weeks of IBKR
authentication. With the owner-enabled saved-form recovery, expiry may recover
when Chrome has filled both fields and the recognized Paper guards pass.
Missing saved fields, unknown forms, challenges and blocked/uncertain attempts
still require owner attention. The banner reconnect is a separate flow.

- Exactly one active 40-minute monitor; cadence includes nights/weekends.
- Helper uses VM 104's original checkout and existing state, not another runner.
- Independent six analysis tasks, model configuration and Telegram quotas intact.
- Correct dedicated browser profile; original browser and firewall unchanged.
- Only the enabled guarded Paper banner reconnect and saved-form Paper sign-in;
  no credential retrieval/typing, challenges/confirmations, financial actions
  or paid research in the heartbeat.
- Expected `busy`, `login_required` and disconnected states handled honestly.
- Normal helper failures deduplicated; outer launch failures covered or explicitly
  documented as unmonitored, without inventing an already-installed notifier.
- Two real scheduled runs observed after activation; no-Telegram manual checks
  and offline tests recorded separately from scheduled production evidence.

## 9. Starter Request For A Replacement Assistant

> Work on Windows trading VM 104 in C:\Trading. Read AGENTS.md, docs/HANDOFF.md,
> docs/OPERATIONS.md and docs/BROWSER_MONITOR_MIGRATION.md. I am asking you to
> migrate the existing 40-minute browser monitor to this client, or to Windows
> Task Scheduler if this client cannot reliably execute local recurring work.
> Preserve state and the six analysis tasks. Prepare the new monitor disabled,
> pause the old monitor and wait for its worker before activating exactly one
> replacement. Use the existing helper and preserve both guarded reconnect and
> saved-form Paper sign-in opt-ins/state. Read docs/PAPER_SAVED_LOGIN.md. No
> credential retrieval/typing, challenges, orders or research-model calls. Obtain
> approval for out-of-schedule live Telegram tests. Verify two
> scheduled runs and document the actual deployed scheduler and remaining limits.

Give that request only when migration is intended. Reading this document or
switching the repository assistant's model does not itself activate migration.
