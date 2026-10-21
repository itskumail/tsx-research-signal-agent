# Independent Review Packet: Canadian Research System

Prepared October 14, 2026, America/Denver. Workspace: `C:\Trading`.
Purpose: let another model review the actual implementation and recent repairs
without depending on this chat. This is not evidence of strategy profitability.

**Historical snapshot:** this packet and any matching source bundle predate the
October 15-16 IBKR snapshot integration, the later code-review repairs and
owner-authorized saved-form sign-in. For a current review, start with
[System overview](SYSTEM_OVERVIEW.md), [Handoff](HANDOFF.md),
[Review repairs](REVIEW_REPAIRS_2026-10-16.md) and
[Saved-form Paper sign-in](PAPER_SAVED_LOGIN.md). Keep the dated findings below
as history; do not use them to restore superseded behavior or test counts.

## 1. Start Here

Read this packet, `AGENTS.md`, `docs/HANDOFF.md`, `ARCHITECTURE.md`,
`strategies/active_strategy.md`, and the source files identified below.
The review bundle contains source/docs/tests only. It deliberately excludes
`.env`, credentials, broker pages, account numbers/values, session databases,
private broker URLs, original screenshots, runtime logs and Git history.

The worktree already contained substantial earlier changes and untracked files.
Do not treat every difference from Git HEAD as an October 14 change. The dated
change inventory in section 9 identifies this repair's scope. Production logs
and sent-alert history were preserved; no quotas/cursors were reset.

### Review Request To Paste Into Another Model

> Perform a skeptical code review of this Canadian stock-research and Telegram
> alert system. Read EXTERNAL_REVIEW_2026-10-14.md and AGENTS.md first. Check the
> executable code rather than accepting the documentation's claims. Identify
> bugs, incorrect financial/data assumptions, missed candidates, unfair coverage,
> budget/cost risks, stale-quote acceptance, session-monitor failure modes and
> missing tests. Give severity, file/function or verified line references, a
> concrete failure scenario, and the smallest corrective change. Distinguish
> observed defects from hypotheses and strategy choices requiring backtesting.
> Do not infer profitability, guarantee two PUT opportunities, or equate a cheap
> share or account cash with option-contract liquidity. Do not request secrets.
> Review only: do not run live scripts, APIs, Telegram sends, browser/account
> actions, authentication, order placement, task registration or state resets.
> The named offline tests mock external actions, but the bundle is not a full
> runnable deployment and some dependencies may be absent. End with remaining
> uncertainties and a focused test plan. Explicitly assess all questions in
> section 12, including whether the new fixes introduce regressions.

## 2. Owner's Intended Behavior

1. Research Canadian names, not a US regime relabelled as Canadian.
2. Consider the entire successfully scored watchlist, including smaller names.
3. Use indicators, news and DeepSeek for screening/ranking; supplement with
   actual public-browser evidence and a separate Claude/GPT review.
4. Deliver Telegram research alerts, not orders. Paper order execution is not
   an implemented feature.
5. Aim for two qualified CALL and two qualified PUT cards daily. These are
   independent maximums, not a requirement to fabricate four recommendations.
6. Continue independent analysis when Gateway, Chrome, IBKR login or an optional
   model is unavailable. Unknown holdings must not become zero holdings.
7. Keep a dedicated Chrome profile separate from the original OpenClaw browser.
   Clean research tabs; retain one IBKR tab or a blank keeper.
8. Check Paper every 40 minutes, including nights/weekends, with deduplicated
   health notifications. This is not a promise of permanent authentication.

## 3. Hosts And Scheduling

| Component | Location and responsibility |
|---|---|
| Windows trading VM 104 | `192.0.2.15`, production checkout `C:\Trading` |
| Python | `C:\Trading\.venv\Scripts\python.exe` |
| Ubuntu OpenClaw VM 105 | `192.0.2.16`, existing key-authenticated SSH |
| Dedicated Chrome | `canadian-stocks`; local CDP 9224, existing scoped bridge 9225 |
| Original Chrome | `windows`; separate 9222/9223 route, unchanged |
| Optional IBKR Gateway | Disabled by default; not a prerequisite for public evidence |
| Retired laptop | `192.0.2.13`; not a second production runner |

