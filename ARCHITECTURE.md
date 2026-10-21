# Current System Architecture

Baseline: 2026-10-09. Read [AGENTS.md](AGENTS.md) and
[the complete handoff](docs/HANDOFF.md) first. This page describes actual code
paths; [Configuration](docs/CONFIGURATION.md) lists settings and model contracts.

## 1. Components And Hosts

```text
Windows Task Scheduler (six weekday triggers)
  -> existing owner, limited/password-free S4U background mode
  -> scheduled/run_session.ps1
     -> session_runner.py <session>
        -> independent market/news/history -> deterministic scoring
        -> shared API LLM -> ranked research pool
        -> optional SSH/OpenClaw/public browser evidence
        -> Telegram cards + private state/audit

Codex heartbeat (every 40 minutes, 24/7)
  -> browser_research.py keepalive
     -> analysis_health.py -> independent Windows task result/health notices
     -> shared browser lock
     -> optional local browser_launcher.py recovery
     -> SSH/OpenClaw -> enabled guarded Paper recovery -> verified activity
     -> safe health/history and deduplicated Telegram notice
```

Windows VM 104: `192.0.2.15`, project `C:\Trading`.
Ubuntu VM 105: `192.0.2.16`, OpenClaw CLI/gateway and optional review provider.
Dedicated Chrome: loopback 9224, bridge `.15:9225`, profile `canadian-stocks`.
Original profile `windows`: separate 9222/9223 route, unchanged.
Gateway: optional local 7497, disabled by default.

## 2. PowerShell Entry Point

`scheduled/run_session.ps1` validates one of six session names, sets working
directory and UTF-8 Python output, creates a scheduler log and optionally probes
Gateway. It passes the probe result via `IBKR_GATEWAY_UP`.

Default behavior skips the Gateway probe and runs Python on independent data.
An explicitly enabled but unavailable Gateway also fails soft. Only the legacy
explicit `REQUIRE_GATEWAY=true` setting makes the wrapper abort on missing
Gateway. That flag does not implement orders or a different market strategy.

Fallback markers use the historical `skipped_` filename but normally mean a
session ran on independent data. Markers from other sessions are retained.
Python nonzero exit propagates as wrapper exit 1; successful execution exits 0.
A process success code does not prove every source/message succeeded.
Since the approved October 13 repair, the six analysis tasks no longer depend
on Windows desktop login. They run limited in background session 0. The Chrome
launcher must reject GUI launch there; existing browser attachment is optional,
and interactive recovery remains in the separate monitor. Verified dependencies
and migration caveats: [Scheduler reliability](docs/SCHEDULER_RELIABILITY.md).

## 3. Common Session Start

`session_runner.run_session()`:

1. Validates session config and passively reports optional browser availability.
2. Pulls new Telegram news and RSS independently, with nonfatal failure handling.
3. Collects one dated market-context snapshot. XIC controls Canadian regime;
   QQQ/SPY are separate context; gold/oil/VIX/sector fields retain unknown values.
4. Saves `market_snapshot_<YYYYMMDD>_<session>.json` and audits classification maps.
5. Premarket sessions scan public per-symbol catalysts, send heads-up and return.
6. Session 4 optionally reviews Gateway holdings, sends EOD summary and returns.
   No EOD liquidation occurs. Unknown holdings count remains unknown.

`format_market_context()` renders dated sector snapshot evidence separately from
the live-only `sector_etf_pct` gate values. Premarket sector inputs remain `None`
for scoring, but their available previous-session quotes are shown with status,
quote time and reference date. VIX display also carries status/time; its filter
input is unchanged. US labels distinguish shared premarket/closed states from
actual missing/stale inputs. See the [active strategy](strategies/active_strategy.md).

Premarket/EOD sessions do not run the full stock-ranking path. `pending_tasks.md`
is not parsed. News-LLM schedule entries for premarket do not prove invocation:
the current runner returns from the no-signals branch before calling that filter.

## 4. Stock Session Pipeline

