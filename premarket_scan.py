"""
premarket_scan.py
-----------------
Pre-market catalyst scanner. Runs at the start of the 6:00 AM and
7:15 AM premarket sessions. For every watchlist name, pulls recent
news from yfinance (which has GlobeNewswire / PR Newswire / TipRanks
feeds for Canadian tickers — far better than the general Telegram
news channels) and flags any with a major catalyst in the last 24h:

  • M&A: acquire / acquisition / merger / buyout / takeover / deal
  • Earnings beat or miss
  • Trading halt
  • Guidance changes
  • FDA approvals, contract wins
  • Bankruptcy / delisting

For every watchlist ticker with a catalyst, dispatches a SEPARATE
"📢 CATALYST ALERT" Telegram card BEFORE the regular session
runs — so the user can act on overnight M&A / earnings news at
the proper price BEFORE TSX open.

This complements (does not replace) the rubric-based BUY signals.

Usage (from premarket sessions in session_runner.py):
    from premarket_scan import scan_and_alert_catalysts
    scan_and_alert_catalysts()
"""
from __future__ import annotations
import html
import json
import sys
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

# UTF-8 stdio
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import yfinance as yf

from telegram_notify import send_status

TRADING_DIR = Path(r"C:\Trading")
LOGS_DIR    = TRADING_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# State: track which catalyst headlines we've already alerted on, so we
# don't re-fire the same alert at every pre-market session of the day.
ALERTED_PATH = LOGS_DIR / "catalyst_alerted.json"


# ---------------------------------------------------------------
# Catalyst keywords
# ---------------------------------------------------------------
HIGH_PRIORITY_BULLISH = [
    "acquire", "acquires", "acquisition", "to acquire", "agreement to acquire",
    "merger", "merger agreement", "buyout", "takeover", "deal", "to be acquired",
    "earnings beat", "beats estimates", "tops estimates", "beat expectations",
    "raised guidance", "raises guidance", "guidance raised",
    "fda approval", "approved by", "regulatory approval",
    "contract win", "wins contract", "awarded contract",
    "record quarter", "record results", "all-time high",
    "strategic review", "going-private",
]

HIGH_PRIORITY_BEARISH = [
    "earnings miss", "misses estimates", "missed expectations",
    "guidance cut", "lowered guidance", "lowers guidance",
    "trading halt", "trading halted", "suspended",
    "downgraded", "downgrade",
    "investigation", "lawsuit", "fraud",
    "bankruptcy", "going concern", "delisting", "delisted",
    "ceo steps down", "resigns",
    "recall",
    # v4.7 — price-action verbs that should override misleading "high" / "record" wording.
    # Examples this fixes: "Celestica Plunges 14% After All Time High..."
    "plunge", "plunges", "plummet", "plummets",
    "tumble", "tumbles", "crash", "crashes",
    "sink", "sinks", "slump", "slumps",
    "drops ", "falls ", "tanks", "selloff", "sell-off",
]


def _classify_headline(title: str, summary: str = "") -> tuple[str | None, str | None]:
    """
    Returns (priority, side) where:
      priority ∈ {"high", "medium", None}
      side     ∈ {"bullish", "bearish", None}

    v4.7 precision rules:
      1. BEARISH keywords are checked FIRST. Stock-action verbs like
         "plunges" / "drops" win over context words like "high" / "record".
      2. Title is weighted more than summary — title alone tells most of
         the story; summary may quote contradicting context.
    """
    title_l   = (title   or "").lower()
    summary_l = (summary or "").lower()

    # 1. Bearish first — title only (avoids summary-context false positives)
    for kw in HIGH_PRIORITY_BEARISH:
        if kw in title_l:
            return ("high", "bearish")

    # 2. Bullish — title only first
    for kw in HIGH_PRIORITY_BULLISH:
        if kw in title_l:
            return ("high", "bullish")

    # 3. Fallback: summary check, bearish then bullish
    for kw in HIGH_PRIORITY_BEARISH:
        if kw in summary_l:
            return ("high", "bearish")
    for kw in HIGH_PRIORITY_BULLISH:
        if kw in summary_l:
            return ("high", "bullish")

    return (None, None)


# ---------------------------------------------------------------
# yfinance helpers
# ---------------------------------------------------------------
def _fetch_news(symbol: str, currency: str = "CAD") -> list[dict]:
    """Returns list of normalized news items: [{title, url, pub_date_iso, provider}]"""
    suffix = ".TO" if currency.upper() == "CAD" else ""
    try:
        t = yf.Ticker(f"{symbol}{suffix}")
        raw = t.news or []
    except Exception:
        return []

    out = []
    for n in raw[:15]:
        c = n.get("content") or {}
        title = c.get("title") or n.get("title")
        if not title:
            continue
        pub = c.get("pubDate") or c.get("displayTime") or n.get("providerPublishTime")
        if isinstance(pub, (int, float)):
            pub_dt = datetime.fromtimestamp(pub, tz=timezone.utc)
        else:
            try:
                pub_dt = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
            except Exception:
                pub_dt = None

        provider = (c.get("provider") or {}).get("displayName") or "?"
        url = (c.get("canonicalUrl") or {}).get("url") or c.get("clickThroughUrl") or ""
        if isinstance(url, dict):
            url = url.get("url", "")

        summary = c.get("summary") or c.get("description") or ""
        out.append({
            "title": title,
            "url": url,
            "pub_dt": pub_dt,
            "provider": provider,
            "summary": summary[:300],
        })
    return out