All times below are Calgary/Denver local time, not Toronto time:

| Time | Task | Work |
|---|---|---|
| 05:45 weekdays | `IBKR_PreMarket_US` | News and contextual heads-up |
| 07:00 weekdays | `IBKR_PreMarket_CA` | Canadian pre-open heads-up |
| 08:30 weekdays | `IBKR_Session_1` | Stock scan and alerts |
| 11:00 weekdays | `IBKR_Session_2` | Stock scan plus separate options scan |
| 12:30 weekdays | `IBKR_Session_3` | Stock scan and alerts |
| 13:55 weekdays | `IBKR_Session_4` | EOD summary, optional holdings review |
| Every 40 minutes, 24/7 | Codex heartbeat | Existing Python Paper/analysis-health helper |

The six analysis jobs use limited, password-free S4U background mode. They do
not need the desktop login for independent analysis. GUI Chrome recovery does
need an interactive Windows session. There is one active browser monitor,
automation ID `ibkr-paper-browser-check`, in a separate monitor chat. No new
monitor or cadence change was installed in this repair.

## 4. Stock Pipeline, In Execution Order

1. Task Scheduler starts `scheduled/run_session.ps1`, then `session_runner.py`.
   A production-runner lock prevents overlapping trading sessions.
2. Gateway is probed/connected only when enabled. Independent history/news
   remain available without it. Optional account data stays explicitly unknown.
3. Collect news and market context. XIC determines Canadian regime using the
   previous completed session's close. SPY/QQQ are separate US context.
4. Enforce the XTSE trading calendar/window for qualified dispatch. Scheduled
   informational jobs and exchange trading sessions are different concepts.
5. Load the watchlist and fetch two years of daily history. Require at least
   250 bars and remove the incomplete daily bar before scoring.
6. `signals.score_symbol()` applies tradability, technical and mandatory gates.
   Promotions cannot erase `Signal.hard_blockers`.
7. Build factsheets over all successfully scored names. Daily fundamentals and
   separately labelled US-chain OI context may supplement price/news evidence.
8. `deep_scan.run_triage()` requests LONG/SHORT/NONE verdicts. Missing results
   are unknown, not NONE. LONG/SHORT injection requires conviction at least 4.
9. Enrich and rank eligible stock candidates with the configured API model.
   Required approvals do not default to approval if the API fails.
10. Session 2 builds strict CALL/PUT technical previews over cached bars, without
    external earnings/US-chain requests. A preview only allocates browser pages.
11. `verify_shortlist()` collects public MX evidence, then secondary Barchart
    pages if time remains, followed by optional model review.
12. Recheck stock blockers, market window, approval, cooldown, sector cap and
    quotas before Telegram dispatch. Only successful sends count.
13. Session 2 then runs the options path below. Stock quota exhaustion does not
    prevent that independent options path.
14. Save audit records, send session summary and disconnect any optional Gateway.

Stock caps: eight cards/day, three/session, with existing diversity/cooldown
rules. The October 14 repair did not change these thresholds.

## 5. Options Pipeline And Meaning Of Liquidity

### Direction And Selection

- Native CAD options do not require a US dual listing.
- Final option scoring checks daily history, stock tradability and earnings.
- Strict direction needs at least three points and must beat the opposite side.
- Strict PUT requires structural bearish evidence: bearish trend, death cross,
  or MACD below signal. Overbought alone is insufficient.
- Relaxed PUT evaluation occurs only over qualifying DeepSeek SHORT names if
  there is no priced strict PUT. It rejects close above EMA-50 and requires
  relative weakness. It cannot invent an option contract.
- Final scoring can differ from preview because earnings and contract checks
  are intentionally not skipped in final scoring.

### Native Contract Gates

Every priced CAD recommendation must have a recognized, observed MX table row:

| Gate | Current rule |
|---|---|
| Identity | Exact underlying symbol, CALL/PUT right, CAD currency, CDE exchange |
| Provenance | `MX visible table` and actual `m-x.ca` host |
| Table | Recognized paired CALL/PUT layout and matching symbol page title |
| Quote time | Explicit source update label; not time the crawler read the page |
| Freshness | At most 1,800 seconds old; at most 60 seconds future tolerance |
| Expiry | Listed contract 21-45 calendar days away |
| Prices | Positive strike; `0 < bid <= ask` |
| Spread | `(ask-bid)/mid <= 25%` |
| Activity | Open interest >= 100 OR day volume >= 10 |

These numerical thresholds are this project's policy, not an exchange rule or
proof of profitability. The October 14 repair retained them. They can be
reviewed/backtested separately, not silently removed to satisfy a quota.

**Contract market liquidity is not account cash, buying power, IBKR's "Excess
Liquidity" field, or the price of the underlying shares.** The data is read
from public MX option rows; Gateway is not needed. A low-price stock can have
an inactive or wide-spread option at a particular strike/expiry.

Account affordability is not evaluated by this path. Paper balances are not
real-account balances. No multiplier, Greeks, IV, fill or total-contract cost
is inferred when unavailable. The screenshot supplied by the owner showed a
portfolio, not evidence for a particular option's bid/ask/OI/volume.

Contracts are revalidated immediately before priced dispatch. No US-chain
conversion substitutes for a Canadian contract. Successful daily delivery is
capped separately at two CALLs and two PUTs, four combined. Unfilled slots do
not transfer to the other side. A directional idea without a validated contract
is labelled unpriced, with no recommended strike, expiry or premium.

## 6. October 14 Observed Incident

These are recorded-run facts, not a live market survey or hindsight trade advice:

| Session 2 record | Observation |
|---|---|
| Eligible names | 75 |
| DeepSeek verdicts | 41; 34 missing; coverage incomplete |
| DeepSeek failure label | At least one final-answer `output_limit` |
| Browser names | SU, NDA, CNQ, NXE, MFC, DML |
| Priced pool | Three CALLs, zero PUTs |
| Successful delivery | Two CALLs: CNQ and SU; zero PUTs |
| Unpriced PUT audit count | Five entries; old count was not guaranteed deduplicated |
| Supplemental reviewer | GPT-6.1 Sol, accepted metadata for the six names |

The saved MX evidence contained PUT rows for SU/CNQ/MFC, but their underlying
signals were CALLs. Existence of PUT contracts is not a bearish signal.

For NXE and DML, the examined November 20 rows within the DTE window failed the
activity rule. Examples from the historical saved excerpt: NXE strike 11.50
had OI 15 and volume 8; DML strike 3.50 had OI 47 and volume 0. Some other rows
also failed spread/zero-bid requirements. This describes the examined rows,
not every strike/expiry in the market. NDA's request returned a generic MX
selector, not an identity-verified NDA chain. No automatic symbol substitution
was made; for example, NDA must not silently become NA.

The old first-six browser limit could stop after failed chains even while
other bearish candidates remained. The old top-four unpriced notice could
also show only CALLs. These are coverage/presentation defects, not proof that
Canada had no PUT opportunities or that an LLM could not identify weakness.

## 7. October 14 Coverage Repairs

### DeepSeek Completion

Factsheet labels were also corrected: the full two-year return/high had been
presented as three-month momentum/60-session high. New `ret_3m_pct` uses 63
completed sessions, and the high uses trailing 60 sessions. This is a data
semantics repair, not a directional threshold change; it is not proven to be
the cause of today's missing PUT alerts.

`deep_scan.py` now uses first-pass batches of ten rather than 25. It preserves
valid verdicts and makes one missing-only repair pass in batches of five.
Wrong symbols, invalid direction and non-integer/out-of-range conviction do
not become approval or NONE. Valid first-pass decisions are never overwritten.

There is a 360-second dispatch budget, provider request timeouts capped at
60 seconds, and no automatic SDK transport retries for these bounded triage
requests. The first-pass timeout allocation reserves time for repair. This is
not a guarantee that every provider request will complete or that a sequence
of HTTP operations cannot exceed a nominal per-operation timeout. Missing
verdicts and per-pass requested/completed symbols remain in the audit.

