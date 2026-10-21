# Dated Change And Verification Record

This records observed changes, not a current quote, account state, credit
balance or profitability claim. Follow [Handoff](HANDOFF.md) for the current
system and [Operations](OPERATIONS.md) for verification commands. The
October 15-16 work is also written up step by step in the
[Work log](WORK_LOG_2026-10-15_16.md).

## 2026-10-19: Correct Premarket Unknown/Unavailable Reporting

- Replayed the saved 07:00 CA snapshot: all eight sector quotes were present.
  Live-only `sector_etf_pct` values correctly remained `None` before open, but
  the message had mistaken that gate exclusion for unavailable source data.
- Sector display now uses underlying snapshots and includes percentage, status,
  quote time and comparison date. Missing sources stay unavailable and stale
  quotes stay stale. Historical inputs without snapshots explicitly lack timing.
- Both US premarket snapshots now yield `US NOT OPEN`; both market-closed
  snapshots yield `US MARKET CLOSED`. Other unusable cases identify ticker and
  reason rather than conflating source failures with not-open markets.
- Replaced premarket "last regular close" with "last regular quote", including
  when formatting old snapshots: ZIN's saved quote was from the prior opening
  time, not a confirmed closing print. VIX display now includes status/time.
- Filter inputs, thresholds, providers, browser/authentication, scheduler jobs,
  private settings, historical logs and runtime state are unchanged. No live
  Telegram send, browser action, external-source request or paid model test ran.
- Verification: 229 Python tests (nine new), 41 Node tests, `pip check` clean;
  offline saved-message replay was 1,371 characters before heading/browser note.

## 2026-10-16: Complete Documentation And Successor Handoff Audit

- Expanded System Overview with both workflow diagrams, all daily trigger times,
  layer/host/model boundaries, optional-source behavior, recovery decision table,
  dated verification ledger and a portable request for another assistant.
- Expanded the saved-form guide with a state diagram, function-level mapping,
  every saved-login reason, exact budget/attempt semantics, safe result examples,
  operator/opt-out procedures and regression-test mapping. Documented that an
  attempted flag is not proof of a submitted credential request and that old
  `notified_*` fields can remain after a no-notification recovery.
- Added the saved-login attempt file to the handoff inventory, corrected older
  manual-only statements, triage batch documentation and monitor invocation order.
  Updated current entry links; dated historical packets remain explicitly scoped.
- Documentation-only: no code, private configuration, credentials, runtime state,
  automation definition, Windows tasks, network exposure or thresholds changed.
  No live helper, broker login, Telegram send or model request ran for this audit.
  Static validation: 21 Markdown files, 173 relative links, balanced code fences
  and all 19 saved-login reasons documented, with no broken links. The 220/41
  functional test totals remain the prior dated run, not a new live test.

## 2026-10-16: Owner-Enabled Saved-Form Paper Sign-In

- Owner explicitly requested Paper selection and login using Chrome's already
  filled credentials after the observed overnight logout. Added `paper_login.py`
  to the existing helper, not a separate AI login agent or scheduler.
- Production `OPENCLAW_PAPER_SAVED_LOGIN=true`; code/template default false.
  It never retrieves/types credential values, opens a password manager, selects
  Live, handles challenges/confirmations or places orders. Every guarded action
  is non-retried, with a durable block before mutation and verified Paper after.
- Existing monitor updated in place, retaining ID, target chat and 40-minute
  cadence. Added safe saved-login flags and preserved independent analysis.
- Current anonymous public form was inspected; three Paper indicators agree.
  The actual browser was initially `ready` and was not deliberately logged out.
  A later SSO check safely rejected the observed empty-action form before clicks;
  support for that same-page POST variant was added with a regression test.
- Live recovery at 23:24:40 Calgary October 16 returned `ready`, verified
  portfolio activity and both saved-login flags true. The helper selected Paper
  and submitted the already-filled form once; no credentials were retrieved or
  logged and no order/Telegram/model test ran. Analysis-scheduler health was ready.
  The persisted block cleared only on verified Paper, never by manual reset.
- Verification: 220 Python tests and 41 Node tests pass. Details, state names,
  stop conditions and migration procedure: [Saved-form sign-in](PAPER_SAVED_LOGIN.md).

## 2026-10-16: Independent Review Findings Corrected

