# Configuration And Model Migration

Baseline: 2026-10-09. Settings below are derived from the executable code, not
from assumed provider features. Runtime secrets belong in `C:\Trading\.env`
or the owner's existing secure configuration, never in these guides.

## 1. Configuration Loading

- Run from `C:\Trading` with the project venv, not system Python.
- `ibkr_client.py` uses python-dotenv; its import loads environment values.
  The browser CLI explicitly loads the project's `.env` before actions.
- `llm_researcher.py`, `telegram_notify.py` and `news_fetcher.py` also have
  simple `.env` readers. Nonempty process-environment values take precedence.
- API provider/key/model are captured at module import. Start a new Python
  process after changing them; an already-running session keeps its old values.
- The PowerShell wrapper separately reads Gateway flags and sets
  `IBKR_GATEWAY_UP` for Python. This is not a universal configuration parser.
- Use one `KEY=value` per line. Keep comments on separate lines, do not quote
  values or append inline comments, and avoid duplicate keys. The simple loaders
  do not implement all shell/dotenv quoting conventions.
- Boolean Python switches accept case-insensitive `true`; other values disable
  them. Numeric research settings are parsed/clamped as described below.

Changing a template does not change the current private `.env`. Never replace
the production file with the example during an upgrade.

## 2. Environment Reference

### Gateway

| Variable | Code default | Meaning |
|---|---|---|
| `IBKR_GATEWAY_ENABLED` | false, unless `REQUIRE_GATEWAY=true` and no explicit override | Enable optional Gateway connection/probe |
| `REQUIRE_GATEWAY` | false | Legacy hard-stop requirement; leave false for the current independent workflow |
| `IBKR_HOST` | `127.0.0.1` | Gateway host used by legacy client |
| `IBKR_PORT` | `7497` | This project's configured paper endpoint; verify the actual client socket setting |
| `IBKR_CLIENT_ID` | `1` | API client ID; simultaneous manual clients must not collide |
| `IBKR_GATEWAY_UP` | supplied by wrapper | Runtime probe result, `1`/`0`; not a credential or normal user setting |
| `IBKR_SESSION` | supplied by wrapper | Session label for runtime context |

`REQUIRE_GATEWAY=true` is an analysis-dependency switch, not a trading-mode
implementation. There is no implemented `TRADING_MODE=auto/paper` switch.
Enabling Gateway does not implement browser holdings extraction or order
execution. If enabled, keep the existing read-only account configuration.

### Telegram

| Variable | Used by | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `telegram_notify.py` | Bot credential for notification delivery |
| `TELEGRAM_CHAT_ID` | `telegram_notify.py` | Destination chat; treat as private configuration |
| `TELEGRAM_API_ID` | `news_fetcher.py` | Telethon user API application ID; numeric |
| `TELEGRAM_API_HASH` | `news_fetcher.py` | Telethon application credential |

The bot send credential and Telethon news credentials are separate. The
existing `claude_news_session.session` contains the authorized user session;
its filename is historical, not tied to which LLM is selected. Missing news
authorization is not fixed by changing the bot token. Preserve that session
securely and do not create unattended login attempts.

### API Research Model

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | empty | One of `deepseek`, `claude`, `openai`; empty/unknown disables API research |
| `LLM_API_KEY` | absent | Credential for the selected provider |
| `LLM_MODEL` | provider default below | Optional exact model ID |

The defaults in `llm_researcher.DEFAULT_MODELS` at handoff are:

| Provider | Default string in code | Adapter |
|---|---|---|
| `deepseek` | `deepseek-reasoner` | OpenAI SDK Chat Completions, `https://api.deepseek.com/v1` |
| `claude` | `claude-haiku-4-5` | Anthropic Messages SDK |
| `openai` | `gpt-4o-mini` | OpenAI SDK Chat Completions, SDK default endpoint |

These are descriptions of code defaults, not claims that a model is currently
available, cheapest, best, or supported on a particular subscription. Check the
provider's current official documentation when selecting a replacement.

### Browser And Optional Review