The provider, model, keys, final-content-only rule and output-token caps were
not replaced. Smaller batches and repair calls can increase request count and
allowance use. No out-of-schedule paid triage was run for this repair.

### Browser Replacement Coverage

`verify_shortlist(symbols, option_directions=...)` keeps six primary names.
Only when supplied option directions, it may examine later candidates of a
side lacking verified matching quotes, aiming for up to half the primary
limit per side. It stops at twice the primary symbol limit, exhausted queue,
or the existing collection deadline. Default maximum is therefore twelve
candidate records, not an exhaustive 75-name option-chain crawl.

Replacements reuse the same `CanadianResearchBrowser.run_research()` call,
shared lock, deadline and cleanup `finally`. They precede secondary Barchart
pages and cannot consume the reserved optional-review budget. Ordinary stock
sessions without option directions retain the six-primary-name behavior.

Observed MX selector absence is only a warning because rendered selectors may
be incomplete. It does not prove a symbol lacks options or authorize guessing
a replacement ticker. Actual quote identity/provenance gates remain mandatory.

Validation now records per-right rejection counts and safe reasons, including
invalid identity, missing/stale quote time, DTE, bid/ask, spread and contract
activity. The optional model receives these rules/counts but cannot create
facts or change gate results. At most six symbols receive model excerpts;
matching-direction verified contracts are prioritized. Additional names without
review are explicitly labelled `review_symbol_budget` when notes are accepted.

### Telegram And Audits

Unpriced ideas are deduplicated by symbol/right. The short unpriced watchlist
shows up to two CALL and two PUT ideas, interleaved, rather than letting the
highest-ranked CALLs hide every PUT. They remain unpriced research ideas.

`options_coverage_*` now records unpriced names, checked symbols, per-candidate
collection/contract rejection diagnostics, actual successful sends and an
explicit distinction between contract liquidity and account funds. These new
fields appear on future runs; earlier audit files are not rewritten.

## 8. The 40-Minute Monitor

### Exact Current Execution

Codex invokes, on Windows VM 104 in `C:\Trading`:

```powershell
.\.venv\Scripts\python.exe browser_research.py keepalive
```

The helper checks six analysis tasks independently, then takes the browser lock
and checks only `canadian-stocks`. Busy is quiet. Confirmed-closed Chrome may
be recovered through the existing scoped Windows launcher. It never resets
or kills a running profile and never operates the original `windows` profile.

IBKR states stay separate: missing browser/control, missing tab, full SSO login
required, unverified/live page, disconnected Paper, and verified ready Paper.
Gateway absence is not a reason to skip independent analysis.

An enabled existing opt-in permits one guarded `Log In` banner click inside an
already-verified disconnected Paper portal. The helper rechecks the banner,
blocks credentials/challenges/dialogs/competition prompts, persists a block
before clicking, does not retry an uncertain click, and verifies the result.
Full credential login, Paper/Live selection and two-factor remain manual.
See `docs/PAPER_RECONNECT.md`. Attempt state was not reset to force a retry.

### Findings And Repair

The monitor was not simply absent: recent scheduled runs executed the helper
and reported `login_required`. A manual no-notification pre-change check later
returned ready after the owner's intervening login. All six analysis tasks
were enabled S4U and showed successful October 14 completion codes.

The old activity path could click the already-selected Portfolio/Positions
control. That could be a no-op; the old `ready` result did not prove an actual
navigation occurred. This weakness is established in code. It does not prove
the exact cause of every IBKR expiry.

The repaired helper reads visible Dashboard/Positions link destinations only
inside a verified Paper portal, rejects credential/challenge/dialog states,
selects a different observed same-portal dashboard route, navigates once and
compares the resulting hash. A no-op or unconfirmed navigation is not success.
It re-observes Paper status afterwards. A portal is preferred over a leftover
SSO tab when selecting the keeper; no login form is operated.

