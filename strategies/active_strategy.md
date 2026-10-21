# Active Canadian Research Strategy

Baseline: 2026-10-09. This is the current code-backed specification, replacing
older v5.6-and-earlier descriptions where they differ. Scope: stock/option
research and Telegram notifications. Read [Handoff](../docs/HANDOFF.md) and
[Architecture](../ARCHITECTURE.md) for orchestration and limitations.

## 1. Universe And Timing

`watchlist.txt` has 75 non-comment entries at handoff. Symbols are reread each
session. All fetchable non-owned names are scored; no regime-based big-cap or
tech-only pre-filter controls the full-watchlist analysis. Actual triage covers
successfully scored factsheets, not names with no usable data.

Calgary local triggers: 05:45 premarket US, 07:00 premarket CA, 08:30 stock S1,
11:00 stock/options S2, 12:30 stock S3, 13:55 EOD summary. Stock sessions use
`1-DAY`. Weekly/monthly automatic sessions are not wired merely because their
rules exist in the scorer.

The XTSE calendar supplies session open/close, weekends, holidays and early
closes. Dispatch is blocked outside regular hours, the first 30 minutes and
the final 15 minutes. A missing calendar is analysis-only. Window checks occur
at scoring and again after potentially slow model/browser research.

## 2. Market Context

`market_context.chart_snapshot()` compares the regular price with the immediately
preceding completed daily bar. Yahoo `chartPreviousClose` can be the start of
the chart range and is not used as a daily reference. Store quote time, reference
date/close, capture time, source and status in the market snapshot.

Fractions are used internally: `-0.01` means -1%. Only `live` and `closed` status
percentages are usable for direction. Premarket, previous-session, stale,
invalid-timestamp and missing data do not become today's direction or zero.
The market snapshot's clock/status logic is distinct from the authoritative
XTSE dispatch-window calendar.

| Label | Exact direction condition |
|---|---|
| CANADA BULLISH | Usable XIC change greater than +0.5% |
| CANADA BEARISH | Usable XIC change less than -0.5% |
| CANADA NEUTRAL | Usable XIC within inclusive +/-0.5% |
| CANADA NOT OPEN | XIC snapshot has premarket status |
| CANADA MARKET CLOSED / UNKNOWN | Closed-market/unavailable status as returned by classifier |
| US BULLISH | Both usable QQQ and SPY greater than +0.5% |
| US BEARISH | Both usable, and either less than -0.5% |
| US NEUTRAL | Remaining usable case |
| US NOT OPEN / MARKET CLOSED | Both QQQ/SPY snapshots are premarket / both have market-closed status |
| US UNKNOWN | At least one unusable input outside those shared states; identifies ticker and reason |
| GOLD-STRONG / WEAK / FLAT | Usable GC=F above +0.5% / below -0.5% / inclusive middle |
| GOLD-UNKNOWN | Gold input stale/unavailable |

US context never substitutes for the primary Canadian label. A one-day broad
market label differs from a stock's multi-week technical trend or news catalyst;
a bullish stock setup does not mean the entire Canadian market is bullish.

Commodity/sector data include CL=F, NQ=F, VIX and XEG/XGD/XFN/XIT/XMA/XUT/XRE/ZIN.
NQ-vs-QQQ returns are different-session context, not a measured futures funding
basis. Missing sector/oil/VIX inputs may fail open under current filter rules.

Since October 19, Telegram and shared AI context display sector snapshot
percentages with their status, quote time and comparison date, independently of
the live-only `sector_etf_pct` filter inputs. A pre-open quote is useful dated
reference context, not unavailable data or today's direction. Missing sources
still say unavailable; stale quotes stay labelled stale. The premarket label
says "last regular quote", not "close": a thin ETF's last quote may be intraday.
VIX display likewise includes status/time even when only an older reference
level is available; the usable `vix_level` filter input is unchanged.

## 3. History And Reference Prices

`HISTORY_DURATION = "2 Y"`; `score_symbol()` requires
`max(250, timeframe.min_history_days)` rows. The Yahoo path requests roughly
504 trading bars and excludes the current daily bar while the market is open.
It records source/capture attributes. Gateway bars use a separate legacy adapter;
completed-bar semantics should be audited before re-enabling that source.