Sessions 1/2/3 use `1-DAY` rules. Runtime quotas are 8 stock cards/day, 3/session.
When stock quota is full, position review can still run; Session 2 still attempts
its independent options scan.

For a normal stock scan:

1. Connect Gateway only when enabled, otherwise use Yahoo history. Obtain owned
   symbols only from available account reads; do not infer none when unavailable.
2. Load the full watchlist. The legacy `filter_hot_names()` helper is not used
   to remove sectors before this scan. Optional news synthesis may reorder names.
3. Fetch XIC reference bars once and per-symbol two-year daily history; score all
   fetchable non-owned names before sending. Build weekly confirmation best-effort.
4. `signals.score_symbol()` records mandatory blockers and technical checks.
   Available live spread/volume can supplement historical tradability; absent
   spread remains unverified. Keep each scored signal/bar pair for triage.
5. Build technical survivors and a catalyst pool only from names without hard
   blockers. Technical pool size is `max(remaining_session * 3, 6)`; inject at
   most five strongest catalysts.
6. `deep_scan.py` builds factsheets from all successfully scored names, not just
   technical survivors. Batches of ten feed the shared API model, followed by
   one missing-only repair pass in groups of five within a 360-second dispatch
   budget. Triage requests cap provider timeouts at 60 seconds without SDK
   retries; valid verdicts are preserved and invalid/missing verdicts stay unknown.
   Factsheet 3-month return uses 63 sessions and the 60-session high uses only
   its trailing window, not the entire two-year history. LONG conviction
   at least 4 may join research; SHORT conviction at least 4 steers relaxed PUTs.
7. Enrich the research pool, then call the shared API ranker. It annotates/sorts
   originals, not arbitrary model-created stocks. Technical veto is a confidence
   reduction; promotion lanes require explicit approval and no veto.
8. Interleave ranked LONG names with Session-2 strict technical PUT previews
   and accepted triage SHORT names, then collect
   browser evidence for six primary names. Session 2 may try replacement names
   of a missing option side, up to twice that primary limit within the same
   collection/review budget and lock. Persist public evidence using
   `browser_research_<YYYY-MM-DD>_<session>.json`.
9. Before every dispatch, recheck blockers, XTSE window, promotion approval,
   repeat cooldown, sector cap and remaining quota. Promotions get reference
   entry/stop/target from close and ATR, never new model-invented quotes.
10. Send the Telegram card. Append successful sends to the daily log and update
    in-process counts only after delivery succeeds. Session 2 then runs options.
11. Send summary and disconnect an optional connected Gateway in `finally`.

See [Active Strategy](strategies/active_strategy.md) for exact thresholds.
API-model failure preserves deterministic technical ranking; AI-dependent
promotion lanes do not acquire approval by default.

## 5. Options Pipeline

`run_options_scan()` is scheduled only in Session 2. Native Canadian eligibility
uses the watchlist, excluding names already alerted in that day's priced-options
log, without requiring a US dual listing. Reuse scanned bars and XIC reference
bars. Strict direction previews allocate bounded browser coverage; they are
never sendable and skip external earnings/US-chain requests. Final scoring
still applies earnings and native-contract checks.

Direction uses the underlying's technical state. Native CAD quote selection
uses `research_pipeline.validated_contract()` over observed MX rows or IBKR
Paper CDE snapshots. It rejects
wrong symbol/currency/exchange/source, absent/stale timestamp, DTE outside 21-45,
nonpositive/crossed quotes, spread over 25%, and inadequate OI/volume.

If no validated row exists, an optional underlying-only idea may be retained.
The options-watchlist notice contains no recommended strike/expiry/premium;
those notices are not successful priced-option cards. Accepted contract cards
are optionally API-ranked. Successful sends are capped at two CALL/two PUT with
no cross-side spillover. Coverage notices/audits explain unfilled slots without
claiming limited browser coverage proves there are no opportunities.

Immediately before priced dispatch, recheck XTSE window and matching fresh
contract evidence. Successful sends alone append to `options_signals_sent_*`.
A full stock quota does not eliminate the options scan; missing browser quotes
can legitimately leave zero priced option cards.