Safe `activity_verified` and `last_verified_activity_at` fields now distinguish
navigation from mere page availability. `logs/browser_monitor_history.jsonl`
appends status/reason/times, elapsed time, gap from the prior helper check and
whether notifications were enabled. No account body, URL, token or balance is
written there. A manual `--no-notify` check is distinguishable from a notifying
run; this history alone must not be mislabelled as proof of a scheduler trigger.

### Live Verification And Limits

At 14:50:54 Denver on October 14, the repaired no-notification helper returned
analysis `ready`, Paper `ready`, and `activity_verified=true`. The helper took
67.7 seconds. No credentials, login controls, orders, settings or Telegram
messages were operated in that test. It verified navigation, not an idle timer
reset acknowledged by IBKR. No repeated 40-minute cycle after this patch has
yet been observed as part of this repair.

IBKR documents configurable inactivity timeouts of 15/30/45/60 minutes. The
owner previously reported selecting 60; current persistence of that setting
was not inspected or changed. A 40-minute check cannot outrun a 15-minute idle
setting and does not prevent server expiry, maintenance or competing sessions.
No script can promise weeks of authenticated access. Missing checks also occur
if the VM/app/permissions/network are unavailable. An active saved schedule is
not proof of completed execution. No second scheduler was created.

## 9. Changed Files And Ownership

| File | October 14 change |
|---|---|
| `deep_scan.py` | Smaller triage batches, missing-only retry, timeout budget, validation/audit, explicit trailing lookbacks |
| `llm_researcher.py` | Optional bounded triage request timeouts/retry controls; safe exception type |
| `research_pipeline.py` | Bounded replacement coverage, identity/quote diagnostics, review prioritization |
| `session_runner.py` | Pass option directions, balanced unpriced notice, richer coverage audit |
| `browser_research.py` | Distinct verified portfolio navigation, portal preference, safe monitor history |
| `test_options_review.py` | Mocked triage, replacement coverage and rejection tests |
| `test_browser_research.py` | Mocked navigation/no-op/tab-selection/history tests |
| Documentation | Updated current behavior, dated incident, limits and reviewer entry point |

Earlier repairs remain relevant but were not reimplemented: independent S4U
analysis jobs; XIC regime; persistent Paper reconnect; strict side caps; native
Canadian quote gates; separate DeepSeek and public-review integrations; daily
Claude/GPT rotation at medium/high only; GPT protected completion SDK route.
No scheduler modes, monitor cadence, credentials, provider defaults, account
settings, firewall exposure or directional/contract thresholds changed here.

## 10. Verification Commands And Boundaries

Only the following suites are designated offline/mocked:

```powershell
Set-Location C:\Trading
.\.venv\Scripts\python.exe -m unittest test_options_review test_analysis_health test_browser_launcher test_browser_research test_market_context test_research_pipeline test_telegram_notify
node --test scripts/test_openclaw_public_review.mjs
.\.venv\Scripts\python.exe -m pip check
```

The Python suite passed 158 tests after the main repair; expanded final
verification passed all 162 tests. All seven mocked Node transport tests passed;
`pip check` reported no broken requirements. These results are also recorded
in `docs/CHANGELOG.md`. Passing tests do not prove future provider availability,
profitability, complete market coverage, or perpetual IBKR authentication.

Do not discover/run all `test_*.py`: `test_llm_setup.py` and
`test_portfolio_review.py` are live. Do not run `place_order.py`; it is a
deprecated notifying stub. Production sessions and Telegram tests send real
messages and may spend allowance. `scripts/openclaw_completion_probe.mjs`
without `--prepare-only` makes a live model request. Obtain approval first.

## 11. Supporting Evidence And Documents

Runtime evidence is available locally to the owner, but excluded from the
shareable bundle because logs as a class may contain private information:

- `logs/options_coverage_20261014_session2.json`
- `logs/deep_scan_20261014_session2.json`
- `logs/browser_research_2026-10-14_session2.json`
- `logs/scheduler_session2_20261014_110003.log`
- `logs/browser_monitor_history.jsonl` and minimal browser/session state