- Snapshot polling waits for numeric prices; blank/null/prefixed fields no
  longer finish polling prematurely. Removed the global `unsubscribeall` call
  and its allow-list entry; existing browser quote streams are not cancelled.
- MX can retry an IBKR name with no validated matching-direction contract,
  including unknown liquidity/stale/wide quotes. IBKR diagnostics survive the
  fallback and remain visible in the coverage audit; all quality gates remain.
- The public research clock starts after lock acquisition/recovery and the
  optional IBKR stage, rather than spending that clock while waiting for access.
- IBKR previous-close references are labelled through scoring, model prompts,
  audit logs and Telegram cards. Current moneyness remains unverified; the
  collector does not claim to fetch a fresh underlying price.
- Cleanup tests isolate the post-research hook and reject unmocked subprocess
  calls. Added actual-JavaScript snapshot fixtures with fake fetch/timers.
- Verification: 204 Python tests and 18 Node tests pass, `pip check` clean.
  No live broker/Telegram/model tests, task changes, `.env` or runtime-state
  changes. Full behavior/limits: [Review repairs](REVIEW_REPAIRS_2026-10-16.md).

## 2026-10-16: Lock Waiting, Post-Research Activity And IBKR Quote Filling

- Logout at about 12:00: the 10:41 check was the last activity; the ~11:21
  check left no history row (most likely the shared lock was held by Session 2
  research, and `busy` returned without waiting or logging), and IBKR logged
  out before the 12:02 check, an 80-minute idle gap.
- `browser_lock` accepts a bounded wait. The 40-minute check now waits up to
  360 seconds for research instead of skipping; if still held it appends a
  `busy` history row so gaps are visible. Research waits up to 180 seconds for
  a running check instead of losing its browser stage.
- After every research run's cleanup, while still holding the lock, a verified
  ready Paper tab gets the same two real clicks (Positions, Dashboard), logged
  as `source=session_research` and updating `last_verified_activity_at`.
  Logged-out Paper still never blocks or alters research results.
- IBKR quotes: today's Session 2 checked 49 names but 645 contracts returned no
  bid/ask in time (11 qualified). Contracts are now quoted best-first (nearest
  qualifying expiry, nearest strike) rotating across names, in 40-contract
  waves with up to eight 1.5-second rounds. Unquoted contracts are counted as
  `ibkr_quote_not_received`/`ibkr_quote_not_requested`, a name with no quotes
  at all falls back to MX, and unknown OI+volume is labelled
  `contract_liquidity_unknown` (still not qualifying; unknown is not zero).
- Live read-only checks during market hours: 233 of 234 contracts quoted on
  10 names (was mostly empty); qualified PUT contracts for BCE 4, CCO 4, U.UN 3,
  NXE 1; CALLs CNQ 16, MFC 9, CTC.A 1. IBKR omitted open interest on about half
  of contracts and volume on most (TECK.B: none), and waiting longer did not
  change that. 147 seconds for 10 names, so Session 2's 300-second IBKR budget
  covers the best contracts of every name rather than every contract.
- `keepalive --no-notify` at 12:38 Denver: ready, activity verified. 194
  offline tests passed. Thresholds, windows, schedules and `.env` unchanged.

## 2026-10-16: Return-To-Dashboard And Pre-Session-2 Login Reminder

- Monitor history Oct 15-16: real clicks verified 11:21-14:01 and 17:21-03:21
  (about ten hours overnight, two automatic banner reconnects). At 14:40 and
  15:20 the tab sat on a quote/option-chain page (`tab_strip_not_unique`); with
  no activity, IBKR logged out by 16:00. On both Oct 15 and 16 the session ended
  at about 04:00 Calgary despite verified activity at 03:21, consistent with an
  IBKR-side scheduled logoff that clicks cannot prevent.
- When the first Positions probe finds no Dashboard/Positions strip on a
  verified Paper portal page (guards already passed), the helper navigates that
  tab to the portal `#/dashboard/` route once, waits four seconds, then makes
  the usual two real clicks. Any other denial still stops the check. Recorded
  as `returned_to_dashboard=true`. Never retried after a click.
- On XTSE trading days, if a check between 10:05 and 10:58 Calgary finds
  `login_required` or `ibkr_tab_missing`, one plain-text Telegram reminder asks
  the owner to log in before the 11:00 options scan (IBKR quotes need Paper;
  MX remains the fallback). Sent once per day (`options_reminder_date` in
  `browser_session_state.json`), never with `--no-notify`; history records
  `options_reminder_sent`.
