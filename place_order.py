"""
place_order.py — DEPRECATED
============================

This script is deprecated as of strategy v3.1 (signals-only pivot).

The bot architecture is signals-only:
Claude analyzes and dispatches Telegram cards, the user executes manually
in IBKR mobile.

DO NOT CALL. If invoked, this script will refuse to run and send a Telegram
heads-up so the user knows something is misconfigured.
"""
from __future__ import annotations
import sys


_MSG = (
    "place_order.py is deprecated (signals-only architecture).\n"
    "Use signals.py + telegram_notify.py to dispatch a Telegram card instead.\n"
    "User executes manually in IBKR mobile.\n"
    "See docs/RESEARCH_WORKFLOW.md."
)


def main():
    print("ERROR: " + _MSG, file=sys.stderr)
    try:
        from telegram_notify import send_status
        send_status(
            "⚠️ Deprecated script invoked",
            "Something tried to call place_order.py — it has been deprecated. "
            "Check pending_tasks.md and the scheduler prompts; everything should "
            "go through signals.py / telegram_notify.py now.",
            icon="⚠️"
        )
    except Exception:
        pass
    sys.exit(1)


if __name__ == "__main__":
    main()
