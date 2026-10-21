# Future Paper Workflow Planning

Baseline: 2026-10-09. This historical filename is retained for existing links.
It is a planning note, not an implemented mode-switch checklist.

Current functionality is research, read-only verified Paper navigation and
Telegram notifications. There is no active order execution, automatic broker
login or implemented `TRADING_MODE=auto/paper` branch. `REQUIRE_GATEWAY` is only
a legacy analysis-dependency gate. Deleting a deprecated script guard does not
produce a complete trading workflow.

## Separate Future Scope

The owner has mentioned a possible later IBKR Paper browser workflow. Before
implementation, define the precise interface, account identity, order semantics,
inputs, user controls, audit trail and failure handling. Keep current research
and notification jobs independent during that work.

Design topics, not existing features:

- Reliable Paper account identity verification before each account action.
- Separating research data from order-entry values; stale/delayed source labels.
- Confirmed contract identity, currency, exchange, expiry and multiplier.
- Preventing duplicate actions after timeout or unknown submission status.
- Position/order/fill reconciliation and distinction between unknown and zero.
- Explicit sizing/risk rules and owner-operated stop/disable controls.
- Session expiry, connectivity loss and manual login recovery.
- Isolated fixtures, simulation tests, paper observation and rollback.

Any new integration must be documented in the model-neutral handoff with actual
implemented flags, modules, tests and activation steps. Do not present proposed
scripts, configuration names or real-account deployment as already available.
See [Handoff](HANDOFF.md), [Architecture](../ARCHITECTURE.md),
[Configuration](CONFIGURATION.md) and [Operations](OPERATIONS.md).