- 190 offline Python tests passed. No live keepalive run, Telegram send or
  broker login was performed for this change.

## 2026-10-15: IBKR Paper Option Chains For Session 2

- Cause of missing PUTs (Oct 14 and 15): only 6-7 MX pages fit the browser
  budget and 3 of 7 on Oct 15 had no listed options (NDA, CTC.A, ENGH), so
  chart-bearish BCE, CCO, NXE, WELL, IVN and TOI were never checked. Oct 15
  Session 2 still delivered 2 CALL + 1 PUT (DML).
- Owner-approved: Session 2 now first reads IBKR's own CDE option data inside
  the verified Paper tab for every option-direction name (interleaved CALL/PUT
  order): stock lookup restricted to TSE/VENTURE (never the NYSE twin), every
  expiry in the months covering 21-45 days, 4 strikes below and above the last
  close for the needed side, then bid/ask/sizes/OI/volume, IBKR delta/IV and
  the contract multiplier. Only GET requests to secdef/search|strikes|info,
  marketdata/snapshot and marketdata/unsubscribeall are possible; no clicks,
  typing, orders or account paths. Separate 300-second budget
  (`OPENCLAW_IBKR_CHAIN_TIMEOUT`), then the unchanged MX/review stage covers
  names IBKR did not (logged out, lookup failure, rate limit).
- Shared gates unchanged: 21-45 DTE recommendation window (owner kept it),
  30-minute quote age, spread <=25%, OI >=100 or volume >=10. IBKR rows must be
  real-time or delayed (`6509` R/D); delayed quote_time is IBKR's update time
  minus 15 minutes; frozen/not-subscribed/prior-close-prefixed values reject.
  Cards show "Source: IBKR Paper", the 100-share contract premium and IBKR delta/IV.
- Verified: 186 offline tests; page scripts under Node with a fake data service
  (TSE chosen over NYSE, out-of-window expiry dropped, retry after empty first
  snapshot, GET-only allowed paths); read-only live dry run after the close on
  10 names: 9 resolved with 8-32 window contracts each (incl. CTC.A, TECK.B,
  U.UN), NDA correctly has no CDE options, 72 seconds. All quotes were frozen
  after hours and correctly rejected, so qualification during market hours is
  first observed in the next scheduled Session 2. Disable with
  `OPENCLAW_IBKR_CHAINS=false`.

## 2026-10-15: Session-Warning Click And Non-Modal Panels

- First real-click run (10:40 scheduled, 10:44 manual) stopped at
  `dialog_requires_attention` with no click. A read-only label check found a
  non-modal IBKR panel (buttons "Orders & Trades" and "Close"), not the expiry
  warning. Non-modal panels no longer stop the check; tab clicks still require
  that nothing covers the tab, and modal/alert/confirmation dialogs still stop it.
- Owner-approved: when IBKR's inactivity warning is shown (expiry/inactivity
  wording, no order/trade/transfer/password text, on a verified Paper portal),
  the helper clicks its single stay-logged-in button once with a real mouse
  click. Allowed labels only: Stay Logged In/Signed In, Keep Me Logged/Signed
  In, Continue Session, Extend Session, Continue, I'm Still Here, Stay. Unknown
  labels, several warnings or a covered button mean no click. The warning must
  disappear within about six seconds or `session_warning_not_cleared` is
  reported; never retried. `session_extended=true` is added to state/history.
- 178 offline tests passed; both page probes passed nine mock scenarios under
  Node/linkedom. Live `keepalive --no-notify` at 11:07 Denver: Paper `ready`,
  `activity_verified=true`, 111.9 seconds, no Telegram. No expiry warning was
  present, so the stay-logged-in click is verified offline only.

- Owner request: the 40-minute check must make real clicks in the IBKR Paper
  page, not only change the URL. Since 09:21 Denver the old hash navigation also
  reported `portfolio_navigation_unconfirmed` on a healthy Paper session.
- Each check now sends two OpenClaw `click-coords` mouse clicks (Playwright
  input, not `element.click()`): portal tab `Positions`, three-second pause,
  then `Dashboard`. A page probe re-checks portal domain, Paper banner, password
  /2FA/CAPTCHA text and visible dialogs before each click, requires exactly one
  Dashboard+Positions tab strip (sidebar duplicates ignored), and refuses if
  another element covers the tab. The active tab is read from the longest
  matching hash route or ARIA/active markers; each click is confirmed within
  about six seconds or reported `<tab>_click_unconfirmed`. Clicks are never
  retried; only safe reason codes are recorded. No URL fallback hides failures.
