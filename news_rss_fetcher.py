"""
news_rss_fetcher.py
-------------------
Supplemental news ingester via public RSS feeds (non-Telegram).

Why:
  Telegram "general news" channels are noisy and often miss Canadian
  company-specific press releases. RSS feeds from major press-release
  wires provide a cleaner stream of corporate catalysts.

What it does:
  - Fetch a small curated list of RSS feeds (best-effort)
  - For each new item since the last run, run the same ticker/alias matcher
    as Telegram ingestion (news_analyzer.match_message)
  - Append matched items into logs/news_events_<today>.jsonl in the same schema
    consumed by filters.news_sentiment()

State:
  logs/rss_last_check.json  — stores per-feed "last_seen" timestamp

Called by:
  session_runner.py at the top of every scheduled session.
"""

from __future__ import annotations

import json
import sys
import time
import email.utils
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import news_analyzer


# Force UTF-8 on stdout/stderr (Windows Task Scheduler safety)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


TRADING_DIR = Path(r"C:\Trading")
LOGS_DIR = TRADING_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

STATE_PATH = LOGS_DIR / "rss_last_check.json"


# Curated feed list (broad → the matcher filters down to your watchlist).
# Pattern discovered via GlobeNewswire RSS list.
FEEDS: list[dict[str, str]] = [
    {
        "name": "GlobeNewswire - M&A",
        "url": "https://www.globenewswire.com/RssFeed/subjectcode/27-Mergers%20And%20Acquisitions/feedTitle/GlobeNewswire%20-%20Mergers%20And%20Acquisitions",
    },
    {
        "name": "GlobeNewswire - Earnings",
        "url": "https://www.globenewswire.com/RssFeed/subjectcode/14-Earnings%20Releases%20And%20Operating%20Results/feedTitle/GlobeNewswire%20-%20Earnings%20Releases%20And%20Operating%20Results",
    },
    {
        "name": "GlobeNewswire - Company Announcement",
        "url": "https://www.globenewswire.com/RssFeed/subjectcode/16-Company%20Announcement/feedTitle/GlobeNewswire%20-%20Company%20Announcement",
    },
    {
        "name": "GlobeNewswire - Financing",
        "url": "https://www.globenewswire.com/RssFeed/subjectcode/50-Financing%20Agreements/feedTitle/GlobeNewswire%20-%20Financing%20Agreements",
    },
    {
        "name": "GlobeNewswire - Bankruptcy",
        "url": "https://www.globenewswire.com/RssFeed/subjectcode/5-Bankruptcy/feedTitle/GlobeNewswire%20-%20Bankruptcy",
    },
    # v5.1 — broader market coverage (free RSS, no auth)
    {
        "name": "GlobeNewswire - Dividend Reports",
        "url": "https://www.globenewswire.com/RssFeed/subjectcode/12-Dividend%20Reports%20And%20Estimates/feedTitle/GlobeNewswire%20-%20Dividend%20Reports%20And%20Estimates",
    },
    {
        "name": "GlobeNewswire - Contract Awards",
        "url": "https://www.globenewswire.com/RssFeed/subjectcode/8-Contract%20Awards/feedTitle/GlobeNewswire%20-%20Contract%20Awards",
    },
    {
        "name": "GlobeNewswire - New Products",
        "url": "https://www.globenewswire.com/RssFeed/subjectcode/35-New%20Products%20And%20Services/feedTitle/GlobeNewswire%20-%20New%20Products%20And%20Services",
    },
    {
        "name": "Yahoo Finance — Top headlines",
        "url": "https://finance.yahoo.com/news/rssindex",
    },
    {
        "name": "MarketWatch — Top stories",
        "url": "https://feeds.marketwatch.com/marketwatch/topstories/",
    },
    {
        "name": "Reuters — Business",
        "url": "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
    },
    {
        "name": "BNN Bloomberg — Top stories",
        "url": "https://www.bnnbloomberg.ca/news/feed/",
    },
    {
        "name": "Investing.com — Stock News",
        "url": "https://www.investing.com/rss/news_25.rss",
    },
    {
        "name": "Seeking Alpha — Market currents",
        "url": "https://seekingalpha.com/market_currents.xml",
    },
]

# Defensive cap per feed per run (avoid floods on first-ever run)
MAX_ITEMS_PER_FEED = 75


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _log_event(record: dict):
    today = datetime.now().strftime("%Y%m%d")
    path = LOGS_DIR / f"news_events_{today}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _parse_rfc822(dt_str: str) -> datetime | None:
    if not dt_str:
        return None
    try:
        t = email.utils.parsedate_to_datetime(dt_str)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.astimezone(timezone.utc)
    except Exception:
        return None


def _fetch_xml(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, TimeoutError, Exception):
        return None


def run_fetch() -> int:
    """
    Fetch all configured RSS feeds and append matched events.
    Returns count of NEW matched items written.
    """
    state = _load_state()
    total_matched = 0

    for feed in FEEDS:
        name = feed["name"]
        url = feed["url"]

        xml_text = _fetch_xml(url)
        if not xml_text:
            continue

        last_seen_iso = state.get(url)
        try:
            last_seen = datetime.fromisoformat(last_seen_iso) if last_seen_iso else None
        except Exception:
            last_seen = None

        try:
            root = ET.fromstring(xml_text)
        except Exception:
            continue

        # RSS 2.0: channel/item
        items = root.findall("./channel/item")
        if not items:
            # Some feeds use namespaces; fall back to scanning any "item"
            items = root.findall(".//item")

        newest = last_seen
        scanned = 0

        for item in items[:MAX_ITEMS_PER_FEED]:
            scanned += 1
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            guid = (item.findtext("guid") or "").strip()
            pub = _parse_rfc822((item.findtext("pubDate") or "").strip())

            # If pubDate missing, treat as "now" so we at least dedupe by last_seen when present
            pub = pub or datetime.now(timezone.utc)

            if last_seen and pub <= last_seen:
                continue

            text = (title + "\n" + desc).strip()
            hits = news_analyzer.match_message(text)
            if not hits:
                # Still advance newest so we don't re-scan old irrelevant items forever
                if newest is None or pub > newest:
                    newest = pub
                continue

            record = {
                "timestamp": pub.isoformat(),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source": name,
                "channel_id": None,
                "msg_id": guid or link or f"{name}:{int(pub.timestamp())}",
                "headline": title[:200] if title else "(no title)",
                "body": (desc or "")[:1500],
                "url": link,
                "tickers": [tk for tk, _ in hits],
                "match_types": dict(hits),
                "sentiment": news_analyzer.sentiment(text),
            }
            _log_event(record)
            total_matched += 1

            if newest is None or pub > newest:
                newest = pub

        if newest:
            state[url] = newest.isoformat()

        # Gentle pacing (avoid hammering feed hosts)
        time.sleep(0.25)

    _save_state(state)
    return total_matched


if __name__ == "__main__":
    n = run_fetch()
    print(f"[OK] RSS fetch wrote {n} matched event(s) to logs/news_events_<today>.jsonl")
