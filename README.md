# TSX Research & Options Signal Agent

An automated research system for a 75-name Canadian (TSX/TSXV) watchlist. On a
fixed daily schedule it combines market data, news, a large-language-model
triage of every name, browser-collected evidence and live option-chain quotes,
then sends ranked stock and option ideas to Telegram for a human to review and
trade manually.

It is a **research and alerting tool**. It does not place orders. Broker access
is limited to an IBKR **Paper** (simulated) account, and every broker
interaction is read-only and guard-railed.

> This is a sanitized copy of a private production system, shared as a code
> sample. Credentials, runtime logs, broker session state, personal data and
> machine-specific addresses have been removed or replaced with placeholders.

---

## What It Does

Every trading day, Calgary (Mountain) time:

| Time | Job | Output |
|---|---|---|
| 05:45 | US pre-market | Catalyst alerts, US/commodity context |
| 07:00 | Canadian pre-market | Pre-open heads-up |
| 08:30 | Session 1 | Full watchlist scan, ranked stock alerts |
| 11:00 | Session 2 | Stock scan plus the options scan (max 2 CALL + 2 PUT per day) |
| 12:30 | Session 3 | Stock scan |
| 13:55 | Session 4 | End-of-day summary |
| every 40 min, 24/7 | Paper-session monitor | Keeps the Paper browser session active, health notices |

For each session the pipeline:

1. **Market context:** XIC sets the Canadian regime; SPY/QQQ/NQ futures, gold,
   oil, VIX and sector ETFs are labelled separately with quote times, never
   silently mixed.
2. **Technical scoring:** scores all 75 names on two years of completed daily
   bars. It uses a rubric of trend, RSI, MACD, volume, Bollinger, support,
   breakouts, relative strength vs XIC and the weekly trend. Hard blockers
   (earnings blackout, bearish trend, climax extension, oil/sector/VIX gates)
   can never be overridden by later AI stages.
3. **Full-watchlist AI triage:** DeepSeek reviews a compact factsheet for every
   name (momentum, free cash flow, leverage, news, options positioning) in small
   batches, with a missing-only repair pass. This deliberately avoids a
   large-cap pre-filter, so smaller companies get the same look.
4. **Ranking and narratives:** the LLM ranks the shortlist and writes
   rationale/concerns. A news-driven lane can surface catalyst names, but only
   with explicit AI approval.
5. **Evidence collection:** a dedicated Chrome profile, driven by
   OpenClaw on a separate Linux VM, reads public pages
   (Montreal Exchange option tables, Barchart). A tool-free Claude/GPT reviewer,
   alternating by day, adds notes but can never add numbers.
6. **Native Canadian options (Session 2):** a read-only reader pulls CDE option
   chains from the logged-in IBKR Paper portal for every CALL/PUT candidate:
   every expiry in the 21–45-day window, strikes around spot, bid/ask, open
   interest, volume, delta and IV. Contracts must pass strict quality gates
   (fresh quote, spread ≤ 25%, OI ≥ 100 or volume ≥ 10). The public MX page is
   the fallback.
7. **Dispatch:** HTML-safe Telegram cards with entry/stop/target, R:R, setup,
   score, AI notes and data provenance, plus daily quotas, sector caps and
   cooldowns.

## Architecture

```text
Windows VM (production runner)                      Linux VM (OpenClaw)
┌───────────────────────────────────────────┐        ┌────────────────────────────┐
│ Windows Task Scheduler (6 weekday jobs)   │        │ OpenClaw CLI / gateway     │
│   └─ run_session.ps1 → session_runner.py  │        │   browser profile control  │
│        ├─ market_context, signals,        │  SSH   │   (CDP to Windows Chrome)  │
│        │  filters, indicators             │ ─────► │ Claude / GPT public review │
│        ├─ deep_scan → llm_researcher      │        │   (tool-free, notes only)  │
│        │  (DeepSeek / Claude / OpenAI)    │        └────────────────────────────┘
│        ├─ research_pipeline               │
│        │   ├─ ibkr_options (read-only)    │        External data
│        │   └─ MX / Barchart pages         │        ├─ Yahoo / yfinance
│        ├─ options_signals                 │ ─────► ├─ RSS + Telegram news (Telethon)
│        └─ telegram_notify                 │        ├─ Montreal Exchange (CDE)
│ 40-min monitor → browser_research.py      │        └─ IBKR Paper portal (read-only)
│   (real clicks, recovery, health notices) │
│ Dedicated Chrome profile (IBKR Paper tab) │ ─────► Telegram bot (alerts)
└───────────────────────────────────────────┘
```

Key design choices:

- **Independent by default.** Analysis runs without IBKR Gateway, browser or
  broker login. Each optional source fails soft and is labelled, never
  silently replaced.
- **AI as an analyst, not an authority.** LLM output can rank, explain and flag
  ideas, but cannot erase hard blockers, invent contracts, Greeks or prices, or
  trigger actions. Model and web text are treated as untrusted data.
