# Review Repairs: October 16, 2026

This is the implementation record for the owner's approval to correct the
independent code-review findings. All edits and offline checks ran on Windows
trading VM 104 in `C:\Trading`. No Ubuntu configuration, authentication,
production `.env`, thresholds, quotas, scheduled-task definitions or runtime
state was changed. No live session, Telegram send, model inference or broker
operation was run for this repair.

This record covers the code-review repair only, before the separate later
owner-authorized saved-form Paper sign-in feature. That feature changed its own
opt-in and existing monitor prompt and was live-verified; see
[Saved-form Paper sign-in](PAPER_SAVED_LOGIN.md). Current system/test entry:
[System overview](SYSTEM_OVERVIEW.md). Preserve the original verification below
as a dated stage rather than treating its 204/18 counts as the latest totals.

## 1. Blank Snapshot Prices

`ibkr_options.SNAPSHOT_FN` now waits for parseable numeric bid **and** ask, not
just the presence of their field keys. Blank, whitespace, null, non-finite,
boolean and prior-close-prefixed values remain pending. Split responses are
merged. A genuine numeric zero is settled, but still fails the existing
positive-bid validator. Frozen/unsubscribed terminal states and the eight-round
limit remain bounded; missing liquidity is not invented by waiting longer.

`scripts/test_ibkr_snapshot.mjs` extracts the actual versioned Python-embedded
JavaScript and executes it with fake fetch/timers, not a rewritten copy of the
algorithm. Its 11 tests make no network requests.

## 2. MX Fallback After Rejected IBKR Samples

An IBKR name is covered only when it has at least one validated contract for
the requested direction, or a definitive no-CDE/no-month/no-expiry-in-window
result. Lookup failures, missing quotes, unknown liquidity, excessive spreads,
stale/frozen quotes and otherwise unusable samples can receive bounded MX
attempts. The collector labels an entirely rejected priced sample
`ibkr_no_qualified_contracts`.

The primary/replacement symbol caps and source deadlines still apply. A retry
is an opportunity to find evidence, not a promise of a contract or alert. Both
sources use the unchanged validator: exact native CAD/CDE identity, 21-45 DTE,
timestamp age at most 30 minutes, positive non-crossed prices, spread at most
25%, and OI at least 100 OR volume at least 10. Unknown liquidity is not zero
and is not sufficient to qualify.

When MX is attempted after IBKR:

- `source_kind` is `ibkr+mx`.
- `ibkr_evidence` retains the prior IBKR counts, reasons and side diagnostics.
- Top-level `collection_reason`, `sources` and `contract_validation` describe
  the MX attempt, including source failure. Existing validated IBKR rows are
  retained alongside any validated MX rows, never overwritten by an MX failure.
- The coverage audit counts these names as IBKR-checked and includes
  `ibkr_collection_reason` / `ibkr_contract_validation` for unpriced candidates.
- Evidence notes distinguish combined-source attempts from MX-only collection.

Tests cover missing-quality fields, spread/staleness/frozen rejection, fallback
success/failure, definitive absence, an already qualified IBKR result, and
source-diagnostic preservation. No US/FX chain was introduced.

## 3. No Global Stream Cancellation

The snapshot collector no longer calls or allow-lists
`marketdata/unsubscribeall`. Although it is a GET endpoint, it closes all
backend instrument streams and can affect an already-open broker page. The
lookup guard now permits only GETs to `secdef/search`, `secdef/strikes`,
`secdef/info` and `marketdata/snapshot`.

No replacement state-changing request or global/scoped unsubscribe operation
was added. Backend market-data line limits and subscription availability can
still limit snapshots; failures remain unavailable evidence and use the bounded
fallback. This repair does not claim unlimited data capacity or private stream
ownership. Browser-tab cleanup is separate and unchanged.

## 4. Work Budget Starts After Browser Access

The public collection/review deadline is now initialized inside the
`run_research()` callback, after the shared lock and browser availability check,
and after the separately budgeted IBKR stage when used. Stock-only research
therefore receives its full configured work budget even after waiting for the
40-minute monitor. Existing lock waits remain 180 seconds for research and
360 seconds for keepalive. Cleanup/post-research activity retain separate budgets.

A fake-clock regression advances 180 seconds before the callback, then verifies
all six MX attempts and the reserved 90-second review opportunity. It does not
change the monitor cadence or create another scheduler.

## 5. Previous Close Is Not Current Spot

This repair deliberately does **not** add another underlying price feed.
IBKR strike sampling still uses the previous completed daily close. Each IBKR
row now records `spot_reference=previous_completed_close`. The chain resolver,
`OptionsSignal`, DeepSeek input, successful-send audit and normal/fallback
Telegram cards retain that meaning. Cards label **Previous completed close**,
state that it is a strike-selection reference, and leave current moneyness
unverified rather than reporting ATM/ITM/OTM from that historical number.

MX rows with an observed underlying price retain their quoted-spot label; a
missing MX underlying price that falls back to the daily close is also labelled
as a reference. The options research prompt no longer assumes a US dual listing
or an ATM strike, and quotes premium per share rather than per contract.

Residual limitation: a large intraday gap may leave the sampled IBKR strike
range poorly centered. Current underlying snapshots and their own timestamp
validation would be a separate integration; no fresh price is claimed here.

## 6. Offline Browser-Test Isolation

Cleanup-only tests now mock the post-research activity hook. Its real behavior
continues to be tested separately with mocked Paper probes/navigation. A
class-wide subprocess guard fails on any unmocked external call, including when
the production fail-soft handler catches its exception. The tests cannot
silently attempt real SSH just because a new `finally` hook was added.

## 7. Verification And Deployment

From `C:\Trading` on Windows VM 104:

```powershell
.\.venv\Scripts\python.exe -m unittest test_browser_launcher test_browser_research test_market_context test_research_pipeline test_telegram_notify test_analysis_health test_options_review
node --test scripts/test_ibkr_snapshot.mjs scripts/test_openclaw_public_review.mjs
.\.venv\Scripts\python.exe -m pip check
```

Verification after this repair: **204 Python tests and 18 Node tests pass**;
dependency check clean. Tests mock broker, provider and Telegram effects. The
PowerShell scheduler-wrapper test uses disposable stub scripts, not production
analysis. None of this proves quote entitlement, a future live alert, or
server-side session longevity.

The existing scheduled Python processes load these files on their next start;
no task re-registration, administrator prompt, duplicate heartbeat or OpenClaw
restart is needed. An already-running process keeps its previously imported
code until it exits. Existing logs and reconnect attempt blocks were preserved.

The real-click monitor, post-research activity and guarded reconnect remain in
place. IBKR can still end the session server-side; the helper must report that
state honestly and independent analysis must continue. This repair did not run
a live keepalive or a weeks-long session test.
