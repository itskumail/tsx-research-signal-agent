# VM Operations Runbook

Baseline: 2026-10-09. This is the machine-specific companion to
[Operations](OPERATIONS.md) and [Handoff](HANDOFF.md). Use those for current
strategy, model configuration, diagnostics and command safety.

## 1. Hosts And Owner Workflow

| Host | Role |
|---|---|
| Proxmox VM 104, Windows `ProxMox-Trading`, `192.0.2.15` | Production project `C:\Trading`, Windows schedules, Chrome and Codex |
| Proxmox VM 105, Ubuntu OpenClaw, `192.0.2.16` | OpenClaw gateway/CLI and optional model review |
| Old laptop `192.0.2.13` | Retired runner; its six tasks stay disabled |

Owner: the owner. Give any user-run Linux/admin instructions one step at a time,
state the host, and verify outcomes. Do not ask the owner to perform steps that
can be completed safely with existing tools. Obtain approval before deleting
state, hard-to-undo changes, schedule re-registration or out-of-schedule live
Telegram tests. Never ask for passwords in a documentation file.

One production runner only. Concurrent deployments can duplicate alerts and
use the same Telethon login/state inconsistently. RDP disconnect/Windows lock
is acceptable. Sign-out prevents interactive GUI recovery/app monitoring, but
the six limited S4U analysis jobs no longer require a desktop login.

## 2. Windows VM Baseline

Windows timezone was checked during documentation: `Mountain Standard Time`
with daylight saving enabled. User-facing times are Calgary local MDT/MST;
market/quote processing uses America/Toronto and UTC as appropriate.

Historical deployment record describes Windows 11 Enterprise, 4 cores,
16 GB RAM, 120 GB disk, GPU passthrough, BitLocker/vTPM and Duo login. These are
inventory notes, not newly verified hardware/security configuration. Proxmox
VM label may differ; use VM ID/IP/Windows hostname to identify the machine.

The owner reports a previous freeze with nested virtualization. Do not install
WSL, Hyper-V or Virtual Machine Platform here. Linux services remain on VM 105.
Keep VM power/sleep settings compatible with unattended analysis; no service can
run while the host is powered off or Windows requires login after reboot.

## 3. Paths

| Item | Path or value |
|---|---|
| Project | `C:\Trading` |
| Python venv | `C:\Trading\.venv\Scripts\python.exe`, Python 3.12.10 at handoff |
| Full dependency snapshot | `C:\Trading\requirements-lock.txt` |
| Private settings | `C:\Trading\.env` |
| News authorization | `C:\Trading\claude_news_session.session` |
| Logs/state | `C:\Trading\logs` |
| Legacy Gateway install | Historical `C:\Jts\ibgateway\1051\`; verify before optional use |
| Chrome executable | `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| Dedicated user-data directory | `C:\Users\Trader\AppData\Local\CanadianStockChrome\Profile` |
| Desktop shortcut | `C:\Users\Trader\Desktop\Canadian Stock Chrome Browser.lnk` |
| Codex automation | `%USERPROFILE%\.codex\automations\ibkr-paper-browser-check\` |
| OpenClaw CLI on Ubuntu | `/home/trader/.nvm/versions/node/v24.21.0/bin/openclaw` |

The shortcut executable/arguments/profile were verified during recovery work.
Git branch/remote and hardware notes can change; inspect them rather than
assuming a historical branch or remote is still current.

## 4. Chrome And OpenClaw Connection

Verified dedicated shortcut arguments:

```text
--remote-debugging-port=9224 --remote-debugging-address=127.0.0.1 --user-data-dir="C:\Users\Trader\AppData\Local\CanadianStockChrome\Profile" --no-first-run
```

The profile is intentionally separate from the original Claw browser. The local
recovery helper uses the same executable/profile/debugging arguments plus the
ordinary `https://ndcdyn.interactivebrokers.com/portal/` entry page. It does not
use saved credentials or operate login selectors.

| Connection | Endpoint |
|---|---|
| Dedicated native Chrome CDP | `127.0.0.1:9224` on Windows |
| Dedicated bridge for VM 105 | `192.0.2.15:9225` forwarding to loopback 9224 |
| Allowed bridge source | OpenClaw VM `192.0.2.16` through existing firewall scope |
| OpenClaw dedicated profile | `canadian-stocks`, attach-only |
| Original Chrome/profile route | Loopback 9222 and .15:9223, profile `windows`, unchanged |

Do not change attach-only settings to launch Ubuntu Chrome as a substitute.
The Windows local launcher supplies recovery. Do not expose CDP broadly to
repair a timeout, reset the profile, disable host checks or move saved broker
credentials between profiles.

Read-only Windows checks:

```powershell
.\.venv\Scripts\python.exe browser_research.py status
netsh interface portproxy show v4tov4
```