| Variable | Default | Scope |
|---|---|---|
| `OPENCLAW_BROWSER_CHECK` | true | Passive browser availability reporting in sessions |
| `OPENCLAW_RESEARCH_ENABLED` | true | Public shortlist evidence collection |
| `OPENCLAW_RESEARCH_MAX_STOCKS` | 6 | Clamped to 1-6 primary names; option-side replacements may try up to twice this total within the same deadline; review remains six names |
| `OPENCLAW_RESEARCH_TIMEOUT` | 300 | Clamped to 30-300 seconds public collection/review work, starting after browser lock/recovery and the optional IBKR stage |
| `OPENCLAW_IBKR_CHAINS` | true | Session 2: read-only IBKR Paper CDE contract/quote lookups for every option-direction name before MX pages; skipped when Paper is not ready |
| `OPENCLAW_IBKR_CHAIN_TIMEOUT` | 300 | Clamped to 60-300 seconds; separate from the MX/review budget |
| `OPENCLAW_AI_REVIEW` | true | Supplemental OpenClaw model review of public excerpts |
| `OPENCLAW_REVIEW_CLAUDE_MODEL` | `anthropic/claude-opus-5-5` | Claude-day per-request review model |
| `OPENCLAW_REVIEW_OPENAI_MODEL` | `openai/gpt-6.1-sol` | GPT-day per-request review model |
| `OPENCLAW_REVIEW_THINKING` | medium | Only medium/high accepted; not extra-high/ultra |
| `OPENCLAW_REVIEW_TIMEOUT` | 90 | Review request seconds, clamped 30-120; collection reserves time |
| `OPENCLAW_REVIEW_FALLBACK` | true | Permit the alternate provider as per-request fallback |
| `OPENCLAW_KEEPALIVE_ENABLED` | true | Paper-session helper; false skips its check and recovery |
| `OPENCLAW_PAPER_RECONNECT` | false | Opt-in guarded disconnected-Paper banner reconnect; production enabled October 11 |
| `OPENCLAW_PAPER_SAVED_LOGIN` | false | Owner-enabled October 16: helper-only Paper selection and submission of Chrome's already-filled SSO form; no credential retrieval/entry or challenges |
| `OPENCLAW_BROWSER_AUTORESTART` | true | Confirmed-closed local dedicated Chrome recovery |
| `OPENCLAW_SSH_TARGET` | `trader@192.0.2.16` | Existing key-authenticated SSH target |
| `OPENCLAW_CLI_PATH` | `/home/trader/.nvm/versions/node/v24.21.0/bin/openclaw` | Absolute CLI path on Ubuntu, not Windows |

`OPENCLAW_BROWSER_CHECK=false` disables passive reporting, not public research,
paper checks or auto-recovery in every context. Use the specific switches to
disable those actions. With `OPENCLAW_AI_REVIEW=false`, collection/cleanup still
run. With `OPENCLAW_RESEARCH_ENABLED=false`, independent analysis continues but
native public-contract evidence is unavailable to the scheduled options path.

There are no new switches for the October 16 review repairs: numeric snapshot
polling, quality-aware MX fallback, stream preservation, post-lock work budgets
and previous-close labels are normal behavior. No live underlying quote is
fetched by the IBKR adapter; sampling still uses the completed-close reference.
See [Review repairs](REVIEW_REPAIRS_2026-10-16.md) for diagnostic fields and limits.

Triage code constants are `deep_scan.TRIAGE_BATCH_SIZE=10` and
`TRIAGE_TIMEOUT=360`. One missing-only repair pass uses batches of five.
Only triage supplies bounded request timeouts, capped at 60 seconds, with SDK
automatic retries disabled; other model callers keep their existing defaults.
No provider/model/key/output-token cap changed in this repair. More small
requests can consume more allowance. Missing final answers remain unknown.

Production `OPENCLAW_PAPER_RECONNECT=true` follows the owner's October 11 request
and live verification. It never enters credentials or accepts confirmations.
Preserve `logs/browser_reconnect_state.json` across migration: failed/uncertain
attempts stay blocked until healthy Paper is observed. Full contract and opt-out:
[Paper reconnect](PAPER_RECONNECT.md).