Intraday alert entry/stop/target levels come from completed daily bars. They are
reference levels, not verified executable quotes. Available Gateway quotes may
supplement tradability; without them, spread is unknown and daily volume is
used. Do not claim live fillability merely because the bar gate passes.

## 4. Mandatory Stock Filters

`Signal.hard_blockers` persists every mandatory rejection through AI promotions.
Observed failures cannot be overridden by an LLM, catalyst or quota target.

| Check | Actual current behavior |
|---|---|
| History | Fewer than 250 bars, or larger timeframe minimum, blocks |
| Price validity | Nonfinite/nonpositive underlying blocks |
| Tradability | Price at least CAD 1.50; volume at least 30,000; dollar volume at least CAD 100,000 |
| Stock spread | If both bid/ask positive, reject crossed quotes or spread greater than 1.5% of midpoint |
| Time window | XTSE dispatch window, enabled by the production stock runner |
| VIX | Greater than 25 blocks; absent value passes |
| Energy oil gate | Listed energy set with WTI at or below -2.5% blocks longs; missing WTI passes |
| Sector ETF | At or below -1.5% blocks; missing/unmapped sector passes |
| News | Recent strong ticker/alias bearish news result blocks; default lookback six hours |
| Earnings | Known earnings within the next three trading sessions, including today's relevant event, blocks |
| Climax | More than 18% above EMA-50 blocks a stock long |
| Bearish trend | Close below EMA-50 below EMA-200 blocks |
| Death cross | Recent bearish EMA-50/EMA-200 cross blocks a stock long |
| Optional ADX gate | Only if selected; ADX below configured threshold blocks |

Tradability missing bid/ask does not prove a tight spread. Missing earnings date
returns a warning and currently does not block. `skip_external_io=True` bypasses
live earnings/options/insider calls for historical/offline evaluation, not normal
production. These fail-open/bypass cases must not be described as verified data.

The news filter uses matching metadata to distinguish ticker/alias matches from
noisy sector mentions; legacy events lacking match type remain strong matches.
It walks newest events first and returns the first applicable result. This is
not a sophisticated all-headline conflict resolver.

## 5. Core Technical Rubric

Seven core checks; MACD cross and MACD-above are alternatives, not two core points.

| Core check | Condition | Weight |
|---|---|---|
| Short trend | Close greater than EMA-50 | 1.0 |
| Long trend | Close greater than EMA-200 | 1.0 |
| RSI | Appropriate inclusive continuation/pullback zone | 1.0 |
| MACD | Recent bullish cross, otherwise above signal | 1.25 cross / 1.0 above |
| Volume | Greater than 1.2 times 20-bar average | 1.0 |
| Bollinger | Percent B less than 0.95 | 0.75 |
| Support | Strictly between 0% and 5% above positive pivot support | 1.0 |

Continuation RSI is 50-70 when close is above EMA-50 and MACD above signal.
Otherwise bank pullback RSI is 35-60; default pullback RSI is 30-55.

Additional checks:

| Bonus | Condition/weight |
|---|---|
| Breakout | Above prior resistance with volume greater than 1.5x; 1.25 |
| Golden cross | Recent EMA-50 above EMA-200 cross; 0.75 |
| RS vs XIC | Positive relative 20-bar return; 0.75 |
| Weekly trend | More than 50 weekly rows and close above weekly EMA-50; 1.0 |
| News boost | Applicable bullish news; 0.5 |
| Insider | Meaningful returned insider-buy modifier; 0.5 |
| ADX bonus | Only in bonus mode, ADX at least threshold; 0.5 |

CAD US-option context does not add directional stock points or impose converted
US wall prices. Futures context does not add a funding-basis score. Although
weight dictionary entries remain for legacy options/futures logic, that does
not prove they are active in the Canadian path.

Raw score counts all passed core/bonus checks and may exceed seven. Weighted
score sums weights and aids ranking/confidence; it is not a strict weighted
entry threshold. Technical BUY requires raw score at least the adjusted minimum,
at least three core checks, no bearish trend and no death-cross blocker.

## 6. Soft Threshold Adjustments

Add to the minimum required score, not the raw score:

