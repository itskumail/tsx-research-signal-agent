# Options Balance And Public Review

Implemented October 13, 2026 after the owner reported CALL-only alerts and
missing alternate-model browser reasoning. This changes selection/reporting,
not the native quote or underlying directional thresholds.

Later source-collection and authentication changes are documented in
[October 16 review repairs](REVIEW_REPAIRS_2026-10-16.md) and
[Saved-form Paper sign-in](PAPER_SAVED_LOGIN.md). Use
[System overview](SYSTEM_OVERVIEW.md) and [Handoff](HANDOFF.md) for the current
combined workflow and 220 Python / 41 Node verification baseline. Dated test
counts below remain evidence of their original repair stage, not current totals.

October 14 coverage/activity follow-up and the self-contained reviewer handoff:
[Independent Review Packet](EXTERNAL_REVIEW_2026-10-14.md). That packet records
the two-CALL/no-PUT incident, incomplete triage, bounded replacement collection,
contract-market liquidity versus account funds, corrected momentum windows,
and verified non-no-op Paper navigation. It separates live evidence from tests
and remaining unverified behavior.

## What Happened

The Session 2 audit contains three delivered CALLs (CNQ, SU, MFC), zero PUTs.
The old dispatcher reused empty PUT slots for more CALLs. That was a soft
two-per-side target, not the strict balance the owner expected.

The same run returned only 25 DeepSeek triage verdicts; two batches failed to
produce parseable final JSON. The API adapter previously substituted reasoning
text when final content was empty. Those missing verdicts were not evidence of
neutral/bullish direction. The later Session 3 pass returned 71 verdicts but
still no high-conviction SHORT list. Neither finding proves there were no
Canadian PUT opportunities: options eligibility was restricted to 20 US-dual-
listed names and the browser checked only six predominantly LONG names.

Public browser collection did run: timestamped MX CALL and PUT contract rows
were recorded for several names, with Barchart secondary pages. The presence
of listed PUTs is not a bearish underlying signal. OpenClaw reasoning did not
complete: Session 2 logged `command_failed`, Session 3 `request_timeout`. The
cards showed DeepSeek analysis without an accepted supplemental review. Daily
Claude/ChatGPT switching was not implemented in that earlier path.

Today's historical alerts/audits are preserved, including the third CALL.
They cannot be retrospectively turned into a two-CALL/two-PUT day.

## Current Options Rules

- Daily caps are two successfully delivered CALLs and two successfully delivered
  PUTs, four combined. Unused slots never transfer between sides.
- Two each is a qualified-opportunity goal, not permission to invent or force
  contracts. Direction, earnings, tradability, market window and timestamped
  native CAD/CDE quote gates remain. Missing/stale quotes do not become prices.
- Native Canadian options no longer require a US dual listing. US mapping is
  retained only for the legacy non-CAD path/context. A name with no verified
  native contract can only produce an explicitly unpriced underlying idea.
- Session 2 previews strict option direction over already-scored daily bars to
  allocate browser pages. The preview performs no earnings/US-chain network IO
  and can never approve a send. Final scoring still checks earnings and contracts.
- Ranked LONGs and strict bearish/AI SHORT candidates share the six-primary-name public
  page budget, with up to three per side when both lists are sufficient. An
  incomplete/empty AI SHORT list does not exclude strict technical PUT candidates.
  Since October 14, a missing side may receive further replacement attempts up
  to twice the primary name limit, under the same browser lock/deadline and
  review reserve. This is bounded coverage, not an exhaustive market search.
- Final options scans reuse the completed daily-bar cache; they do not refetch
  every stock merely to remove the old US-dual-list gate.
- `Options coverage` reports daily CALL/PUT delivery counts, observed page
  coverage and any shortfall. Unchecked names are not described as absent setups.
  `logs/options_coverage_<YYYYMMDD>_<session>.json` stores those safe counts.
  Failed sends are not counted. Existing delivered-alert logs are never cleared.

## Browser Collection And Reasoning

`CanadianResearchBrowser.run_research()` still holds the dedicated browser lock
and performs cleanup in `finally`. The model receives only bounded public page
excerpts/representative rows, not broker pages, account details or browser tools.
This is reasoning over observed browser evidence; DeepSeek ranking stays separate.

MX pages across selected names are prioritized before secondary Barchart pages,
within the collection deadline. With the
default 300-second total budget, 110 seconds is reserved for the 90-second
review plus its process overhead. Secondary pages cannot consume that reserve.
Limited budgets or failed sources are explicitly recorded, not silently called
completed research. Cleanup has its own existing bounded attempt; its completion
report is retained in the evidence. No browser/profile/network change was made.

