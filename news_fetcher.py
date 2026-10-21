"""
news_fetcher.py
---------------
Pull-on-schedule news ingester. Called at the top of every scheduled
session by session_runner.py — pulls all new messages from the user's 7
joined Telegram news channels since the last check, runs them through
the ticker/alias/sector matcher, and appends matches to
logs/news_events_<today>.jsonl. The existing scoring code already
consumes that file (filters.news_sentiment).

REPLACES the always-on daemon news_listener.py. No background process —
news is bundled with each session run for cleaner phone notifications
and lower system overhead.

USAGE:

  First-time auth (5-second interactive — needs to be at the PC once):
      python news_fetcher.py --auth

  One-shot manual fetch (test / fill in if a session was skipped):
      python news_fetcher.py

  As a library (called by session_runner):
      from news_fetcher import run_fetch
      n = run_fetch()  # returns count of new matched events

STATE:
  logs/news_last_check.json  — {<channel_id>: <last_msg_id>}
"""
from __future__ import annotations
import os
import sys
import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 stdio (Windows Task Scheduler runs under cp1252 by default)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from telethon import TelegramClient
except ImportError:
    print("ERROR: telethon missing. pip install telethon", file=sys.stderr)
    sys.exit(1)

import news_analyzer

TRADING_DIR = Path(r"C:\Trading")
LOGS_DIR    = TRADING_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

SESSION_NAME = "claude_news_session"
SESSION_PATH = TRADING_DIR / SESSION_NAME
STATE_PATH   = LOGS_DIR / "news_last_check.json"

# 7 confirmed channels — match by display name OR username substring
CHANNEL_PATTERNS = [
    "canada",                                 # Canada 🇨🇦 News & Life
    "canada_breaking_news_canadian_24",       # Canada Breaking News
    "worldnews",                              # World News [Breaking]
    "Al Jazeera",                             # Al Jazeera World News
    "Wall Street Journal",                    # WSJ World
    "Telegraph",                              # The Telegraph
    "BBC News",                               # BBC News (World)
]

# Per-fetch cap (defensive against floods on first run or after long downtime)
MAX_PER_CHANNEL_PER_FETCH = 200


# ---------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------
def _load_env() -> dict:
    env_path = TRADING_DIR / ".env"
    env = {}
    if not env_path.exists():
        return env
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


_env     = _load_env()
API_ID   = int(os.environ.get("TELEGRAM_API_ID")    or _env.get("TELEGRAM_API_ID")    or 0)
API_HASH = os.environ.get("TELEGRAM_API_HASH")     or _env.get("TELEGRAM_API_HASH")  or ""


# ---------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------
def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def log_event(record: dict):
    """Append one matched event to today's news_events_<date>.jsonl."""
    today = datetime.now().strftime("%Y%m%d")
    path  = LOGS_DIR / f"news_events_{today}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def fetcher_log(msg: str):
    path = LOGS_DIR / "news_fetcher.log"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")
    print(msg)


# ---------------------------------------------------------------
# Channel resolution
# ---------------------------------------------------------------
async def _resolve_channels(client: TelegramClient) -> list:
    """Walk dialogs and return entities matching the 7 channel patterns."""
    matched = []
    seen_names = set()
    async for dialog in client.iter_dialogs():
        if not dialog.is_channel:
            continue
        name = (dialog.name or "").lower()
        username = (getattr(dialog.entity, "username", None) or "").lower()
        for pat in CHANNEL_PATTERNS:
            p = pat.lower()
            if (p in name or p in username) and dialog.name not in seen_names:
                matched.append(dialog.entity)
                seen_names.add(dialog.name)
                break
    return matched


# ---------------------------------------------------------------
# Fetch + process one channel
# ---------------------------------------------------------------
async def _fetch_for_channel(client: TelegramClient, channel, last_id: int) -> tuple[list, int, int]:
    """
    Returns (matched_count, scanned_count, new_last_id).
    Walks new messages (id > last_id), runs matcher, appends matches to log.
    """
    matched = 0
    scanned = 0
    new_max = last_id
    source = getattr(channel, "title", None) or getattr(channel, "username", "unknown")

    # Telethon iter_messages with min_id returns msgs newer than that id, newest-first
    async for msg in client.iter_messages(channel, min_id=last_id,
                                           limit=MAX_PER_CHANNEL_PER_FETCH):
        scanned += 1
        if msg.id > new_max:
            new_max = msg.id
        text = (msg.message or "").strip()
        if not text:
            continue
        hits = news_analyzer.match_message(text)
        if not hits:
            continue
        sentiment = news_analyzer.sentiment(text)
        headline  = text.split("\n", 1)[0][:200]
        record = {
            "timestamp":   (msg.date or datetime.now(timezone.utc)).isoformat(),
            "fetched_at":  datetime.now(timezone.utc).isoformat(),
            "source":      source,
            "channel_id":  channel.id,
            "msg_id":      msg.id,
            "headline":    headline,
            "body":        text[:1500],
            "tickers":     [tk for tk, _ in hits],
            "match_types": dict(hits),
            "sentiment":   sentiment,
        }
        log_event(record)
        matched += 1

    return matched, scanned, new_max