- Sector change at or below -0.5%, but above -1.5%: +1.
- ATR/close below 0.8% or above 6%: +1 when ATR valid.
- Close below prior resistance by at most 1%: +1; already above resistance gets
  no resistance penalty.
- Primary regime containing BEARISH: +1.
- Non-CAD option modifiers exist in the general scorer but are not CAD gates.

ADX default is off; its configured threshold is 20 when used. Mixed setup is
an informational warning, not an additional mandatory structure gate.

## 7. Timeframe And Exit Parameters

| Timeframe | Base min score | Fixed TP/SL if fixed mode selected | Metadata history minimum |
|---|---|---|---|
| INTRADAY | 5 | +5% / -2.5% | 30 |
| 1-DAY | 5 | +5% / -2.5% | 30 |
| 2-DAY | 5 | +6% / -3% | 30 |
| 1-WEEK | 5 | +8% / -4% | 60 |
| 2-WEEK | 6 | +12% / -6% | 90 |
| 1-MONTH | 6 | +15% / -7% | 120 |
| 3-MONTH | 6 | +25% / -12% | 200 |

The 250-bar floor supersedes all smaller metadata minima. Production uses
ATR stop `close - 2.0*ATR` and target `close + 3.0*ATR`. If ATR is unavailable,
`atr_based_exits()` uses -2.5%/+5%, independent of the per-timeframe fixed-mode
percentages above. ATR helper defaults are 1.5/2.5, but the scorer/production
promotion path explicitly passes 2.0/3.0.

Suggested quantity uses a CAD 4,000 reference divided by price, with minimum
one share. This is informational, not portfolio risk sizing, capital allocation,
buying-power enforcement or an order. No automated exits or capital/loss caps
are enforced in the current alert workflow.

Confidence is computed from reaching base/extra raw score, weighted score and
recognized setup, clamped 1-5. It is not a calibrated probability. An API veto
can reduce confidence in technical stock/option paths; promotion approval is
separately gated.

## 8. Candidate Lanes And Dispatch

| Lane | Entry | Permission to send |
|---|---|---|
| Technical | Rubric-passed deterministic survivor | Existing sendability plus dispatch gates |
| News-driven | Rubric failed, fresh catalyst, no hard blocker | Explicit stage-two `send_ok`, no veto, dispatch gates |
| Deep-scan | LONG conviction at least 4, no hard blocker, not already in pool/owned/sent today | Same stage-two approval/no-veto requirement |

A bullish news pick that also passed technicals may be labelled BOTH. API
failure does not automatically promote news/deep candidates. The final loop
checks blockers again before synthesizing any promotion's BUY reference levels.

Stock quota: eight successful cards/day, three/session. Sector cap: two/day
for mapped sector ETF. Duplicate same-day names are skipped. Repeat lookback
is the preceding three calendar days, excluding today, not three exchange
sessions; a fresh catalyst may waive that cooldown, not hard blockers or quotas.
Unknown/unmapped sector means no mapped-sector cap, not an inferred sector.

Browser verification is supplemental for stocks. Missing browser evidence is
labelled but does not suppress an otherwise eligible independent stock card.
The browser shortlist has six primary names. Session 2 interleaves ranked LONG names
with strict technical PUT candidates and accepted AI SHORT names. A preview
allocates browser coverage only; it cannot approve a priced option card.
Session 2 may try further names of a side without verified matching contracts,
up to twice the primary symbol limit within the same deadline and lock. Only
six symbols receive supplemental model excerpts; unreviewed extras are labelled.

## 9. Options Direction And Coverage

Only Session 2 runs the scheduled options scan. Native Canadian watchlist names
do not need a US entry in `DUAL_LIST_MAP`; native CAD/CDE evidence remains mandatory.
Underlyings require at least 250 bars, tradability and earnings checks. The
option scorer is not identical to the stock scorer: it does not receive the
same VIX/oil/sector gates. The dispatch window is checked before priced sends.

Strict direction needs at least three points and more than the opposite side.
Bearish trend/death cross each add two bearish points; negative MACD/cross,
overextension, RSI above 72 and upper Bollinger evidence add context points.
A strict PUT also requires structural bearish evidence: bearish trend, death
cross or MACD below signal. Overbought alone is insufficient.