Each symbol records `ai_review_meta`: requested model, alternate fallback,
Denver rotation date, requested effort, completion status/reason, and actual
model when reported and recognized. Accepted notes require an explicit zero-
tool-call summary. Unknown/failed/tool-using responses do not create source
facts. A reported model outside the requested/fallback pair is rejected.

Stock evidence labels and option cards show review completion/model/effort or
failure, alongside source/time labels. Accepted summaries/concerns are HTML
escaped. No model-supplied contracts or numbers are merged into quote evidence.

## Daily Model Route

Defaults, verified in the installed VM 105 catalog on October 13:

| Trading public review | Exact requested model | Effort |
|---|---|---|
| October 13, then every other calendar day | `anthropic/claude-opus-5-5` | medium |
| October 14, then every other calendar day | `openai/gpt-6.1-sol` | medium |

The Denver calendar date determines the route. Every review on the same day
uses the same primary; restarts do not flip it. Weekends advance the calendar
but do not create extra research runs. There is no model call in the 40-minute
session/analysis health monitor.

`scripts/openclaw_public_review.mjs` scopes selection to this request. It runs
Claude through native `agent exec` and ChatGPT through OpenClaw's public
credential-aware simple-completion SDK, with no tools. The other provider is
the optional fallback within the same bounded request, not a second mandatory
opinion on every candidate. Fallback starts only after failure with at least
15 seconds left; both attempts share the original deadline. Disable
fallback with `OPENCLAW_REVIEW_FALLBACK=false`. A fallback can still consume the
alternate allowance; daily alternation is not an unlimited-usage guarantee.

Only `medium` and `high` are accepted by this adapter. Extra-high, max, adaptive,
ultra and low are rejected for this review. Model IDs can be changed through
the documented preference variables after verifying supported IDs/auth in the
installed client. Do not assume an account can use every catalog entry.