# ---------------------------------------------------------------
# Main fetch (async core)
# ---------------------------------------------------------------
async def _run_fetch_async(initialize_only: bool = False) -> int:
    if not API_ID or not API_HASH:
        fetcher_log("ERROR: TELEGRAM_API_ID / TELEGRAM_API_HASH missing from .env")
        return 0
    if not (TRADING_DIR / f"{SESSION_NAME}.session").exists():
        fetcher_log("ERROR: no Telegram session. Run: python news_fetcher.py --auth")
        return 0

    client = TelegramClient(str(SESSION_PATH), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        fetcher_log("ERROR: session expired. Re-run: python news_fetcher.py --auth")
        await client.disconnect()
        return 0

    try:
        channels = await _resolve_channels(client)
        if not channels:
            fetcher_log("WARNING: no matching channels found.")
            return 0
        fetcher_log(
            "Channels resolved: "
            + ", ".join(getattr(ch, "title", None) or getattr(ch, "username", "unknown") for ch in channels)
        )

        state = load_state()
        total_matched = 0
        total_scanned = 0

        for ch in channels:
            cid = str(ch.id)
            last_id = state.get(cid, 0)

            if last_id == 0 or initialize_only:
                # First time we see this channel — record latest msg id, do NOT process history
                async for msg in client.iter_messages(ch, limit=1):
                    state[cid] = msg.id
                    fetcher_log(f"  init {ch.title}: starting from msg {msg.id}")
                    break
                continue

            matched, scanned, new_max = await _fetch_for_channel(client, ch, last_id)
            state[cid] = new_max
            total_matched += matched
            total_scanned += scanned
            if scanned > 0:
                fetcher_log(f"  {ch.title}: scanned {scanned}, matched {matched}")

        save_state(state)
        fetcher_log(f"Fetch complete. {total_scanned} messages scanned, {total_matched} matched watchlist.")
        return total_matched

    finally:
        await client.disconnect()


# ---------------------------------------------------------------
# Synchronous entry-points
# ---------------------------------------------------------------
def run_fetch(initialize_only: bool = False) -> int:
    """
    Synchronous wrapper for use by session_runner. Returns the count of
    matched events. Catches all exceptions so a news-fetch failure can
    never crash the calling session.

    After execution, installs a fresh event loop in the main thread so
    subsequent ib_insync (Gateway) connections work — asyncio.run()
    closes its loop on exit, leaving no current loop in Python 3.12,
    which breaks ib_insync.connect().
    """
    try:
        return asyncio.run(_run_fetch_async(initialize_only=initialize_only))
    except Exception as e:
        fetcher_log(f"ERROR in run_fetch: {e}")
        return 0
    finally:
        # Reinstall a usable event loop for downstream code (ib_insync etc.)
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
        except Exception:
            pass


def run_auth():
    """Interactive first-auth: connect and log in, then exit."""
    if not API_ID or not API_HASH:
        print("ERROR: TELEGRAM_API_ID / TELEGRAM_API_HASH missing from .env")
        sys.exit(1)
    print("First-time Telegram authentication.")
    print("Telegram will SMS a code to your phone — enter it when prompted.")
    print()
    client = TelegramClient(str(SESSION_PATH), API_ID, API_HASH)
    client.start()
    print("\n[OK] Authenticated. Session saved to:", SESSION_PATH)
    print("Initializing channel state (no history will be processed)...")
    client.disconnect()
    # After auth, do a one-time init pass to record current latest msg ids
    n = run_fetch(initialize_only=True)
    print(f"[OK] Channel state initialized. From now on, each session pulls only new messages.")


# ---------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------
if __name__ == "__main__":
    if "--auth" in sys.argv:
        run_auth()
    elif "--reset" in sys.argv:
        # Clear state to re-initialize on next run
        if STATE_PATH.exists():
            STATE_PATH.unlink()
        print("[OK] Cleared news_last_check.json. Next run will re-init from current latest msgs.")
    else:
        n = run_fetch()
        print(f"[OK] {n} new matched events written to logs/news_events_<today>.jsonl")