## 6. Browser Adapter And Recovery

`CanadianResearchBrowser` calls the existing Ubuntu CLI over authenticated SSH.
Reads may retry once within a 35-second total command budget. Mutations are not
blindly retried after an uncertain result. Profile mismatch stops processing.

Passive `check()` does not launch, navigate or clean. `ensure_available()` is
used under the shared lock by actual research/paper checks. If unavailable, the
local launcher verifies Windows loopback/process state before opening the exact
existing dedicated Chrome profile. Local Chrome ready but remote attachment
unready is distinguished from a confirmed closed browser. No process is killed.

`run_research()` fails soft. Its context attempts cleanup in `finally`, retaining
one IBKR tab, or opening a blank keeper before closing old tabs when IBKR is
missing. Cleanup has its own 90-second budget. Independent non-browser scans
and passive status do not close tabs.

`verify_shortlist()` starts its 30-300-second public collection/review budget
inside the research callback, after lock acquisition and availability/recovery.
Session 2 first attempts native IBKR Paper CDE snapshots for direction names
using the separately bounded GET-only `ibkr_options.py` adapter. Public MX
attempts cover names without qualified matching-direction IBKR quotes (except
definitive no-CDE/no-month/no-expiry results), then Barchart when time permits.
Both source diagnostics survive a fallback; no global stream cancellation is
performed. Public DOM/table parsing still rejects unsupported layouts/timestamps.
Review time is reserved. Historical IBKR strike-selection prices are explicitly
labelled previous-close references, not current spot or current moneyness. See
[Review repairs](docs/REVIEW_REPAIRS_2026-10-16.md).
The optional review receives bounded public excerpts, accepts only recognized
symbol notes and requires an explicit zero-tool-call summary. Review requests
alternate daily in Denver between Claude Opus 5.5 and GPT-6.1 Sol, with medium
effort by default and only medium/high accepted. Global OpenClaw defaults and
DeepSeek ranking are unchanged. Cards/audits label review outcome/model/effort.
`research_pipeline.py` streams `scripts/openclaw_public_review.mjs` to VM 105's
Node process over SSH. Claude uses native safe-mode `agent exec`; GPT uses the
public completion SDK with the existing OAuth and an empty tool list. Fallback
shares the original deadline and cannot start with less than 15 seconds left.
Collection survives review failure. Details:
[Options And Review Repair](docs/OPTIONS_AND_REVIEW_REPAIR.md) and
[Browser Workflow](docs/RESEARCH_WORKFLOW.md).

## 7. Paper-Session Check

The keepalive CLI first calls `analysis_health.check_analysis_health()` outside
the Paper lock. This reads task states/results, never launches jobs, and has a
separate minimal deduplication state. It emits an analysis diagnostic before the
Paper diagnostic; healthy browser state cannot mask rejected task starts. See
[Scheduler reliability](docs/SCHEDULER_RELIABILITY.md).

`check_paper_session()` obtains the shared lock; busy is quiet. With checks
enabled, `keepalive()` has a 240-second budget, can recover the dedicated Chrome,
and inspects only minimal paper/login state. A `/sso/` login page is recognized
without reading auth inputs. A paper banner is required before portfolio
navigation. Live/unverified pages are not navigated.

October 16 owner opt-in `OPENCLAW_PAPER_SAVED_LOGIN` adds `paper_login.py` only
to `keepalive()` when SSO is classified. It may select Paper and submit only
an already-filled recognized IBKR form, with synchronous domain/form/destination/
challenge/mode guards and a separate durable attempt block. No field value is
returned, retrieved for storage, typed or sent to an LLM. Three agreeing Paper
indicators are mandatory; uncertain results stop without retries. Authenticated
Paper must be observed before normal portfolio activity resumes. Details:
[Saved-form Paper sign-in](docs/PAPER_SAVED_LOGIN.md). Independent research
never attempts login, and no new schedule or model is involved.