The general OpenClaw default remains `anthropic/claude-opus-5` with its existing
global configuration. DeepSeek API provider/model/key, native sign-ins and
managed permission policy were not changed. Per-request trading review is a
separate integration. References: [OpenClaw agent exec](https://docs.openclaw.ai/cli/agent),
[thinking levels](https://docs.openclaw.ai/tools/thinking).

## DeepSeek Output And Coverage

Final `message.content` is the answer channel. Empty final content now fails
soft with a safe reason instead of substituting `reasoning_content` into JSON
ranking/approvals. The existing token cap, model, provider and credentials are
unchanged. Failed/partial triage records missing symbols and completeness.
Model-created symbols outside the submitted batch are ignored. No replacement
direction is invented for missing verdicts; technical evidence remains separate.

Provider output limits and reasoning controls depend on the model/version;
see [DeepSeek API reference](https://api-docs.deepseek.com/api/create-chat-completion/).
Further token-budget/model changes require their own verification; this repair
does not claim all future DeepSeek batches will complete.

## Verification

The October 13 baseline had 148 Python and seven Node tests. The October 14
expanded suite has 162 Python and seven Node offline mocked tests covering
triage repair/lookbacks, replacement coverage, verified monitor activity and
the existing side caps/prior sends, native symbols
without US dual listings, final earnings guards, non-sendable preview, cached
history, actual delivery counts, daily route/timezone stability, reserved review
time, MX-before-secondary priority, model/tool identity and missing final JSON.
The installed CLI supports explicit model/fallback/thinking flags and lists
both requested models available. Catalog visibility and cached authentication
health do not establish permission under the agent's model policy.

Owner-approved live public-evidence fixture tests on October 13, 2026 used
medium effort, a 90-second request timeout and no fallback. They performed no
browser navigation, broker action, Telegram send or full trading session:

| Request start (UTC) | Requested model | Result | Elapsed |
|---|---|---|---|
| 20:31:11 | `anthropic/claude-opus-5-5` | Accepted recognized note, matching actual model, explicit zero tool calls | 53.0 seconds |
| 20:32:04 | `openai/gpt-6.1-sol` | Agent model policy rejected selection before a usable review | 45.1 seconds |
| 21:04:35 | `openai/gpt-6.1-sol` | After approved allowlist addition, Responses request rejected with missing bearer authentication | 69.3 seconds |

The exact rejection was independently confirmed in the VM 105 log. Cached
OpenAI OAuth health was `ok`; the allowlist
`agents.defaults.modelPolicy.allow` included `openai/gpt-6-astra` and
`openai/gpt-5.6-sol`, but not `openai/gpt-6.1-sol`. The adapter now labels this
`model_policy_rejected` without printing the provider error. No policy was
changed by the initial diagnostic test. The owner subsequently approved adding
only `openai/gpt-6.1-sol` and one retry. VM 105's supported CLI appended it to
the seven-entry list; all seven existing entries, the default model and browser
configuration were verified unchanged. No wildcard, authentication setting,
credential, sign-in or browser permission was changed.

The appended index was guarded with `--expect-current-absent`. In this installed
CLI, that conditional-write flag cannot be combined with `--dry-run`; validation
and the guarded write ran separately. Before migration, read the current list
and use its current length, not the historical index 7. Do not replay this
completed addition or remove/empty the allowlist.

The single approved GPT retry passed model selection but failed with the
Responses API reporting missing bearer authentication. The CLI returned
`error.kind=exception`, no accepted notes and no verified actual model. The
adapter now labels this `authentication_unavailable`, never raw error text.
Healthy cached OAuth metadata did not establish a successful authenticated
request. The exact component omitting the header remains unconfirmed; do not
infer subscription expiry, quota exhaustion or a need to log into IBKR.

### Approved ChatGPT Route Repair

The owner subsequently approved request-path repair and one additional tiny
GPT test, but not re-login, credential replacement or a paid API key. On VM 105,
OpenClaw 2026.9.9 separates native ChatGPT OAuth and API-key model transports.
A supported, single-model merge overlay was added using CLI validation and a
guarded write, preserving defaults, allowlist, browser settings and credentials:

```json
{"models":{"providers":{"openai":{"models":[{
  "id":"gpt-6.1-sol","name":"GPT-6.1 Sol",
  "api":"openai-chatgpt-responses",
  "baseUrl":"https://chatgpt.com/backend-api/codex","reasoning":true
}]}}}}
```

This is the installed client's native OAuth transport, not a new paid API key
or the separate OpenAI token-sharing sign-in flow. OpenClaw supplies normalized
model metadata automatically. Provider-level `baseUrl` is empty/implicit; the
model-level route above is explicit. Do not overwrite other provider records
or copy credentials into this project. The other configured OpenAI models
remained available; the general default remained Claude Opus 5 with no global
fallbacks.

The isolated GPT agent runner still rejected route-compatible authentication
before inference. Normal local-agent and gateway probes also failed before a
usable response, so neither is used as a workaround. The public SDK's
`prepareSimpleCompletionModelForAgent` successfully prepared the existing OAuth;
`completeWithPreparedSimpleCompletionModel` then returned HTTP 200, matching
`openai/gpt-6.1-sol`, one recognized fixture note, normal completion and zero
tool calls at medium effort. Preparation/completion took 20.2 seconds excluding
initial SDK imports and SSH startup. No browser, broker, Telegram or full
trading session ran. Cached sign-in alone was not treated as verification.

The project now streams its versioned bridge code to Node on VM 105 via SSH;
no extra VM-side helper installation or gateway restart is required. It resolves
only public SDK exports relative to the configured CLI's real package path.
The SDK owns credential resolution/refresh internally; the bridge never reads
an auth store, prints tokens, or substitutes an API key. It requires explicit
live allowlist membership before preparation and checks selected/reported model
identity. The completion receives only the bounded public prompt and `tools: []`.
Truncated/error responses fail closed. Claude retains its verified native
runner with scoped safe-mode startup. Safe errors and attempted-model metadata
cross SSH; library stdout is suppressed so it cannot corrupt the JSON envelope.

`scripts/openclaw_completion_probe.mjs --prepare-only` is a no-inference
maintenance preflight; running it without that flag is a live GPT fixture and
requires owner approval. `scripts/test_openclaw_public_review.mjs` uses mocked
SDK/CLI transports, not the owner's accounts. Run it with `node --test` locally.

Both providers now have a successful tiny no-fallback fixture. A full six-name
live review and cross-provider live fallback remain untested; the fallback
is not guaranteed recovery. Independent analysis and collection still continue
when optional review fails. These fixtures do not establish future allowance,
quote freshness or session continuity. After an OpenClaw upgrade, revalidate
public SDK availability and native OAuth transport without guessing new auth
fields. Re-login or credential replacement still requires owner approval.

The repaired 12:30 analysis and 13:55 EOD schedules completed automatically with
exit 0 on October 13. That verifies the separate scheduler repair, not live
success of these newly routed review models. No extra stock/options session or
Telegram test was launched for this repair.
