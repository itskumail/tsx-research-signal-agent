# Canadian Research Workflow

Current behavior, 2026-10-16. This is the browser/evidence companion to the
[complete handoff](HANDOFF.md), [architecture](../ARCHITECTURE.md),
[active strategy](../strategies/active_strategy.md),
[configuration](CONFIGURATION.md) and [operations guide](OPERATIONS.md).
Scope: research and Telegram alerts. No order execution is implemented in this phase.

## Sources And Selection

1. Scan the Canadian watchlist using independent structured data and news.
2. Determine Canadian regime from XIC, comparing with the preceding daily
   close. US context stays separate. Premarket, stale and unavailable readings
   are labelled, never silently replaced with bullish or zero values.
3. Request two years of daily history; require 250 completed bars for EMA-200.
   Remove the current incomplete daily bar before scoring.
4. Apply price, volume, spread when available, earnings, market-window and
   technical blockers. AI catalyst/deep-scan promotions cannot erase them.
   TSX windows use the XTSE exchange calendar (holidays and early closes);
   the earnings horizon counts trading sessions, not calendar days.
5. DeepSeek requests triage across successfully scored watchlist names and
   researches the shortlist. Missing final verdicts are incomplete coverage,
   not bearish/bullish decisions; reasoning-only text is not final JSON.
   Batches of ten and one missing-only repair pass of five share a 360-second
   dispatch budget. The 3-month/60-day factsheet fields now use explicit trailing
   windows rather than mislabelling the whole two-year price history.