Canonical guides: `docs/CONFIGURATION.md`, `docs/OPERATIONS.md`,
`docs/VM_OPERATIONS.md`, `docs/RESEARCH_WORKFLOW.md`,
`docs/OPTIONS_AND_REVIEW_REPAIR.md`, `docs/PAPER_RECONNECT.md`,
`docs/BROWSER_MONITOR_MIGRATION.md`, `docs/SCHEDULER_RELIABILITY.md`.

Primary external references used for definitions/limits, not profitability:

- [IBKR option-chain fields](https://www.interactivebrokers.com/campus/trading-lessons/trading-options-using-the-option-chain/)
- [IBKR inactivity timeout](https://www.ibkrguides.com/orgportal/gettingstarted/log-out-after-inactivity.htm)
- [IBKR account-management and brokerage session distinction](https://www.interactivebrokers.com/docs/web-api/authentication/sessions)
- [OpenAI local scheduled-task execution requirements](https://learn.chatgpt.com/docs/automations)

Do not apply Web API tickle/timeout values directly to browser UI sessions or
add hidden authenticated API calls as a substitute for the scoped UI helper.

## 12. Questions The Independent Reviewer Must Challenge

1. Does every successfully scored name receive a fair triage attempt, and can
   provider timeouts/retry allocation still starve later names?
2. Can invalid/partial JSON manufacture approval or overwrite valid decisions?
3. Are timeouts truly wall-clock bounded, or only per network operation?
4. Does candidate replacement preserve the browser lock, cleanup, side balance,
   collection deadline and optional-review reserve under every failure path?
5. Can an incomplete selector, ambiguous symbol or unsupported MX layout cause
   false exclusion? Are the failures clearly labelled without ticker guessing?
6. Can valid near-money PUTs be missed because of the activity/DTE gates or the
   twelve-candidate bound? What evidence/backtest supports changing those rules?
7. Does the nearest-expiry/near-underlying selection behave sensibly without
   invented Greeks, multipliers, prices or US-to-CAD converted contracts?
8. Do earnings, quote freshness and dispatch-window gates run again at the
   correct final stages? Can lengthy research leave no fresh priced contracts?
9. Can the unpriced notice still hide a side, duplicate names or imply a priced
   recommendation? Are delivery counts based only on successful sends?
10. Is six-symbol model review explicit about unreviewed replacement names?
11. Does the portfolio-navigation check actually move to a different observed
    route, fail closed on auth/dialogs and avoid unnecessary broker actions?
12. Does portal preference behave correctly if several portal tabs exist, one
    expired and another live/Paper? Can cleanup preserve the wrong keeper?
13. Does ready mean only a verified accessible Paper page, not live market data
    entitlement, preserved idle timer or healthy independent analysis?
14. Does notification deduplication survive failures, manual no-notify checks,
    recovery and malformed/missing state without silently losing an alert?
15. Can monitoring gaps be diagnosed if the helper itself never launches, and
    which failures remain outside its own ability to notify?
16. Are runtime state, audit history, secrets and the original browser preserved?
17. Which test fixtures are too permissive, fail to model real DOM/table changes,
    or fail to verify network-budget and scheduling behavior end to end?
18. Which claimed improvements still need owner-approved live or out-of-sample
    validation before they can be described as reliable or economically useful?

## 13. Remaining Work And Safe Rollback

The new triage retry path and replacement-chain collection have offline tests,
not a new paid/full live options session. They will be exercised by later
scheduled runs; no claim is made that tomorrow will have two qualified PUTs.
Monitor continuity needs later real scheduled observations, not only this
manual success. Session expiry can still require manual Paper login.

If a regression is found, identify the exact changed function and preserve
unrelated dirty work. Do not reset Git, overwrite `.env`, clear daily sent logs,
clear browser reconnect blocks, re-register tasks, disable quote safeguards or
create a duplicate monitor as a rollback shortcut. Back up the current source,
apply the smallest reviewed repair and rerun the named offline tests first.

Never send this machine's whole `logs` folder, `.env`, Chrome profile, SSH keys,
OpenClaw auth stores, telemetry database or broker screenshot to another model
as a substitute for this sanitized review packet.
