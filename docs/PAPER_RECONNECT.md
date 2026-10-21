# Guarded IBKR Paper Reconnect

Implemented and owner-authorized on 2026-10-11. This is a narrow exception to
the earlier manual-login-only policy, not full login automation. It belongs to
`CanadianResearchBrowser.keepalive()` in `browser_research.py`. The existing
40-minute monitor invokes it; no extra schedule or research model is needed.

## Session States

- `login_required`: IBKR's authentication page is open. Full login remains
  manual by default. The later October 16 opt-in
  [saved-form extension](PAPER_SAVED_LOGIN.md) permits guarded Paper selection
  and submission only when Chrome has already filled both fields. Credential
  entry/retrieval and two-factor approval remain manual.
- `paper_disconnected`: the existing Paper portal is accessible, but its warning
  says it is disconnected from the brokerage system. The owner authorized one
  guarded click on that warning's `Log In` control, using IBKR's retained session.

This click changes brokerage-session state; it is not just a refresh. IBKR
describes separate read-only and brokerage sessions in its
[official session documentation](https://www.interactivebrokers.com/docs/web-api/authentication/sessions).
A username can have only one brokerage session. The banner cannot rule out
another platform using it: reconnecting could move the connection away from
that platform. Keep Gateway off in this workflow. The helper does not approve
explicit competing-session prompts.

## Configuration

`OPENCLAW_PAPER_RECONNECT` defaults false; the credential-free template leaves
it false. Production `.env` was explicitly set true following the owner's
October 11 request and successful live guarded reconnect test.

Set it false to disable reconnects without disabling ordinary Paper navigation,
public research or independent analysis. It is separate from
`OPENCLAW_KEEPALIVE_ENABLED` and `OPENCLAW_BROWSER_AUTORESTART`.
Never print private configuration or replace it with the example. A fresh
helper process loads the new setting.

## Guards And Attempt Limit

1. Hold the existing shared browser OS lock and verify dedicated control.
2. Only `paper_disconnected`, with the flag true, enters reconnect. Healthy,
   missing, live/unverified and SSO pages do not.
3. Inspect saved attempt state. A blocked, malformed or unavailable state must
   not authorize another click.
4. Require IBKR host, `/portal` path, Paper/simulated banner, disconnected-brokerage
   warning and without-Trading/Market-Data warning. Exactly one visible enabled
   `Log In` control must belong to a bounded ancestor of that warning.
5. Reject password forms, two-factor/CAPTCHA indicators, active nonempty dialogs
   and explicit competing-session prompts. Never read input values, cookies,
   storage, credentials or tokens. Empty inert dialog containers are ignored;
   these exist in the observed IBKR page.
6. Persist `blocked=true` BEFORE clicking. A write failure prevents the click.
   Immediately recheck the same DOM guards inside the click operation.
7. Click once with a non-retried command. A crash, timeout or uncertain result
   cannot cause another process to repeat that click in the same episode.
8. Re-observe within a maximum 60-second confirmation budget and the existing
   total 240-second helper budget. At most three observations, no second click.
   Stop on SSO, unverified pages, challenges, active dialogs or competition prompts.
9. Verified `ready` clears the block and allows portfolio navigation. Failed or
   uncertain attempts stay blocked. A later keepalive observation of healthy
   Paper after owner intervention can clear it. Do not erase/reset state to retry.

`logs/browser_reconnect_state.json` contains only `blocked` and UTC `checked_at`.
Preserve it with health/recovery state across client/scheduler migrations. A
successfully resolved episode can reconnect after a later disconnection; an
unresolved episode does not receive a click every 40 minutes.

Safe output can include `reconnect_attempted` and `reconnect_succeeded` booleans,
alongside normal status/reason/failed-step diagnostics:

| Reason | Meaning |
|---|---|
| `reconnect_attempt_exhausted` | Previous failed/uncertain attempt remains blocked |
| `reconnect_state_unavailable` | State cannot safely authorize an attempt |
| `reconnect_authentication_required` | Credentials/challenge need owner action |
| `reconnect_dialog_requires_attention` | Active dialog is not accepted |
| `reconnect_competing_session` | Explicit competition prompt is not accepted |
| `reconnect_control_not_unique` | Cannot identify one eligible banner control |
| `reconnect_warning_missing` | Expected disconnected warning no longer observed |
| `reconnect_not_restored` | Single click did not establish verified healthy Paper |

The banner-reconnect flow does not perform credential login or Paper/Live switching.
The separate owner-enabled October 16 saved-form flow is documented above; it
never selects Live, retrieves/types credentials, opens password managers, places
orders or changes transfers/settings/subscriptions. This does not prove live data
entitlement. Connected delayed quotes and an unverified disconnected feed are
different, not interchangeable.

## Verification And Limits

On October 11 the live test observed `paper_disconnected`, passed the guards,
clicked once and returned `ready` with both reconnect flags true. No credentials,
two-factor or competing-session confirmation was operated. No order or Telegram
test was sent. The existing monitor prompt was updated in place and production
flag enabled, preserving its cadence, destination and active status.
The full `keepalive --no-notify` path subsequently returned `ready`, without
another reconnect click or Telegram send.

107 offline tests passed: opt-out, persistent attempt limit, no uncertain mutation
retry, guarded failures, healthy recovery, SSO/unverified stops and the existing
navigation/cleanup paths. The positive DOM guard was observed live before clicking.
This does not establish weeks of unattended sessions or every future page layout.
The Sunday test did not compare stock quote prices or establish real-time data.

Public research and independent analysis continue when reconnect needs owner
attention. See [Operations](OPERATIONS.md) and
[Monitor migration](BROWSER_MONITOR_MIGRATION.md) for scheduler continuity.

Since October 15 activity is two real OpenClaw mouse clicks (`Positions`, then
back to `Dashboard`), each confirmed active, with safe activity history. Reconnect guards, opt-in and persistent attempt block remain unchanged.
The 40-minute monitor can run successfully while a server-side full login still
expires. The owner previously reported a 60-minute idle setting; its present
persistence was not inspected/changed. [IBKR's inactivity guide](https://www.ibkrguides.com/orgportal/gettingstarted/log-out-after-inactivity.htm)
lists 15/30/45/60-minute settings. Do not assume 40 minutes is sufficient if the
portal has reverted to 15 minutes; do not automate account-setting changes.
