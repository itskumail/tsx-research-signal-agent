# Claude Entry Point

Read [AGENTS.md](AGENTS.md) first. It contains model-neutral instructions for
this workspace; do not maintain a second, conflicting ruleset here.

Then read [the complete handoff](docs/HANDOFF.md). Its index covers architecture,
strategy, configuration, model changes, schedules, recovery, tests and operations.

The repository assistant, API research model and OpenClaw review model are
separate integrations. Switching one does not configure the other two.

Use `C:\Trading\.venv\Scripts\python.exe`, preserve the dirty worktree and never
expose credentials or broker-session details. Read Operations before running
commands that contact an account, send Telegram messages or spend API credit.