The second command inventories forwarding rules without altering them. Verify
9225 targets loopback 9224 and original rules remain intact. Inspect firewall
scope with authorized Windows tools when an attach failure specifically points
there. Rebuilding bridges/firewalls requires an explicit deployment change,
not a guessed all-network allow rule.

SSH uses `trader@192.0.2.16`, existing key/known-host authentication. Python
passes the CLI directory in remote PATH; a Node/OpenClaw upgrade may move it.
Verify installed CLI/version on Ubuntu before updating `OPENCLAW_CLI_PATH`.
OpenClaw/provider configuration and native sign-in live on Ubuntu outside this
repository; use supported product tools, not blind file overwrites.

## 5. Browser Availability And Account Availability

These are different checks:

- Passive OpenClaw status verifies the dedicated profile/control readiness.
- Local recovery verifies Chrome endpoint/process before an allowed launch.
- Paper-session check verifies a simulated-account banner, then ordinary
  portfolio navigation. Browser-open is not account-authenticated.
- Public MX/Barchart research does not need broker login.
- A paper session does not prove a real-account portfolio or live entitlement.

Gateway and Client Portal can conflict over the same login session, as observed
by the owner. Current workflow leaves Gateway disabled. The owner-authorized
opt-in helper may reconnect an already verified Paper portal through its unique
disconnected-banner control; it does not accept competing-session confirmations
or operate credential forms in that banner flow. Reconnecting may still displace
another same-user brokerage session. The separate October 16 owner-enabled
[saved-form sign-in](PAPER_SAVED_LOGIN.md) may recover SSO using Chrome's already-filled
Paper form; missing values/challenges still require the owner. The attempt blocks
are separate and must both survive migration. See [Paper reconnect](PAPER_RECONNECT.md).

## 6. Schedules

Six weekday Windows tasks: 05:45, 07:00, 08:30, 11:00, 12:30, 13:55 Calgary.
They invoke `powershell.exe` with `scheduled/run_session.ps1` and one of the
six session arguments. Principal is password-free S4U/limited. Task code has a
30-minute limit and start-when-available behavior.
October 13: automatic interactive starts failed despite an active owner session.
A limited password-free background mode passed automatic/local/SSH/HTTPS fixtures;
the owner-approved repair was deployed at 11:57 Denver time. All six installed
tasks were verified enabled, S4U/limited, with unchanged next times and limits;
all diagnostic task definitions were removed. Read
[Scheduler reliability](SCHEDULER_RELIABILITY.md) before applying it. Do not use
the background mode as a workaround to launch Chrome in session 0.

The EOD task is a summary/review, not an order-flattening job. Old installed
Task Scheduler descriptions can lag behind code; verify actual actions and
triggers rather than trusting a historical description. Documentation work
does not re-register the installed tasks.

All six installed task names/times, S4U logon and limited run level
were inspected after repair. Trigger start boundaries carry
the October -06:00 offset; recheck local `NextRunTime` after DST/timezone changes
instead of assuming the calendar display and trigger serialization are identical.

Separate Codex task `ibkr-paper-browser-check`: every 40 minutes 24/7.
It needs Codex running. Windows research can recover Chrome on demand when it
runs, but no separate all-night Windows watchdog exists. Full procedures:
[Operations](OPERATIONS.md).

The task's current destination is the separate `IBKR Browser Monitor` chat.
For assistant subscription expiry or a client change, use
[Monitor migration](BROWSER_MONITOR_MIGRATION.md), including the portable prompt
and Windows-side execution requirements. The helper makes no research-model
request; no alternate scheduler is currently installed. Ubuntu browser attachment
alone is not a Windows command-execution route.

## 7. Optional Gateway

`IBKR_GATEWAY_ENABLED` defaults false. Do not launch Gateway as part of routine
research/paper checks. Existing manual Gateway tools need the account connected
and its configured socket port. The project's configured port is 7497; that is
not a universal default for every IBKR product/install.

If explicitly using the legacy path, keep read-only API and localhost access.
A socket probe does not prove login or market-data permission. Existing
historical read-only synchronization errors are not instructions to turn off
read-only protections. Missing Gateway leaves holdings/spreads unverified, not
zero or a guaranteed tight spread. Native public option evidence does not
fall back to FX-converted US strikes.

## 8. Migration History And Current Cautions

2026-10-02 laptop-to-VM migration transferred code, `.env`, Telethon session
and logs privately, recreated Python and registered the six tasks. Laptop
triggers were disabled. Old notes about first-run verification, API balance,
provider plan or credential rotation are historical, not current state.

Do not duplicate the old deployment. Preserve logs/cursors and private
configuration during upgrades. Pushing code and restoring credentials are
separate operations; Git ignores key operational state. The tracked ledger
still contains private financial data. Follow the complete backup/restore
checklist in Operations, not a Git-only recovery assumption.