CALL evidence includes bullish trend (two), above EMA-50/EMA-200, positive
MACD/cross, golden cross and RSI 45-65. Bearish news blocks CALL, not PUT.

If no priced strict PUT exists, relaxed PUT runs only over triage SHORT names.
It rejects close above EMA-50 and requires a relative-weakness reason. Do not
claim that every relaxed candidate also meets the strict point/structure test.
Put/call OI is not a direction detector; the old P/C veto is inactive. Qualified
PUTs are never manufactured just to meet a two-PUT target.

## 10. Native Canadian Contract Validation

Every priced CAD option needs observed native evidence satisfying all:

1. Exact underlying symbol, CAD currency, CDE exchange, CALL/PUT right.
2. Source `MX visible table` with `m-x.ca`/`www.m-x.ca` host, or `IBKR CDE snapshot`
   from the verified Paper portal.
3. MX: recognized paired table columns and correct symbol page title. IBKR:
   integer contract ID, native CAD/CDE definition and R/D availability only.
4. MX: explicit quote-update timestamp interpreted in America/Toronto, not crawl
   time. IBKR: snapshot `_updated` timestamp, shifted back 15 minutes for delayed
   availability; missing timestamps never qualify.
5. Age at most 1,800 seconds, with only 60 seconds future tolerance.
6. Listed expiry 21-45 calendar days away, based on the validator's UTC date.
7. Positive strike and bid/ask with `0 < bid <= ask`.
8. Spread at most 25% of midpoint.
9. Open interest at least 100 OR volume at least 10.

The scorer chooses the nearest qualifying expiry and an available
near-underlying strike for the direction, not a generated contract. Dispatch
revalidates a matching symbol/right/expiry/strike quote after research.

No verified contract means an unpriced underlying watchlist only, without a
recommended strike, premium or expiry. Missing multiplier/Greeks/IV remain
unknown. Quote premium is per share; contract total requires a known multiplier,
which the current MX parser leaves unknown. Delayed/unspecified source mode is
labelled; a page's real-time label is not independent entitlement verification.

US-chain OI can appear as explicitly separate context, never converted to CAD
contract prices or interpreted as guaranteed Canadian support/resistance. The
old Gateway/FX/US-reference proposal is not the current CAD evidence pipeline.

Options quota is four successful priced cards/day, capped at two CALLs and two
PUTs separately. There is no cross-side spillover. Missing qualified candidates
produce a coverage notice/audit, not invented contracts. See
[Options And Review Repair](../docs/OPTIONS_AND_REVIEW_REPAIR.md). Underlying
watchlist notices are not counted as priced-contract cards. IBKR Paper's
GET-only CDE snapshot collection is implemented; extra visible-chain/TradingView
cross-checks remain follow-ups.

Session 2 samples IBKR strikes around the previous completed close, not a fresh
underlying quote. That reference is explicitly labelled through model input,
cards and audits; current ATM/ITM/OTM is unverified. MX may try names whose IBKR
sample has no qualified matching contract within the same bounded coverage.
Missing liquidity is not waived. Both source diagnostics are retained; no
global portal stream cancellation occurs. See the
[October 16 review repair](../docs/REVIEW_REPAIRS_2026-10-16.md).

The activity/spread gates above concern the native option's market, not account
cash, margin excess liquidity or the underlying share price. No account-based
affordability calculation is implemented here. Unpriced notices show up to two
ideas per side without recommending a strike/expiry/premium. Per-candidate
rejection diagnostics are retained in the coverage audit.

## 11. Evaluation And Maintenance

TP/SL and confidence are research annotations, not fills or enforced exits.
Historical technical backtests omit present-day news, browser layouts, model
ranking and actual execution. Old sweep/audit figures are historical artifacts,
not a lower bound on future results or proof of current profitability.

Preserve source timing, skipped reasons and successful-send logs. Evaluate
forward outcomes by lane, direction, sector and source availability. Re-run
out-of-sample evaluation after threshold/data/model changes rather than silently
increasing alert count or fabricating options to satisfy quotas.

Future changes must retain mandatory blockers, quote provenance, calendar gates,
optional-source independence and model approval defaults. Update this page and
the affected tests/configuration when code semantics change.
