# IBKR Paper Browser And Optional Gateway Reference

Baseline: 2026-10-09. Read [AGENTS.md](AGENTS.md) and
[the handoff](docs/HANDOFF.md) first. This is the current setup, not the original
execution prototype. The active workflow researches and sends Telegram alerts.

## 1. Present Browser Use

Dedicated Windows shortcut: `Canadian Stock Chrome Browser`.
User-data directory: `C:\Users\Trader\AppData\Local\CanadianStockChrome\Profile`.
OpenClaw profile: `canadian-stocks`; native loopback 9224, scoped bridge .15:9225.
The original Claw browser is separate and unchanged.

The owner initially fills/saves the Paper login in this profile. Since October 16,
the owner-enabled helper may select Paper and submit only the already-filled
recognized SSO form. Field-presence booleans, not credential values, leave the
page. The helper never retrieves stored passwords, opens password managers,
types credentials or accesses cookies/storage/tokens. See
[Saved-form Paper sign-in](docs/PAPER_SAVED_LOGIN.md). It verifies a simulated-account banner before portfolio
navigation. A live/unverified page is not navigated. Do not publish account IDs,
balances or authenticated broker URLs in documentation.

An ordinary portal entry URL is used when recovery reopens Chrome. Authentication
may survive through existing session state, recover through the enabled guarded
saved-form flow, or require owner attention for missing fields/challenges.
Public research can proceed in every case. Read
[Operations](docs/OPERATIONS.md) for status/recover/keepalive/cleanup commands.

## 2. Session And Data Limits

The owner selected a 60-minute browser idle timeout. The existing 40-minute
check performs read-only activity only in verified Paper, including after market
close. With the owner's enabled opt-in it can also make one guarded disconnected-
Paper banner reconnect attempt; saved-form SSO recovery is a separate opt-in. See
[Paper reconnect](docs/PAPER_RECONNECT.md) and the saved-form guide above.
Neither is a guarantee against server expiry, maintenance or connectivity
loss. If automatic recovery cannot restore the session, Telegram requests owner
attention once per meaningful state change. It does not repeatedly retry authentication.

Gateway and web login can conflict over a session. Current default leaves
Gateway disabled. Paper account data may be delayed/disconnected; browser-open
or login alone is not proof of live subscriptions/data entitlement. The owner
accepts labelled delayed research, but stale quote timestamps still fail native
option validation.

Scheduled real-account holdings extraction from the Paper browser is not
implemented. Session 2 does use owner-approved GET-only IBKR Paper CDE option
snapshots, with validated MX visible tables as a bounded fallback. Both sources
must pass native contract identity, timing and quote-quality gates. Paper
holdings must never be represented as real-account holdings.

## 3. Optional Gateway Tools

Gateway is unnecessary for the default independent workflow. If explicitly
using legacy account reads, verify its actual install, account/session and socket
port. Project default is `127.0.0.1:7497`, client ID 1; this is a local configuration,
not a universal product default. Keep existing read-only/localhost access.

| Tool | Purpose/caveat |
|---|---|
| `check_gateway.py` | Three-second socket probe; does not verify authentication |
| `get_portfolio.py`, `get_snapshot.py` | Private account reads, require connected Gateway |
| `get_quotes.py`, `get_history.py` | Manual Gateway quotes/bars, not runner fallback |
| `get_options.py`, `get_orders.py` | Manual account/contract/order reads |
| `ibkr_client.py` | Legacy wrapper; retained order helpers are not scheduled |
| `place_order.py` | Deprecated error/notifying stub, not an execution tool |

No order-testing command belongs in the default setup checklist. Manual account
reads can print sensitive information; inspect locally and redact before sharing.
Do not enable write access or change subscriptions to make a read test pass.

## 4. Watchlist And Setup Verification

`watchlist.txt` is manually maintained, not automatically downloaded from an
IBKR named watchlist. It contains 75 entries at handoff; `.TO`/`.V`/class-share
normalization is centralized in `yf_symbols.py`. Verify issuer/exchange identity
when adding a symbol; an ambiguous bare-symbol fallback is not confirmation.

Safe baseline uses the named offline regression suite, dependency check and
passive browser status. Paper navigation with `--no-notify` is a separate scoped
live check; it may perform enabled guarded banner reconnect and saved-form
Paper sign-in, but never credential retrieval/entry or challenges. Do not force
logout or reset attempt blocks as a test. Full deployment, schedule and backup
instructions are in [Operations](docs/OPERATIONS.md) and
[VM Operations](docs/VM_OPERATIONS.md).
