# Canadian Stock Research And Telegram Alerts

Production workspace on Windows Proxmox VM 104, `C:\Trading`. Canadian watchlist
analysis combines deterministic technical filters, news, an optional API model
and optional OpenClaw browser evidence. IBKR Paper checks read account pages and
can perform the owner's enabled guarded reconnect, never orders or credential
entry. Gateway is disabled by default.

## Start Here

For a complete current map, start with [System Overview](docs/SYSTEM_OVERVIEW.md):
daily timeline, both workflows, source/model boundaries, recovery states,
verification ledger and a portable request for another assistant.
For independent review, combine the current handoff with the
[October 16 code-review repairs](docs/REVIEW_REPAIRS_2026-10-16.md) and
[saved-form Paper sign-in contract](docs/PAPER_SAVED_LOGIN.md). The
[October 14 Review Packet](docs/EXTERNAL_REVIEW_2026-10-14.md) is a historical
snapshot, not a replacement for those later changes.

A new assistant/developer should read [AGENTS.md](AGENTS.md), then
[the complete handoff](docs/HANDOFF.md). Previous chat history is not required
for the documented configuration and workflow. Credentials and actual runtime
state must still be obtained through the owner's existing secure setup.

| Guide | Contents |
|---|---|
| [System overview](docs/SYSTEM_OVERVIEW.md) | End-to-end diagrams, daily timeline, current feature/status map and model-neutral entry prompt |
| [Handoff](docs/HANDOFF.md) | Goals, context, decisions, complete inventory, state and limitations |
| [Architecture](ARCHITECTURE.md) | Hosts, invocation paths, pipeline and contracts |
| [Active strategy](strategies/active_strategy.md) | Exact gates, scores, quotas and options |
| [Configuration](docs/CONFIGURATION.md) | Environment reference and model/provider migration |
| [Operations](docs/OPERATIONS.md) | Commands, schedules, diagnostics, upgrade and restore |
| [VM runbook](docs/VM_OPERATIONS.md) | Machine-specific topology and cautions |
| [Browser workflow](docs/RESEARCH_WORKFLOW.md) | Evidence, recovery and dated live checks |
| [Monitor migration](docs/BROWSER_MONITOR_MIGRATION.md) | Subscription/client changes, portable prompt and single-scheduler transfer |
| [Paper reconnect](docs/PAPER_RECONNECT.md) | Enabled banner reconnect, guards and persistent attempt limit |
| [Saved-form Paper sign-in](docs/PAPER_SAVED_LOGIN.md) | Owner-enabled Paper selection/submission of Chrome's already-filled form; no credential retrieval/typing |
| [Change record](docs/CHANGELOG.md) | Fixes, rationale and verification boundaries |
| [IBKR setup](IBKR_Setup_Guide.md) | Paper-browser use and optional Gateway tools |

## Safe Baseline

Windows VM 104 PowerShell, from `C:\Trading`:

```powershell
.\.venv\Scripts\python.exe -m unittest test_paper_login test_options_review test_analysis_health test_browser_launcher test_browser_research test_market_context test_research_pipeline test_telegram_notify
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe browser_research.py status
```

The first command is the 220-test offline suite. Browser `status` needs permitted
SSH access but does not launch Chrome, log in, navigate or send Telegram. Live
session/connectivity commands are classified in Operations; not every
`test_*.py` is offline.

Healthy Chrome does not prove the analysis tasks ran. See the October 13
[scheduler reliability runbook](docs/SCHEDULER_RELIABILITY.md) for launch checks
and the deployed limited, password-free unattended analysis mode.
For strict CALL/PUT caps, balanced native-Canadian page coverage and daily
Claude/GPT public-review routing, read [Options And Review](docs/OPTIONS_AND_REVIEW_REPAIR.md).

## Day To Day

Keep the VMs powered on. Six limited, password-free Windows background tasks
run independent analysis without a desktop login. The separate 40-minute Codex
check needs Windows logged in and the app running for interactive recovery; it may
reopen a closed dedicated Chrome and navigate only a verified paper portfolio.
Public research does not need IBKR login. Server-side expiry can still require
manual attention. The owner-enabled [saved-form sign-in](docs/PAPER_SAVED_LOGIN.md)
can select Paper and submit only Chrome's already-filled recognized SSO form;
it never retrieves/types credentials or handles challenges. The enabled
[guarded reconnect](docs/PAPER_RECONNECT.md) may restore
an existing disconnected Paper portal, stopping if owner authentication or
confirmation is needed. Research cleanup retains one IBKR tab and closes other dedicated
research tabs; the original OpenClaw browser stays unchanged. Recovery is not
an instant watchdog or an indefinite authentication guarantee.

Changing client or losing assistant access does not transfer the 40-minute
schedule automatically. Follow [Monitor migration](docs/BROWSER_MONITOR_MIGRATION.md).
The helper itself needs no research LLM; a direct Windows scheduler is a future
subscription-independent option, not an installed fallback.

## Private State

`.env`, `logs/`, `.claude/`, `.venv/` and Telethon `*.session` files are Git-ignored.
Browser profiles and Codex/OpenClaw configuration live outside this repo. The
Excel ledger is tracked, so a repository backup can contain financial data.
Do not publish it, assume ignore rules encrypt files, or use Git as the only
backup. See the restore checklist in Operations.