6. OpenClaw checks six primary names through the dedicated Chrome. Session 2
   interleaves ranked LONGs with strict technical PUT previews and accepted AI
   SHORT names. When Paper is ready, Session 2 first reads IBKR CDE contract
   snapshots using its own bounded stage. MX collection covers selected names
   without qualifying matching-direction IBKR quotes, apart from definitive
   no-CDE/no-month/no-expiry results, before secondary Barchart checks. The
   public work budget starts after lock/recovery and the IBKR stage and reserves
   review time. Both sources' diagnostics survive fallback. A missing option side
   may receive replacement attempts if its first chains fail, capped at
   twice the primary symbol limit and the same deadline. The optional reviewer
   receives at most six names, prioritizing matching-direction verified quotes.
   Additional names without notes are explicitly labelled. The selected
   daily OpenClaw model reviews public excerpts without tools. Its notes cannot
   create contracts or replace observed numbers. Source failures are optional.
   The review subprocess sets `CLAUDE_CODE_SAFE_MODE=1` so unrelated native
   Claude startup customizations do not load. Existing authentication and
   managed permission policies remain; general model defaults and Claude settings
   are unchanged. GPT's single-model native transport overlay is recorded in
   the repair runbook. The normal browser/keepalive calls do not
   receive this override. See the [Claude CLI reference](https://code.claude.com/docs/en/cli-reference).
   Each review receives at most two 1,500-character source excerpts and six
   representative quote rows per symbol, together with the full qualified-quote
   count. It is told that samples are incomplete and asked for concise notes.
   The original source records, complete validated contract list and selection
   gates are not truncated or changed. Models alternate daily in Denver:
   Claude Opus 5.5 / GPT-6.1 Sol, medium by default, only medium/high permitted,
   with the alternate as optional fallback. An explicit zero-tool-call summary
   is required. The versioned SSH/Node bridge keeps native exec for Claude and
   uses OpenClaw's protected public completion SDK for GPT, with `tools: []`.
   The fallback shares the original review deadline. Model outcomes/identity/
   effort and cleanup results are recorded;
   cards label missing review rather than silently implying completion. See
   [Options And Review Repair](OPTIONS_AND_REVIEW_REPAIR.md).
7. Recheck the dispatch window and send Telegram cards, with source/timing
   limitations. Daily-bar entry levels are references, not executable quotes.

The existing scoring thresholds and quotas otherwise remain. This is not
evidence of profitability; rerun out-of-sample evaluation before relying on
the changed filters. Open interest and US option positioning are context,
not proof of directional buying or Canadian support/resistance.

## Canadian Options

Priced Canadian option cards require native CAD/CDE contracts parsed from
the recognized visible MX table or the verified Paper portal's GET-only CDE
snapshot adapter. Quotes need an explicit update timestamp no more
than 30 minutes old, positive non-crossed bid/ask, spread at most 25% of the
midpoint, and OI at least 100 or volume at least 10. Select listed expiries
21-45 calendar days away. Do not manufacture CALL/PUT ideas to fill a quota.

Missing dates, blocked pages, unknown layouts, wide spreads or missing
contracts produce only an unpriced underlying watchlist. No converted US
strike is substituted. Delayed data is acceptable but labelled. Quote time
and crawl time are different. Missing Greeks stay unknown. Premium is quoted
per share; contract totals require a known multiplier (adjusted contracts
must not be assumed to represent 100 shares).

IBKR snapshots require an integer contract ID and R/D availability; delayed
timestamps are shifted back 15 minutes. Blank/null/prefixed prices remain
pending within the eight-round limit. The collector never globally cancels the
portal's quote streams. Missing quality fields can trigger bounded MX fallback,
not relaxed quote gates. Historical strike-sampling prices are explicitly
previous-close references, not current spot or current moneyness. Additional
visible-chain/TradingView checks remain follow-ups. Paper data is not a
real-account holdings feed. Full repair details and tests:
[October 16 review repairs](REVIEW_REPAIRS_2026-10-16.md).

## Browser And Session Checks

- Dedicated profile: `canadian-stocks`, attach-only CDP on 192.0.2.15:9225.
  The original `windows` OpenClaw profile is unchanged.
- Research has a shared OS lock, a 300-second public collection/review work budget
  starting after access is acquired (plus the optional separate IBKR stage), and a
  separate 90-second cleanup budget. Command calls are bounded at 35 seconds.
- Read-only command observations retry once for transient connection failures
  within the same command budget. Clicks, navigation and tab closes are never
  blindly repeated after an uncertain response.
- Paper activity (since October 15) is two real OpenClaw mouse clicks: the
  portal's `Positions` tab, then back to `Dashboard`, each confirmed active
  before it counts. Auth/dialog/Paper guards are re-checked before each click.
  IBKR's inactivity warning gets one click on its allow-listed stay-logged-in
  button; non-modal panels are ignored if they do not cover the tab.
- Cleanup closes the dedicated research tabs and retains one IBKR tab. If
  no IBKR page exists, it leaves one blank tab so Chrome stays running.
- The Codex recurring check runs `browser_research.py keepalive` every
  40 minutes, 24/7, including after market close. It performs normal portfolio
  navigation only after verifying the paper banner. With the enabled owner opt-in,
  it may reconnect through the unique disconnected-Paper banner `Log In` control.
  Since October 16 an additional owner-enabled saved-form guard can select
  Paper and submit an already-filled official SSO form, with a separate durable
  attempt block. It never retrieves/types passwords, selects Live, handles
  challenges/confirmations, changes settings or places orders. See
  [Saved-form sign-in](PAPER_SAVED_LOGIN.md).
  See [Paper reconnect](PAPER_RECONNECT.md) for guards and persistent attempt limits.
- Keep the VM, OpenClaw and Codex running, with the Windows user logged in.
  A closed dedicated Chrome can now be reopened as described below. A background/minimized
  page can omit accessibility refs; the helper then navigates to an observed
  same-portal portfolio link. Read-only activity is not a guarantee that IBKR
  will preserve authentication for weeks. Server-side expiry/maintenance may
  reach SSO; enabled saved-form recovery may restore it, but missing fields,
  challenges or a blocked/uncertain attempt still require owner attention.
  The recurring task resumes after the app is running;
  it is not an independent Windows service.
- Telegram health notifications are deduplicated by state. Failed sends are
  retried on the next check. Healthy unchanged checks and a busy lock stay
  quiet. Missing/login/live-unverified sessions never stop independent scans.
- Connection failures record safe `reason` and `failed_step` codes in the health
  file. `control_unavailable` is not proof that Chrome closed or IBKR logged out;
  `navigation_failed` preserves the preceding verified paper-session state.
  Notices distinguish changed failure reasons rather than masking every error
  as browser unavailable. Telegram errors log only status/type, never response
  bodies or exception text containing bot-token URLs.
- The Codex helper requires permitted SSH/HTTPS access. Use the environment's
  supported permission process, with escalation only when required and allowed;
  a sandbox denial is an
  execution limitation, not evidence about the broker's session.

## Browser Recovery

The existing 40-minute check and the start of actual public browser research
call the local Windows launcher in `browser_launcher.py`. Plain `status` checks
remain passive. No new recurring task or AI-to-AI messaging is required.

The launcher checks loopback port 9224 and the existing dedicated profile
process. Only a confirmed connection refusal, an absent matching Chrome process,
an existing installation/profile and an interactive Windows session permit a
launch. Timeouts, permission errors and unknown process state do not count as
proof that Chrome is closed. An existing profile process is never killed,
reset or duplicated. Failed launch attempts have a ten-minute cooldown.

Recovery uses the existing `CanadianStockChrome\Profile` user-data directory,
the same Chrome executable and debugging arguments as the desktop shortcut,
and the ordinary IBKR portal entry URL. It does not read credentials, select
Paper, submit login forms or alter the original `windows` profile. If a running
profile has no tabs, the helper can open that same portal entry URL. Once Chrome
and OpenClaw attach successfully, public MX/Barchart research can proceed even
while IBKR is logged out. The usual cleanup retains the IBKR login/portal tab;
the monitor's separate enabled saved-form branch may then sign in, or request
owner attention through a deduplicated Telegram notice. Public research itself
does not call that authentication branch.

This is recovery at the next check or research run, not an instant watchdog.
The VM must be on and the recovery process must be in an interactive Windows
session. The Codex recurring task also needs the desktop app running. Since the
October 13 repair, scheduled analysis runs password-free S4U in background
session 0; it can attach to existing Chrome but must not launch GUI Chrome there.
Independent analysis no longer needs a desktop login. See
[Scheduler reliability](SCHEDULER_RELIABILITY.md). Before that repair, research jobs could
recover Chrome on demand without Codex when those jobs run normally. No separate
Windows browser watchdog was installed. Set `OPENCLAW_BROWSER_AUTORESTART=false`
to disable automatic launches without disabling independent analysis.

## Gateway And Failure Paths

`IBKR_GATEWAY_ENABLED` defaults to false. The scheduler skips its probe and
Python skips its connection. Existing explicitly enabled Gateway reads remain
optional. With Gateway off, live portfolio review is unknown, never zero.

Browser ready: attempt bounded evidence checks; an IBKR login is not required
for public browser research. Browser closed: attempt scoped recovery first.
Browser still unavailable or busy: use independent analysis and label browser
verification unavailable.
Both inputs missing: the same independent analysis and scheduled Telegram
flow continue. DeepSeek missing: retain deterministic analysis; AI-dependent
promotion lanes do not gain permission to send.

## Checks And Logs

Run from `C:\Trading` using the project virtual environment:

```powershell
.\.venv\Scripts\python.exe browser_research.py status
.\.venv\Scripts\python.exe browser_research.py recover
.\.venv\Scripts\python.exe browser_research.py keepalive --no-notify
.\.venv\Scripts\python.exe browser_research.py cleanup
.\.venv\Scripts\python.exe -m unittest test_browser_launcher test_browser_research test_market_context test_research_pipeline test_telegram_notify
```

Logs: dated `market_snapshot_*`, public `browser_research_*`, minimal
`browser_session_state.json`, `browser_recovery_state.json` (UTC launch-attempt
timestamp only), `browser_reconnect_state.json` (blocked flag and UTC check time),
scheduler/session logs and existing signal logs. Preserve reconnect state across
restarts/migration; never clear it to force retries.
Do not store broker page bodies, balances, account IDs, credentials or tokens.
Keep test Telegram sends mocked unless the user requests a live test.

## Verification On October 11

The owner explicitly requested unattended reconnection from an already verified
Paper page without entering credentials. One guarded banner click changed
`paper_disconnected` to `ready`, without credentials or confirmation handling.
Production opt-in and the existing monitor prompt were updated in place; no
cadence/destination change or duplicate schedule. All 107 offline tests passed,
including no-retry clicks, persistent uncertain-attempt blocks and guard denials.
This Sunday session-state test does not establish live quotes or improved data
entitlements. Full authentication and multi-week retention remain unverified.

## Verification On October 9

The read-only paper portfolio navigation succeeded. A public TOU research
pass collected MX and Barchart pages, parsed 11 native contract quotes meeting
the quote-quality checks, and removed the research tabs while preserving
IBKR. A further post-upgrade MX pass parsed eight qualified quotes and cleaned
up its research tab; the Barchart cross-check was unavailable on that pass.
An independent Barchart retry read the public TOU page without a browser
challenge and cleaned up its tab, again retaining IBKR. The updated paper
session helper then reported `ready`; the recurring check remains active.
No real Telegram test or order was sent. The optional review initially timed
out after the 2026.9.9 upgrade. Although OpenClaw's readiness probe reported an
indeterminate Claude route, the native sign-in check succeeded and a direct
tool-free Claude request finished in seven seconds. Startup-isolated OpenClaw
calls then succeeded: a tiny request in 52 seconds and a two-source test-excerpt
review in 68 seconds, with no tool calls accepted. That subprocess-only fix is
now in `research_pipeline.py`; the exact offending customization was not
identified. A full-page run still returned no usable review, so the review
request was also bounded and its output instructions shortened. The next full
TOU pass collected MX and Barchart, kept eight qualified contract quotes,
received Claude notes in 69 seconds within the normal 90-second review budget,
and removed both research tabs while retaining IBKR. Total pass: 162 seconds.
No login, provider/model setting or browser configuration changed. Source
collection remains independent if a future model call is unavailable.
Ninety-three offline regression tests passed, including failure classification, read-only retries,
snapshot fallback, notification redaction and offline browser-recovery cases. Retain the
single dedicated browser: its verified cleanup leaves IBKR as the only tab
without touching the original `windows` profile.
Long-duration authentication and multiple scheduled keepalive cycles have not
been verified; the helper does not promise an indefinite authenticated session.
The recovery launch branch was tested with mocked endpoints and processes,
including duplicate prevention, failed-attach handling and retry cooldown.
The live local endpoint/process checks found the existing dedicated Chrome
running and correctly returned `ready` without launching another instance.
OpenClaw's dedicated-profile recovery check also returned `ready`.
The subsequent live paper-session navigation check returned `ready`, with
Telegram test sends disabled.
The authenticated browser was not deliberately closed to test recovery.