- **Provenance everywhere.** Quotes carry source, timestamp and data mode
  (real-time, delayed, frozen). Unknown is never treated as zero.
- **Strict broker boundaries.** The IBKR option reader can only issue GET
  requests to an allow-list of contract-definition and market-data endpoints.
  Browser actions are limited to verified Paper pages, with guards for login
  screens, two-factor prompts and dialogs, and no repeated clicks after an
  uncertain result.
- **One shared OS lock** coordinates research and the 40-minute monitor across
  processes.

## Main Modules

| File | Responsibility |
|---|---|
| `session_runner.py` | Session orchestration, pools, quotas, dispatch |
| `signals.py`, `indicators.py`, `filters.py` | Technical rubric, indicators, gates and penalties |
| `market_context.py`, `trading_calendar.py` | Regime labels, previous-session references, XTSE calendar |
| `deep_scan.py`, `llm_researcher.py` | Full-watchlist triage, provider adapter, robust JSON parsing |
| `research_pipeline.py` | Evidence collection, MX parsing, shared contract quality gates, AI review |
| `ibkr_options.py` | Read-only IBKR Paper CDE contract and quote reader |
| `options_signals.py` | CALL/PUT direction, contract selection, option cards |
| `browser_research.py`, `browser_launcher.py` | OpenClaw adapter, shared lock, Paper monitor, recovery |
| `paper_login.py` | Owner-enabled Paper sign-in helper for the browser's saved form |
| `analysis_health.py` | Independent scheduled-task health monitoring |
| `news_*.py`, `premarket_scan.py` | Telegram/RSS intake, matching, catalyst alerts |
| `enrichment.py`, `fundamentals.py`, `ownership_data.py`, `insider_transactions.py` | Fundamentals and ownership enrichment |
| `telegram_notify.py` | HTML-safe delivery with plain-text retry and redacted errors |
| `backtest.py`, `backtest_sweep.py`, `eval_signals.py` | Historical simulation, parameter sweep, sent-signal audit |
| `scheduled/` | Task Scheduler registration and session wrapper |
| `scripts/` | OpenClaw review bridge (Node) and its tests |

## Running It

Requirements: Windows with Python 3.12. Optional: an OpenClaw host reachable
over SSH, a Telegram bot, an LLM API key, and an IBKR Paper account.

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-lock.txt
Copy-Item .env.example .env      # then fill in your own values; see docs/CONFIGURATION.md
```

Offline test suite. Every external call is mocked, so it needs no network,
broker, browser or API key:

```powershell
.\.venv\Scripts\python.exe -m unittest test_paper_login test_options_review test_analysis_health test_browser_launcher test_browser_research test_market_context test_research_pipeline test_telegram_notify
```

Run a session manually. This sends real Telegram messages and may spend LLM
credit:

```powershell
.\.venv\Scripts\python.exe session_runner.py session1
```

Schedule the six daily jobs with `scheduled/register_tasks.ps1`. See
[Operations](docs/OPERATIONS.md) and [Configuration](docs/CONFIGURATION.md) for
every setting.

## Historical Backtest (Technical Rubric Only)

`backtest.py` replays the technical rubric with ATR-based exits on five years
of daily bars (2021-06 to 2026-06, 75 symbols). It does **not** simulate news,
earnings blackouts, spreads/liquidity, AI stages or the options pipeline, so
the results are directional only.

| Metric | Value |
|---|---|
| Trades | 3,989 |
| Win rate | 44.1% |
| Average win / loss | +8.09% / −5.69% |
| Expectancy per trade | +0.39% |
| Profit factor | 1.12 |
| Average holding period | 13.1 bars |
| Exits: take-profit / stop-loss / timeout | 1,731 / 2,217 / 41 |

The report's compounded figures (CAGR and drawdown) come from naive full-equity
compounding of overlapping trades and are not meaningful as portfolio
performance, so they are omitted here. Strongest sector: financials (XFN, 50.9%
win rate). Weakest: utilities (XUT). A positive expectancy with a 1.12 profit
factor is a modest edge before costs; it is not evidence of live profitability.

## Engineering Notes

- 229 offline unit tests covering contract provenance, quote validation, lock
  behaviour, browser guards, notification redaction, triage coverage, quota
  caps and option-side balance.
- Data-identity fixes, for example never falling back from a TSX symbol to an
  unrelated US company with the same letters (TELUS `T` vs AT&T).
- Detailed operational documentation: [handoff](docs/HANDOFF.md),
  [architecture](ARCHITECTURE.md), [active strategy](strategies/active_strategy.md),
  [change record](docs/CHANGELOG.md) and a
  [step-by-step work log](docs/WORK_LOG_2026-10-15_16.md).
- Developed with AI coding assistants (Claude Code, Codex) under a written
  model-neutral rulebook ([AGENTS.md](AGENTS.md)).

Some docs refer to private operator files that are intentionally not included
in this copy (`logs/`, `pending_tasks.md`, the production `.env`).

## Disclaimer

For research and educational purposes only. Not financial advice. No warranty.
Trading involves risk.