Since October 15 each check makes two real mouse clicks through OpenClaw
`click-coords` (not script clicks or URL changes): the portal's own
`Positions` tab, a three-second pause, then `Dashboard`. A page probe first
re-checks the Paper/authentication/dialog guards, requires one unique tab strip
and confirms nothing covers the tab; each click is never retried and counts as
activity only after that tab is observed active. Non-modal side panels do not
block; modal/confirmation dialogs do. If IBKR's inactivity warning is shown,
its single allow-listed stay-logged-in button is clicked once first. If the
verified Paper tab sits on another portal page (no tab strip), it is navigated
to the portal Dashboard route once before the clicks. Between 10:05 and 10:58
Calgary on trading days, a logged-out result triggers one daily Telegram
reminder to log in before the 11:00 options scan. The check waits up to six
minutes for research to release the browser (logging `busy` if it cannot), and
research waits up to three minutes for a check; each research run ends with
the same two verified clicks when Paper is ready. A portal is preferred over a
leftover SSO tab when selecting the keeper.
With `OPENCLAW_PAPER_RECONNECT=true`, a disconnected verified Paper portal can
receive one guarded banner `Log In` click before navigation. The helper checks
the exact warning/control, blocks credential forms and active dialogs, persists
an attempt block before clicking, uses a non-retried command, and verifies the
result within at most 60 seconds of the overall budget. A failed/uncertain
attempt stays blocked until healthy Paper is observed. That banner-only branch
does not enter credential forms. The separate saved-form exception above is
the only automated SSO submission; credential entry/challenges remain manual.
See [Paper reconnect](docs/PAPER_RECONNECT.md).
The helper does not guarantee server-side authentication retention. Login-needed
notices are deduplicated; public research and independent analysis continue.
Health state stores codes/times/notified fingerprint, never broker page bodies.
Separate `browser_reconnect_state.json` and `browser_paper_login_state.json`
each store only their own blocked flag and UTC check time. Preserve both;
one flow's cleared block does not override the other flow's block.
`browser_monitor_history.jsonl` appends safe status/time/activity diagnostics,
notification-enabled flag, elapsed time and prior-check gap. It contains no
broker body/URL/account values. Manual no-notify checks are not scheduler proof.

## 8. Failure Isolation

| Optional state | Default behavior |
|---|---|
| Gateway up, Chrome up | Independent scan plus available optional reads/evidence |
| Gateway up, Chrome unavailable | Same scan/account reads; evidence limitation labelled |
| Gateway down, Chrome up | Independent scan plus public evidence; holdings unknown |
| Both unavailable | Independent scan/news/API research and Telegram continue |
| API model unavailable | Technical lane continues; promotion lanes require unavailable approval |
| OpenClaw review unavailable | Retain collected public evidence and deterministic validation |
| IBKR logged out | Public research still runs; owner receives login-needed notice |
| Telegram unavailable | Delivery failure recorded safely; no false successful-send quota entry |

A missing mandatory input differs from an unavailable optional source: short
history, bad underlying price, observed blockers or invalid contract evidence
must still suppress the affected signal.

## 9. Ownership And Extension

Complete inventory/state reference: [Handoff](docs/HANDOFF.md). Provider schemas:
[Configuration](docs/CONFIGURATION.md). Deployment/tests: [Operations](docs/OPERATIONS.md).

Change technical gates in `filters.py`/`signals.py`, orchestration in
`session_runner.py`, model transport/parsing in `llm_researcher.py`, native
validation/collection in `research_pipeline.py`, browser coordination in
`browser_research.py`, and local startup in `browser_launcher.py`. Prefer these
existing ownership boundaries over another parallel orchestration framework.

`ibkr_client.py` retains legacy order methods and `mx_options.py` legacy helpers,
but no scheduled order path uses them. `place_order.py` remains a deprecated
notifying stub. Future execution requires a separately designed workflow, not
removal of that stub or a fictional environment flag.

Historical backtests simulate selected technical rules and omit live
news/browser/provider state. They are not a performance floor or evidence that
adding an LLM improves outcomes. Revalidate after strategy changes.