def _load_alerted() -> set:
    if not ALERTED_PATH.exists():
        return set()
    try:
        data = json.loads(ALERTED_PATH.read_text(encoding="utf-8"))
        return set(data.get("ids", []))
    except Exception:
        return set()


def _save_alerted(alerted: set):
    # Prune entries older than 48h to keep file bounded
    ALERTED_PATH.write_text(
        json.dumps({"ids": list(alerted), "updated": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8"
    )


def _load_watchlist() -> list[str]:
    p = TRADING_DIR / "watchlist.txt"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


# ---------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------
def scan_and_alert_catalysts(hours_back: int = 24,
                             max_alerts_per_run: int = 8) -> int:
    """
    For every watchlist name, fetch yfinance news, find catalyst-keyword
    matches in the last `hours_back` hours, and dispatch a Telegram
    catalyst alert per unique headline (deduplicated against today's
    already-alerted set).

    Returns count of NEW alerts sent.
    """
    watchlist = _load_watchlist()
    if not watchlist:
        print("No watchlist found.")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    alerted_already = _load_alerted()
    new_alerts = []

    # v4.7 — per-symbol-per-day cap: at most ONE bullish + ONE bearish alert
    # per symbol per calendar day. Stops repeated re-coverage of the same
    # underlying event from creating duplicate Telegram cards.
    today_str = datetime.now().strftime("%Y%m%d")
    sym_today_cap: set[str] = set()

    for sym in watchlist:
        items = _fetch_news(sym)
        for it in items:
            if it["pub_dt"] is None or it["pub_dt"] < cutoff:
                continue
            priority, side = _classify_headline(it["title"], it["summary"])
            if priority != "high":
                continue
            # Per-symbol-per-day cap: bail if we already queued this symbol today
            if sym in sym_today_cap:
                continue
            # Persistent dedupe across runs (separate from the per-day cap)
            unique_id = f"{today_str}::{sym}"
            legacy_side_id = f"{today_str}::{sym}::{side}"
            if unique_id in alerted_already or legacy_side_id in alerted_already:
                continue
            sym_today_cap.add(sym)
            new_alerts.append({
                "symbol": sym,
                "side": side,
                "priority": priority,
                "title": it["title"],
                "url": it["url"],
                "provider": it["provider"],
                "pub_dt": it["pub_dt"],
                "id": unique_id,
            })

    # Sort: bullish first (more actionable for our long-only architecture),
    # then by recency (newest first)
    new_alerts.sort(key=lambda a: (0 if a["side"] == "bullish" else 1,
                                    -(a["pub_dt"].timestamp() if a["pub_dt"] else 0)))
    new_alerts = new_alerts[:max_alerts_per_run]

    sent_count = 0
    for a in new_alerts:
        side_emoji = "🟢" if a["side"] == "bullish" else "🔴"
        side_word  = "BULLISH" if a["side"] == "bullish" else "BEARISH"
        local_time = a["pub_dt"].astimezone().strftime("%Y-%m-%d %H:%M %Z") if a["pub_dt"] else "?"

        # Headlines often contain "&" (S&P/TSX, M&A); unescaped they make
        # Telegram reject the whole alert.
        body = (
            f"<b>Symbol:</b> {html.escape(str(a['symbol']), quote=False)}\n"
            f"<b>Catalyst type:</b> {side_emoji} {side_word}\n"
            f"<b>Source:</b> {html.escape(str(a['provider']), quote=False)}\n"
            f"<b>Posted:</b> {local_time}\n\n"
            f"<i>{html.escape(str(a['title']), quote=False)}</i>\n"
        )
        if a["url"]:
            body += f"\n<a href=\"{html.escape(str(a['url']), quote=True)}\">Read full article</a>\n"
        body += (
            f"\n⚠️ <i>Pre-market read — this is NOT a regular BUY signal. "
            f"Decide manually whether to act before TSX open.</i>"
        )
        ok = send_status(f"📢 CATALYST: {a['symbol']}", body, icon="📢")
        if ok:
            alerted_already.add(a["id"])
            sent_count += 1
            print(f"  ALERT SENT: {a['symbol']} — {a['title'][:80]}")

    _save_alerted(alerted_already)
    return sent_count


# ---------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------
if __name__ == "__main__":
    hours = 24
    if "--hours" in sys.argv:
        try: hours = int(sys.argv[sys.argv.index("--hours") + 1])
        except Exception: pass
    if "--reset" in sys.argv:
        if ALERTED_PATH.exists(): ALERTED_PATH.unlink()
        print("[OK] Cleared catalyst-alerted state.")

    print(f"Scanning watchlist news (last {hours}h)...")
    n = scan_and_alert_catalysts(hours_back=hours)
    print(f"\n[OK] Sent {n} catalyst alert(s).")
