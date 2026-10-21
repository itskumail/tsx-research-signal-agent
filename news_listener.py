"""
news_listener.py — RETIRED (v4.3)
=================================

This 24/7 daemon was replaced by the pull-on-schedule fetcher
(`news_fetcher.py`) called at the top of every scheduled session by
session_runner.py.

Reason for the change: news + signal cards now arrive bundled at session
times instead of as separate real-time pings, which means cleaner phone
notifications, no always-on background process to babysit, and decisions
are made WITH the news already factored in.

USAGE NOW:
    # First-time auth (5 sec interactive)
    python news_fetcher.py --auth

    # Subsequent: each scheduled session pulls news automatically.

If you really want the old real-time daemon back, the original code is
preserved in git history (see commits prior to v4.3).
"""
import sys

print(__doc__)
print("This script is retired. Use news_fetcher.py instead.")
sys.exit(0)