Production `OPENCLAW_PAPER_SAVED_LOGIN=true` follows the owner's October 16
request. Preserve `logs/browser_paper_login_state.json` across migration.
Disabling the flag leaves public research and banner reconnect independent.
Both fields must already be filled; no broker credentials belong in `.env`, the task
prompt or this project's state. Guard and verification limits:
[Saved-form Paper sign-in](PAPER_SAVED_LOGIN.md).

The SSH target and CLI path are validated, quoted and bounded. No OpenClaw
gateway token, broker username or password is stored in the example. SSH uses
existing keys/known hosts with `BatchMode=yes`, `StrictHostKeyChecking=yes` and
a three-second connect timeout. Do not disable host verification to repair it.

## 3. Fixed Settings That Are Not Environment Flags

| Setting | Location/value |
|---|---|
| Project root | Several modules hard-code `C:\Trading`; moving it is a code migration |
| Stock quotas | `session_runner.py`: 8/day, 3/session |
| Diversity | 2/sector ETF/day; three calendar-day repeat lookback, fresh-catalyst waiver |
| Catalyst pool | At most 5 injected news-driven names per session |
| Deep triage | `deep_scan.py`: batches of 10, missing-only repair batches of 5, LONG/SHORT conviction threshold 4 |
| History duration | `session_runner.py`: `2 Y`; score minimum at least 250 bars |
| Browser profile | `CanadianResearchBrowser.PROFILE = canadian-stocks` |
| Chrome executable | `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| Chrome user-data directory | `%LOCALAPPDATA%\CanadianStockChrome\Profile` |
| Native CDP port | Loopback 9224; bridge .15:9225 |
| Recovery cooldown | `browser_launcher.py`: 600 seconds |
| Command/cleanup budgets | Browser commands 35 seconds; cleanup 90 seconds |
| Paper-check budget | `browser_research.py`: 240 seconds |
| Saved-form sign-in budget | `paper_login.py`: up to 100 seconds, capped by the Paper helper's remaining time |
| Research/monitor lock waits | `browser_research.py`: 180 / 360 seconds; separate from the inner work budgets |
| Optional review budget | Default 90 seconds; bounded by remaining collection/review budget |
| Heartbeat interval | Codex automation, 40 minutes, not a `.env` variable |
| Weekday trigger times | `scheduled/register_tasks.ps1`, not the strategy Markdown |
| Analysis task principal | Existing owner, limited password-free S4U since October 13; no desktop-login prerequisite |

Do not invent `LLM_BASE_URL`, `MAX_OPEN_POSITIONS`, `TRADING_MODE` or browser
profile flags and assume code reads them. Search the code before adding a
setting. A provider-specific default URL remains inside the adapter.
S4U is an installed Windows task setting, not a `.env` flag. Existing HTTPS/key-
authenticated dependencies were verified on VM 104; GUI recovery stays separate
and interactive. Read [Scheduler reliability](SCHEDULER_RELIABILITY.md) before
restoring task definitions or changing run mode.

## 4. Replacing The Repository Assistant

Changing the coding assistant/model needs no strategy rewrite. Give the new
assistant workspace access and this starting instruction:

> Work in C:\Trading. Read AGENTS.md and docs/HANDOFF.md first, then the linked
> architecture, strategy, configuration and operations guides. Preserve the
> dirty worktree and private state. Begin with the named offline regression
> suite and passive browser status. Do not run live message/account tests or
> change provider/authentication/network settings without explicit scope.

Some tools load `AGENTS.md`; Claude Code uses `CLAUDE.md`, which points to it.
Other tools may need a manual instruction to read the entry file. Do not assume
every model has browser, SSH, shell or task-management capabilities. Report
missing capabilities as execution limitations, not production failures.

Replacing the assistant does not transfer Codex schedules to another app. If
retiring Codex, recreate the existing paper-check schedule intentionally in the
new execution environment, then disable the old one to avoid duplicate workers.
The Windows analysis tasks are independent of the assistant's chosen model.

For the exact existing monitor identity, reusable task prompt, capability checks
and safe replacement procedure, follow [Monitor migration](BROWSER_MONITOR_MIGRATION.md).
The 40-minute helper requires browser CLI access, not a research LLM. A direct
Windows scheduler could remove the assistant/subscription dependency, but none
is installed at this baseline; prepare disabled and pause the old task before
activating any replacement.

## 5. Replacing The API Research Model

For a supported provider:

1. Record current provider/model and configuration securely without printing
   the key. Do not put old/new credentials in a handoff or Git diff.
2. Choose an accessible model from official provider documentation. Verify its
   API supports the adapter's exact request and response fields.
3. Set `LLM_PROVIDER`, `LLM_API_KEY`, optionally `LLM_MODEL` in private `.env`.
   Preserve unrelated flags, credentials and browser configuration.
4. Start a fresh Python process. Check configuration presence with
   `llm_researcher.is_enabled()`; that is not a paid request or validity check.
5. Run the offline regression suite and mocked schema cases.
6. With owner approval, run one bounded live connectivity request, then a small
   no-Telegram research fixture. Assess parsing, latency, errors and ranking.
7. Observe a normal scheduled session and compare deterministic gates, notes
   and source labels. Revert the three private settings if the adapter fails.

Current adapter details:

- OpenAI-compatible requests use `chat.completions.create`, one user message,
  `response_format={"type":"json_object"}` and `max_tokens`. Output budget is
  8,000 if the model ID contains `reasoner`, otherwise 4,000.
- Anthropic requests use `messages.create`, `max_tokens=2000` and one user
  message; the first text block is returned.
- API calls rely on SDK default retries/timeouts; Python does not enforce the
  browser's 90-second model limit here. Test slow/reasoning models explicitly.
- OpenAI-compatible parsing accepts final message `content` only. Empty final
  output is labelled `output_limit` or `no_final_answer`; reasoning-only text is
  not a verdict. Triage audits record missing symbols and incomplete coverage.
- Error exceptions in legacy API modules are not universally redacted. Inspect
  their logs privately; do not paste raw provider failures into Telegram/docs.

A new model may require different token parameters, structured-output mode or
response extraction. Changing only the model name is not universally sufficient.
For an unsupported provider, add a narrow branch/default in `llm_researcher.py`,
reuse an adapter only when its contract matches, and add failure/schema tests.
Preserve the downstream contracts below. No new provider was installed or
selected during the documentation update.

## 6. Model Output Contracts

### Full-Watchlist Triage

`triage_watchlist()` receives factsheets with technical state, momentum,
fundamentals, available option context, news and catalyst fields. The expected
JSON has a `verdicts` list and optional `summary`:

```json
{
  "verdicts": [
    {"symbol": "RY", "direction": "LONG", "conviction": 4,
     "reason": "Illustrative fixture, not a live recommendation"}
  ],
  "summary": "Illustrative fixture"
}
```

Directions are LONG/SHORT/NONE; conviction is 1-5. The prompt requests each
input symbol exactly once, reason at most 15 words and summary at most 30 words.
`deep_scan.py` batches at 25
names. Only recognized input symbols are relevant. LONG at least 4 may enter
the stock research pool if not hard-blocked; SHORT at least 4 identifies the
relaxed-PUT universe. Triage is not stage-two permission to dispatch.

### Stock Ranking

```json
{
  "ranked": [
    {"symbol": "RY", "rank": 1, "rationale": "Illustrative fixture",
     "concerns": "", "veto_flag": false, "send_ok": true}
  ],
  "summary": "Illustrative fixture"
}
```

The adapter annotates original candidates: `llm_rank`, `llm_rationale`,
`llm_concerns`, `llm_veto`, and `llm_send_ok` only if supplied. Omitted approval
defaults differently by lane: technical names can remain eligible; news/deep
promotion names require explicit approval. Hard blockers always survive.

The current parser tolerates fences/truncated JSON and can save debug text.
It is not strict schema validation: booleans/ranks may be coerced by existing
code. A new provider must return real JSON booleans, numeric ranks and exact
symbols; do not count on permissive parsing to make arbitrary output safe.

### Option Ranking

Uses `ranked`, `symbol`, `option_type`, `rank`, `rationale`, `concerns`,
`veto_flag`. Annotation maps by `(symbol, option_type)`. Model ranking does not
create/replace expiry, strike, price or native-contract evidence. An options
veto is a confidence reduction in this implementation, not the same gate as
news-lane stock approval. Inspect `research_options_candidates()` and the
dispatch loop before changing these semantics.

### Supplemental Public Evidence Review

```json
{"reviews":[{"symbol":"RY","summary":"Illustrative fixture","concerns":""}]}
```

OpenClaw receives public excerpts only: at most six symbols, two source excerpts
of 1,500 characters each and six representative quote rows per symbol, plus the
full qualified-contract count. Original evidence and quote gates are unchanged.
The prompt requests each note at most 300 characters/two sentences; accepted
strings are capped at 600. Unknown symbols are ignored. Contracts returned by
a model are not merged into evidence.

The bridge response must have `ok=true`, an explicit integer zero-tool-call summary,
and a reported model matching the requested/fallback pair when identity exists. Accepted
notes come from final assistant text/payload text, not arbitrary tool
output. Missing notes or timeout retain observed evidence. See `_agent_review()`.

## 7. Changing The OpenClaw Review Model

Provider authentication is configured on Ubuntu, separately from the API ranker
and repository assistant. Project review requests explicitly select the model
using the variables above. Denver dates alternate Claude/GPT, anchored on
October 13, 2026 (Claude); October 14 is GPT, October 15 Claude. All requests
that day share the primary; the alternate is a fallback if enabled. Global
OpenClaw defaults and authentication are not rewritten. Cards/audits distinguish
requested model from reported actual model and label failures. See
[Options And Review Repair](OPTIONS_AND_REVIEW_REPAIR.md).

Check `agents.defaults.modelPolicy.allow` (and any agent-specific override) on
VM 105 as well as catalog/authentication status. October 13's Claude fixture
passed. The owner-approved GPT-6.1 Sol addition is now present, preserving the
existing seven entries, default model and browser configuration. Its first retry
failed with missing bearer authentication. The subsequently approved repair
added a GPT-only native ChatGPT transport overlay and replaced GPT's isolated
agent execution with the supported tool-free completion SDK. A tiny medium
fixture now succeeded with matching GPT identity and zero tool calls. See the
runbook for the exact overlay and verification boundaries. Cached sign-in
alone is insufficient. Do not clear a policy,
add a wildcard, expose tokens or substitute a new paid API key as a shortcut.
Rejections are labelled `model_policy_rejected` or `authentication_unavailable`;
public collection and independent analysis continue. Details and exact live
test boundaries are in the linked repair runbook.

Use the installed OpenClaw version's supported configuration tools;
do not guess a provider key or blindly overwrite its configuration file.

1. Confirm SSH, CLI path/version and public browser navigation separately.
2. Record the existing profile/provider selection securely. Preserve
   `canadian-stocks` and `windows` browser configurations and sign-ins.
3. With owner approval, verify the intended provider with a small public
   fixture: Claude uses native `agent exec`; GPT uses the public completion SDK.
4. Run the project's bounded review fixture and confirm zero tool calls,
   recognized symbols, parseable JSON and completion within the remaining budget.
5. Test public collection with review disabled to prove its independence.
6. Update only the intended provider selection, observe results, and preserve
   a rollback path. Do not switch API stock-ranking credentials inadvertently.

The Claude timeout fix sets `CLAUDE_CODE_SAFE_MODE=1` only for the review
subprocess and sends bounded excerpts. It retains existing sign-in but skips
unrelated native startup customization; it is not a global setting change.
The precise original customization causing the delay was not identified.

`scripts/openclaw_public_review.mjs` is streamed from this project to VM 105's
Node process. SDK exports resolve relative to the CLI realpath; there is no
separate remote helper to deploy. Both attempts share one deadline, with no
fallback launch if less than 15 seconds remains. GPT requests use 1,800 output
tokens maximum; truncated output is rejected, not converted into research notes.
The SDK uses existing OAuth internally; there are no project token variables.
Both tiny fixtures passed, but full six-name latency and live fallback remain
unverified. Offline bridge tests: `node --test scripts/test_openclaw_public_review.mjs`.

`PATH` includes the CLI's Node directory and `/usr/local/bin:/usr/bin:/bin`.
Remote GNU `timeout` and local subprocess limits bound the call. A Node/CLI
upgrade may invalidate `OPENCLAW_CLI_PATH`; update the path only after verifying
the installed executable. Do not extend timeouts indefinitely to hide a hang.