- Offline: 176 Python tests passed (old hash-navigation tests replaced). The
  probe script passed six mock-page scenarios under Node/linkedom on VM 105.
  It has NOT yet run against the live IBKR page; `click-coords` on the attached
  `canadian-stocks` profile and the real tab markup need one owner-approved
  `keepalive --no-notify` check. Scheduler, cadence, reconnect opt-in and
  `.env` unchanged. Real clicks improve activity; they cannot stop IBKR
  server-side logouts or a competing login with the same username.

## 2026-10-15: Data Identity, Telegram Escaping, Final-Answer And Strike Label Repair

- Insider lookups could reach a different US company with the same letters
  after an empty Canadian result: TELUS `T` -> AT&T, Cameco `CCO` -> Clear
  Channel, Methanex `MX` -> Magnachip, WELL Health -> Welltower, Air Canada `AC`
  -> Associated Capital. Insider buying adds a stock score point and card lines.
  Insider now queries only the resolved Canadian listing plus the curated
  same-issuer US listing; otherwise insider data stays unknown. The shared
  resolver no longer offers a bare symbol, which also removes the bare-symbol
  price-history fallback and most per-session Yahoo 404 lookups. Curated maps
  themselves were not changed (note: `NA` maps to an unrelated Nasdaq symbol
  but is not on the watchlist; Barrick's `GOLD` mapping should be rechecked).
- Stock cards, card footers, catalyst alerts, news-LLM summaries, portfolio
  review reasons and ownership holder names now HTML-escape external/model
  text. A headline such as "S&P/TSX" or a reason such as "close < EMA" could
  previously make Telegram reject the whole card, which then went unlogged.
  `send()` retries exactly once as plain text when Telegram reports an HTML
  entity parse error; the rejected request was not delivered, so no duplicate.
- DeepSeek triage sometimes returned a draft JSON, prose and its
  end-of-thinking marker before the final JSON (observed 2026-10-15 batch 1).
  Parsing now uses only the text after the last marker; a draft is never
  accepted, reasoning with no final answer stays a failure, and two unmarked
  JSON objects still fail rather than guessing.
- Option cards always printed "(ATM)". Strikes are the nearest qualifying
  quoted strike, which can be in the money (October 14: CNQ 72 vs 74.27, SU 104
  vs 107.99). Cards now show ATM within 1%, otherwise the ITM/OTM percentage.
- No thresholds, quotas, providers, keys, schedules, `.env` or production
  settings changed. 175 offline Python tests passed (13 new). No live session,
  paid model call, broker-page action or Telegram send ran.

## 2026-10-14: Coverage And Monitor Activity Repair

- Observed Session 2: 75 eligible names, 41 DeepSeek verdicts/34 missing, six
  browser names, three priced CALL candidates, two CALL sends, zero PUT sends.
  Five unpriced PUT entries existed; this was not proof of no PUT opportunities.
- Added ten-name triage batches and one missing-only repair pass of five within
  a 360-second dispatch budget. Only triage gets bounded provider timeouts/no SDK
  retries. Valid verdicts persist; missing/invalid entries are unknown. Model,
  provider, keys and output-token caps unchanged. Additional calls may cost more.
- Corrected factsheet lookbacks: a two-year whole-series return/high had been
  labelled three months/60 sessions. New explicit 63/60-session windows do not
  change directional or native-quote thresholds.
- Option-side replacement candidates reuse the same browser lock/deadline,
  capped at twice the six-primary-name limit. Added quote rejection counts and
  symbol-identity warnings; no guessed ticker mappings. Six model excerpt names
  maximum; extras are explicitly unreviewed. Selector absence is not proof.
- Balanced/deduplicated unpriced notices; richer per-candidate coverage audits
  explain contract liquidity versus account cash. No historical logs reset.
- Monitor was executing, not absent. All six analysis tasks completed with code
  zero. Earlier Paper checks reported login required; the owner later logged in.
- Replaced potential current-tab no-op with different observed Dashboard/
  Positions navigation and hash verification. Added portal preference over SSO
  and append-only safe monitor history; no login automation or new schedule.
- Live no-notification helper passed at 14:50:54 Denver: analysis ready, Paper
  ready, activity verified, 67.7 seconds. No credentials, login controls, broker
  financial actions or Telegram sends. This does not prove weeks of retention,
  an acknowledged idle-timer reset, or subsequent scheduled cycles.
- Main repair passed 158 Python tests; expanded final verification passed
  162 Python plus seven mocked Node tests, with dependency check clean. No extra paid triage, full trading
  session or Telegram test ran. External review packet records remaining limits.

## 2026-10-13: Live Review Route Verification

- Following separate owner approval, repaired the ChatGPT route with a GPT-only
  native OAuth model overlay and OpenClaw's public simple-completion SDK. Tiny
  medium GPT fixture succeeded (HTTP 200, matching model, one note, zero tools);
  preparation/completion took 20.2 seconds excluding imports/SSH. No re-login,
  credential replacement, paid key, broker action or trading session.
- Added a versioned public-review bridge streamed to VM 105 Node over SSH,
  retaining Claude native exec and sharing one bounded fallback deadline.
  Seven mocked Node transport tests supplement the 148 Python tests. Full
  six-name latency and live fallback remain unverified. See the repair runbook
  for the exact single-model overlay and upgrade/migration checks.

- Owner approved one tiny fixture per model, medium effort, 90-second bound,
  fallback disabled, no browser/broker/Telegram/trading-session activity.
- Claude Opus 5.5 returned an accepted note with matching identity and zero tool
  calls in 53.0 seconds. GPT-6.1 Sol failed after 45.1 seconds: the agent allowlist
  rejected the model despite catalog visibility and healthy cached OpenAI OAuth.
- Confirmed `agents.defaults.modelPolicy.allow` is the rejecting setting. The
  owner approved one exact-model addition and one retry. CLI validation and
  guarded append added GPT-6.1 Sol, preserving the seven existing entries,
  default model and browser configuration. No credential/sign-in changes.
- GPT retry passed selection but failed in 69.3 seconds with missing bearer
  authentication from the Responses API. Cached OAuth health was not proof of
  request authentication. That isolated-agent path remained unusable; it is
  superseded for GPT by the approved SDK repair recorded above.
- Added `model_policy_rejected` / `authentication_unavailable` safe diagnostics and one offline regression;
  combined suite now has 148 tests. Full six-name live review remains untested.

## 2026-10-13: Option Split And Supplemental Review Repair

- Found three successful CALL cards/no PUT cards: unused PUT slots spilled
  into CALLs, browser selection lacked strict bearish previews, and native CAD
  eligibility unnecessarily depended on US mapping. Preserve historical sends.
- Enforced two successful CALLs/two PUTs separately, no spillover; added daily
  coverage audit/shortfall notices and full native Canadian eligibility.
  Technical previews allocate browser slots but cannot approve sends; final
  earnings/quote/DTE/liquidity/window gates remain intact.
- MX collection across selected names now precedes secondary Barchart checks;
  reserve review time. Request daily Denver Claude Opus 5.5 / GPT-6.1 Sol with
  medium by default, only medium/high, optional alternate fallback and explicit
  zero-tool-call validation. Global defaults/auth and DeepSeek are unchanged.
- Label review identity/effort/outcome in cards/audits. Reject reasoning-only
  DeepSeek output and record missing final triage verdicts rather than fabricating
  directions. No API token-budget/provider change.
- 147 offline tests passed at initial repair. Installed CLI/model catalog verified;
  subsequent owner-approved live results are recorded above. No additional trading session or Telegram
  test. See [Options And Review Repair](OPTIONS_AND_REVIEW_REPAIR.md).

## 2026-10-13: Analysis Launch Failure And Independent Health

- Found `0x800710E0` rejected starts on all six Windows tasks, before the runner;
  no October 12/13 scheduled logs. Browser keepalive was a separate working job.
- Enabled task history with administrator approval. Explicit-session catch-up
  succeeded, delivering three stock and three priced-option alerts, exit 0.
- Both interactive engines failed timed probes; limited password-free S4U
  passed automatic launch, Yahoo/SSH/local state/runner and non-sending Telegram
  checks. Prepared a scoped existing-task repair with backups and validation;
  production deployment requires owner approval and installed-state verification.
- The owner subsequently approved deployment: at 11:57 Denver all six existing
  tasks were updated and verified S4U/limited/enabled, unchanged next times and
  limits, with task XML backups retained. Removed all diagnostic task definitions
  and updated future registration to the same mode. No extra catch-up run.
- Added analysis-task health read/notices to the existing keepalive, separate
  state/lock, no automatic catch-up and no new production schedule. Updated its
  prompt and removed misleading browser-only assurances that jobs were running.
- Added 15 offline health tests: 122 combined tests pass, including honoring the
  new first-trigger boundary without re-alerting pre-repair failures. Future wrapper output
  is unbuffered. Strategy gates, credentials, providers and browser auth unchanged.

## 2026-10-11: Guarded Paper Banner Reconnect

- Owner authorized a credential-free reconnect from an already verified Paper
  portal. Added opt-in `OPENCLAW_PAPER_RECONNECT`; default false, production true
  after a successful live single-click test (`paper_disconnected` to `ready`).
- Require the exact disconnected warning and unique control; recheck all guards
  at click time. Block credential forms, active dialogs and competing-session
  confirmations. Persist the attempt block before a non-retried click; failed or
  uncertain outcomes remain blocked until healthy Paper is observed.
- Updated the existing 40-minute monitor prompt, preserving cadence/destination.
  Added the reconnect runbook and reconciled operational/migration documentation.
- All 107 offline tests passed. No orders, credentials, paid model requests or
  Telegram test sends. Sunday session restoration does not prove quote freshness
  or changed subscriptions; full authentication remains manual.

## 2026-10-10: Monitor Migration Documentation

- Added a model-neutral runbook for subscription/client changes: current monitor
  identity/destination, exact helper command, portable prompt, state/permission
  requirements, alternate schedulers, verification and rollback.
- Explained the distinction between the assistant-backed scheduler and the
  scripted helper, which invokes no research LLM. Documented a future direct
  Windows scheduler as a subscription-independent option, not an installed task.
- Linked the runbook from the agent entry point, README, Handoff, Configuration,
  Operations and VM runbook. No code, credentials, provider, network exposure,
  schedule or browser/account activity changed for this documentation update.
- Verified eight documents, 38 local links and balanced code fences;
  `git diff --check` passed. No live helper or notification test was run.

## 2026-10-10: Post-Reboot Paper Navigation

- Observed IBKR's Portfolio control as an accessibility button. The helper
  had verified connected Paper but failed to navigate because its matcher
  accepted only links/tabs. Added the observed Portfolio button role, without
  accepting unrelated buttons or changing authentication behavior.
- Added two offline tests covering the button, existing navigation roles and
  rejection of unrelated buttons. Fixed a UTC/local midnight mismatch in an
  expiry test by freezing its date; production expiry logic is unchanged.
- 95 offline tests passed; dependencies were clean. No-notification Paper
  navigation returned ready. Public MX/Barchart TOU checks succeeded and cleanup
  retained one IBKR tab. Yahoo XIC connectivity succeeded with market closed.
- All six Windows tasks were enabled/ready with previous process result 0.
  The existing 40-minute monitor remained active in its separate chat.
- No login, order, paid model call, Telegram test or schedule change.

## 2026-10-09: Documentation Consolidation

- Added model-neutral `AGENTS.md`, with `CLAUDE.md` pointing to the same rules.
- Added full handoff, configuration/model-migration and operations guides.
- Reconciled architecture, strategy, setup and VM guidance against code.
- Corrected old mandatory-Gateway, quota/schedule, US-to-CAD option conversion,
  24/7 news-daemon, automatic pending-task execution and profitability-floor
  descriptions. Legacy planning flags are no longer described as implemented.
- Kept secrets/account state out of handoff documents. No private `.env`,
  provider selection, authentication, installed schedule or strategy threshold
  was changed by the documentation work.
- Rechecked 15 Markdown files: all 66 local links resolve and code fences are
  balanced. `git diff --check` passed.
- Reran the 93-test offline suite successfully; `pip check` found no broken
  requirements. The edited PowerShell scripts passed parse-only validation,
  and `session_runner.py` passed Python compilation. No live session or
  out-of-schedule Telegram message was sent for this documentation check.

## 2026-10-09: Dedicated Chrome Recovery

- Added `browser_launcher.py`, invoked under the shared lock before public
  research and from the existing 40-minute paper check.
- Recover only a confirmed-closed existing Canadian Stock Chrome profile.
  Local loopback refusal, absent matching process, existing executable/profile
  and interactive Windows session are required. Unknown checks never prove closure.
- Preserve running profiles; no kill/reset/duplicate. Failed attempts have a
  ten-minute cooldown. Open only the normal IBKR portal entry page.
- Public research remains independent of IBKR login. Manual Paper login notice
  is deduplicated; changed recovery failures can produce a new attention notice.
- Added `recover` CLI action and `OPENCLAW_BROWSER_AUTORESTART` toggle.
  Updated the existing Codex task, without another recurring schedule.
- Verification: 93 offline tests passed. Live local process/endpoint reported
  ready with `launched=false`; OpenClaw attachment and paper navigation were ready.
  The authenticated browser was not forcibly closed. Closed recovery was mocked.

## 2026-10-09: Supplemental Review Reliability

- After an OpenClaw upgrade, public navigation and independent analysis worked,
  but supplemental Claude review timed out.
- Native Claude sign-in check and a tool-free direct request succeeded; the
  request completed in seven seconds. This was not evidence of broker logout.
- Applied `CLAUDE_CODE_SAFE_MODE=1` only to the remote review subprocess, keeping
  existing sign-in/model/configuration. The exact slow customization was not
  identified. Small isolated OpenClaw request took 52 seconds; excerpt review
  took 68 seconds.
- A full-page review still failed, so input was bounded to six symbols,
  two 1,500-character excerpts and six representative quote rows per name.
  Original evidence/contract filters remained unchanged.
- Full TOU pass: MX and Barchart collected, eight qualified native quotes,
  accepted tool-free review in 69 seconds within its normal 90-second budget.
  Both research tabs closed, IBKR retained. Total pass: 162 seconds.
- Reject model failures/tool-call responses; log safe reason codes and retain
  observed evidence without a review. No provider/authentication change.

## 2026-10-09: Browser And Notification Reliability

- Observational browser commands retry once for transient failures within a
  shared 35-second budget; mutations are not blindly repeated.
- Failed accessibility snapshot can use an observed same-portal portfolio link.
- Added safe reason/failed-step diagnostics to distinguish connection failure,
  inability to verify account state and verified-session navigation failure.
- Telegram error logging avoids token-bearing exception URLs/raw bodies.
- Added meaningful failure-change notification deduplication and fail-soft paths.
- Earlier public TOU checks observed 11, then eight, qualified quotes on separate
  passes. Counts are dated test results, not permanent coverage expectations.

## 2026-10-08 To 2026-10-09: Canadian Data And Signal Fixes

- Primary regime now XIC; US context separate. Use prior completed close instead
  of a five-day chart baseline. Preserve reference dates and stale/pre-open labels.
- Request two years of history and enforce the 250-bar scoring floor. Yahoo
  removes the unfinished current daily bar before default production scoring.
- Store hard blockers on signals and retain them across catalyst/deep promotions.
- XTSE calendar supplies holidays/early-close dispatch windows and earnings
  trading-session counting; dispatch is checked again after research.
- Native CAD/CDE MX evidence required for priced option alerts. Listed expiry
  21-45 DTE, explicit recent quote time, positive/non-crossed prices, spread/OI/
  volume checks. No US-strike substitution or assumed adjusted multiplier.
- US OI and NQ/QQQ context no longer imply Canadian directional scoring or a
  measured futures funding basis.
- Gateway disabled by default; browser/Gateway optional-source combinations
  preserve independent analysis and Telegram. Unknown holdings are not zero.

## Historical Context

- 2026-10-02: project moved from laptop `.13` to Windows VM 104 `.15`; laptop
  tasks disabled. Six interactive weekday tasks registered. Historical notes
  about API balance/credential rotation need secure rechecking, not assumption.
- Earlier v5.5/v5.6 work added full-watchlist factsheets, catalyst/deep lanes,
  diversity controls, 8/day and 3/session stock quotas, structural bearish
  requirements for strict PUTs and the 08:30 Session-1 trigger.
- Earlier sweep/audit outputs remain in report artifacts. Their numbers are
  not fresh evaluation of the October data/selection changes.

## Remaining Verification Boundaries

Long-duration authentication, multiple unattended expiry/recovery cycles,
live forced browser-close recovery, complete watchlist website coverage,
fresh strategy profitability and any order workflow remain unestablished.
IBKR visible portfolio/option-chain extraction is not yet integrated into
scheduled research. Keep these boundaries explicit in future release notes.
