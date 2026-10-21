"""
news_llm_filter.py
------------------
Broad-scope LLM news synthesis layer (v5.4).

PURPOSE:
  After news_fetcher (Telegram) and news_rss_fetcher (RSS feeds) have
  populated logs/news_events_<today>.jsonl + the supplemental RSS log,
  this module compiles ALL recent headlines + the full watchlist and
  asks the LLM to identify which watchlist tickers have bullish or
  bearish catalysts. The output is a small set of "news-driven" picks
  that get promoted to the top of the candidate pool BEFORE technical
  scoring.

TWO ENTRY POINTS:
  1. find_news_driven_candidates()  — per-session broad synthesis (Mon/Wed/Fri).
     broad -> narrow (all news + 68 names -> ~3-5 picks per session)
  2. generate_weekly_focus_list()   — Monday premarket_us ONLY.
     Reads 72h of weekend news, produces a 15-20 name high-conviction
     "Weekly Focus List" written to logs/weekly_focus_list.json.
     All weekday sessions promote these names during scanning.

DIFFERENT FROM llm_researcher.py:
  llm_researcher  — narrow -> narrow (top 6 -> re-ranked top 2-3)
  news_llm_filter — broad -> narrow (all news + 68 names -> ~3-5 picks)

CADENCE (controlled in session_runner via NEWS_LLM_SCHEDULE):
  Mon premarket_us + session1 + session2
  Wed session1 + session2
  Fri session1 + session2
  Weekly Focus generation: Mon premarket_us only
  = 7 calls/week

COST:
  ~$0.014 per call with DeepSeek (~$0.50/month total)
  ~$0.10 per call with Claude Haiku (~$3.50/month total)

NO-OP without API key — everything else still works.
"""
from __future__ import annotations
import os
import json
import warnings
warnings.filterwarnings("ignore")
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Reuse the LLM provider abstraction we already built
import llm_researcher
from market_context import format_market_context

TRADING_DIR       = Path(r"C:\Trading")
LOGS_DIR          = TRADING_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH        = LOGS_DIR / "llm_news_last_run.json"
WEEKLY_FOCUS_PATH = LOGS_DIR / "weekly_focus_list.json"  # v5.4: Monday weekly focus list

# Cadence: which (weekday, session_name) pairs trigger the news-LLM filter
# weekday: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
NEWS_LLM_SCHEDULE: set[tuple[int, str]] = {
    (0, "premarket_us"),  # Monday 5:45 AM
    (0, "session1"),      # Monday 8:00 AM (Added for testing)
    (0, "session2"),       # Monday 11:00 AM
    (2, "session1"),       # Wednesday 8:00 AM
    (2, "session2"),       # Wednesday 11:00 AM
    (4, "session1"),       # Friday 8:00 AM
    (4, "session2"),       # Friday 11:00 AM
}


def is_scheduled(session_name: str, when: datetime | None = None) -> bool:
    """True only on Mon/Wed/Fri specific sessions."""
    when = when or datetime.now()
    return (when.weekday(), session_name) in NEWS_LLM_SCHEDULE


def is_enabled() -> bool:
    """Active only when LLM provider + key are configured."""
    return llm_researcher.is_enabled()


# ---------------------------------------------------------------
# State persistence (per-session last-run timestamp)
# ---------------------------------------------------------------
def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------
# News compilation — gather everything since last run
# ---------------------------------------------------------------
def _collect_recent_news(hours_back: float = 72.0) -> list[dict]:
    """
    Pulls Telegram-matched events + RSS-matched events from today's and
    yesterday's news_events_<date>.jsonl (always includes the weekend
    backlog when run on Monday).

    Returns list of {headline, body, tickers, source, timestamp}.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    out = []
    for offset in range(0, 5):
        d = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
        path = LOGS_DIR / f"news_events_{d}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except Exception:
                continue
            try:
                ts = datetime.fromisoformat(
                    evt.get("timestamp", "").replace("Z", "+00:00")
                )
            except Exception:
                continue
            if ts < cutoff:
                continue
            out.append(evt)
    # Sort newest-first, dedupe identical headlines
    seen = set()
    deduped = []
    for evt in sorted(out, key=lambda e: e.get("timestamp", ""), reverse=True):
        key = (evt.get("headline", "") or "")[:100]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(evt)
    return deduped


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
# Prompt builder
# ---------------------------------------------------------------
def _build_prompt(news_events: list[dict], watchlist: list[str],
                  market_context: dict) -> str:
    headlines_block = []
    for evt in news_events[:200]:  # cap at 200 for token control
        ts = evt.get("timestamp", "")[:16].replace("T", " ")
        src = (evt.get("source") or "")[:30]
        hl  = (evt.get("headline") or "")[:200]
        tickers = evt.get("tickers") or []
        if tickers:
            ttag = " [" + ",".join(tickers[:5]) + "]"
        else:
            ttag = ""
        headlines_block.append(f"  [{ts} · {src}]{ttag} {hl}")

    return f"""You are a Canadian-market trading analyst. Below is a stream of recent
news headlines from Telegram channels and corporate-news RSS feeds.

YOUR JOB:
Identify which tickers in MY WATCHLIST have a clear bullish or bearish
catalyst implied by this news. ONLY include tickers that are in my
watchlist. Be CONSERVATIVE — most names will not have a clear catalyst.
Skip generic macro news that affects "the market" broadly.

For each pick, classify:
  • side       : "bullish" or "bearish"
  • catalyst   : 1-line summary of the news catalyst (e.g. "Q1 earnings beat", "M&A target", "FDA approval", "Guidance cut")
  • confidence : "high" / "medium" / "low"
  • timeframe_relevance : "1-DAY" (catalyst is fresh, plays out today/tomorrow) or "1-WEEK" (slower, multi-day setup)

MY WATCHLIST ({len(watchlist)} names):
{", ".join(watchlist)}

MARKET CONTEXT:
{format_market_context(market_context)}

NEWS HEADLINES ({len(headlines_block)}):
{chr(10).join(headlines_block)}

OUTPUT JSON ONLY (no other text):
{{
  "picks": [
    {{
      "symbol": "TICKER",
      "side": "bullish",
      "catalyst": "1-line summary",
      "confidence": "high",
      "timeframe_relevance": "1-DAY"
    }}
  ],
  "summary": "1-2 sentences on the day's overall news tone for the watchlist"
}}

If no clear catalysts exist, return picks: [].
"""


# ---------------------------------------------------------------
# Main entry — called by session_runner only on scheduled sessions
# ---------------------------------------------------------------
def find_news_driven_candidates(market_context: dict,
                                session_name: str) -> dict:
    """
    Returns {picks: [...], summary: ""} or {} if disabled / no API key.
    Each pick has: symbol, side, catalyst, confidence, timeframe_relevance.
    Bullish picks should be PROMOTED to the top of the hot-names pool.
    Bearish picks should be NOTED on the card if the bot was about to
    flag the same name.
    """
    if not is_enabled():
        return {}
    if not is_scheduled(session_name):
        return {}

    news_events = _collect_recent_news(hours_back=72.0)
    if not news_events:
        return {}

    watchlist = _load_watchlist()
    if not watchlist:
        return {}

    print(f"[news_llm_filter] {session_name}: synthesizing {len(news_events)} headlines "
          f"vs {len(watchlist)} watchlist names via {llm_researcher.PROVIDER}...")

    prompt = _build_prompt(news_events, watchlist, market_context)
    raw    = llm_researcher._call_llm(prompt)
    if not raw:
        return {}

    # Strip code fences
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:].strip()

    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"[news_llm_filter] failed to parse JSON: {e}")
        return {}

    picks = data.get("picks") or []
    # Restrict to watchlist names only
    wl_upper = {s.upper() for s in watchlist}
    filtered = [p for p in picks if (p.get("symbol") or "").upper() in wl_upper]

    # Persist last-run timestamp
    state = _load_state()
    state[session_name] = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "picks_count": len(filtered),
        "summary": data.get("summary", "")[:200],
    }
    _save_state(state)

    # Persist the picks themselves so the user has an audit trail
    today = datetime.now().strftime("%Y%m%d")
    audit_path = LOGS_DIR / f"news_llm_picks_{today}.jsonl"
    with audit_path.open("a", encoding="utf-8") as f:
        for p in filtered:
            f.write(json.dumps({
                **p,
                "session": session_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }) + "\n")

    return {
        "picks": filtered,
        "summary": data.get("summary", ""),
    }


# ---------------------------------------------------------------
# Helper: extract bullish picks for promotion
# ---------------------------------------------------------------
def get_bullish_symbols(result: dict, min_confidence: str = "low") -> list[str]:
    """
    Returns ticker symbols rated bullish above the confidence threshold.
    Confidence ordering: low < medium < high
    """
    rank = {"low": 1, "medium": 2, "high": 3}
    floor = rank.get(min_confidence, 1)
    out = []
    for p in (result.get("picks") or []):
        if p.get("side", "").lower() != "bullish":
            continue
        if rank.get(p.get("confidence", "low").lower(), 0) < floor:
            continue
        sym = (p.get("symbol") or "").upper().strip()
        if sym:
            out.append(sym)
    return out


def get_pick_for_symbol(result: dict, symbol: str) -> dict | None:
    """Returns the pick dict for a specific symbol, or None."""
    sym_u = symbol.upper().strip()
    for p in (result.get("picks") or []):
        if (p.get("symbol") or "").upper().strip() == sym_u:
            return p
    return None



# ---------------------------------------------------------------
# v5.4 — Weekly Focus List (Monday premarket_us only)
# ---------------------------------------------------------------
def _build_weekly_focus_prompt(news_events: list[dict], watchlist: list[str],
                               market_context: dict) -> str:
    """
    Build the Monday weekly-focus prompt.

    The LLM reads 72h of weekend news and returns a priority-ranked list
    of 10-20 watchlist names to focus on this week, each with a 1-line
    catalyst note and a timeframe relevance tag.
    """
    headlines_block = []
    for evt in news_events[:300]:
        ts  = evt.get("timestamp", "")[:16].replace("T", " ")
        src = (evt.get("source") or "")[:30]
        hl  = (evt.get("headline") or "")[:200]
        tickers = evt.get("tickers") or []
        ttag = (" [" + ",".join(tickers[:5]) + "]") if tickers else ""
        headlines_block.append(f"  [{ts} · {src}]{ttag} {hl}")

    return f"""You are a Canadian-market trading analyst preparing the WEEKLY FOCUS LIST.

It is Monday morning. Below is a stream of weekend + early-week news from
Telegram channels and corporate RSS feeds.

YOUR JOB:
Based on this news, select the 10-20 most actionable tickers from MY WATCHLIST
for THIS WEEK. These are names where:
  * There is a clear catalyst (earnings, contract, M&A, sector shift, macro event)
  * OR a clear technical theme that aligns with the news environment
  * OR a risk that makes them worth monitoring for a SHORT (PUT) setup

Classify each pick:
  * side       : "bullish" | "bearish" | "watchlist" (neutral, keep an eye on it)
  * catalyst   : 1-line specific catalyst for this week
  * confidence : "high" / "medium" / "low"
  * week_theme : the 1-word macro theme driving this (e.g. "oil", "rates", "gold", "earnings")

MY WATCHLIST ({len(watchlist)} names):
{", ".join(watchlist)}

MARKET CONTEXT (pre-market Monday):
{format_market_context(market_context)}

WEEKEND NEWS ({len(headlines_block)} headlines):
{chr(10).join(headlines_block)}

OUTPUT JSON ONLY (no other text):
{{
  "focus_list": [
    {{
      "symbol": "TICKER",
      "side": "bullish",
      "catalyst": "1-line catalyst for this week",
      "confidence": "high",
      "week_theme": "oil"
    }}
  ],
  "week_summary": "2-3 sentences on the dominant macro theme and risk environment for this week"
}}

If news is sparse (weekend quiet), return the top names by sector strength with
side="watchlist" and catalyst="no specific catalyst, monitor technicals".
"""


def generate_weekly_focus_list(market_context: dict) -> dict:
    """
    v5.4 — Generate the Monday Weekly Focus List.

    Called ONCE per week at premarket_us on Monday. Reads 72h of weekend news,
    asks DeepSeek to select 10-20 high-conviction watchlist names, and writes
    the result to logs/weekly_focus_list.json so all daily sessions can use it.

    Returns {focus_list: [...], week_summary: "..."} or {} if disabled / no news.
    Each item in focus_list has: symbol, side, catalyst, confidence, week_theme.

    Used by session_runner to boost focus names to the top of the scanning pool
    BEFORE technical scoring runs, ensuring the LLM's weekly thesis is applied.

    Cost: ~1 DeepSeek call (~$0.014) per week.

    HOW TO TRIGGER MANUALLY:
        python news_llm_filter.py --weekly
    """
    if not is_enabled():
        return {}

    today_wd = datetime.now().weekday()  # 0=Mon
    if today_wd != 0:
        print("[weekly-focus] Skipping — only runs on Monday premarket.")
        return {}

    # Check if already ran today (avoid double-runs on session restart)
    if WEEKLY_FOCUS_PATH.exists():
        try:
            prev = json.loads(WEEKLY_FOCUS_PATH.read_text(encoding="utf-8"))
            if prev.get("generated_date") == datetime.now().strftime("%Y-%m-%d"):
                print("[weekly-focus] Already generated today. Loading from cache.")
                return {
                    "focus_list":  prev.get("focus_list", []),
                    "week_summary": prev.get("week_summary", ""),
                }
        except Exception:
            pass

    news_events = _collect_recent_news(hours_back=72.0)
    watchlist   = _load_watchlist()
    if not watchlist:
        return {}

    print(f"[weekly-focus] Generating Weekly Focus List: "
          f"{len(news_events)} headlines x {len(watchlist)} watchlist names "
          f"via {llm_researcher.PROVIDER}...")

    prompt = _build_weekly_focus_prompt(news_events, watchlist, market_context)
    raw    = llm_researcher._call_llm(prompt)
    if not raw:
        return {}

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:].strip()

    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"[weekly-focus] JSON parse failed: {e}")
        return {}

    focus_list = data.get("focus_list") or []
    wl_upper   = {s.upper() for s in watchlist}
    filtered   = [p for p in focus_list if (p.get("symbol") or "").upper() in wl_upper]

    week_summary = data.get("week_summary", "")
    today_str    = datetime.now().strftime("%Y-%m-%d")

    # Persist — overwrite each Monday
    record = {
        "generated_date": today_str,
        "focus_count":    len(filtered),
        "week_summary":   week_summary,
        "focus_list":     filtered,
    }
    WEEKLY_FOCUS_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"[weekly-focus] Saved {len(filtered)} focus names to {WEEKLY_FOCUS_PATH}")
    if week_summary:
        print(f"[weekly-focus] {week_summary}")

    return {"focus_list": filtered, "week_summary": week_summary}


def load_weekly_focus_list(max_age_days: int = 7) -> dict:
    """
    Load the current week's focus list from disk.

    Called by session_runner at the start of EVERY session (Mon-Fri) to promote
    focus names during scanning. Returns {focus_list: [...], week_summary: ""}
    or {} if the file is missing or older than max_age_days.

    max_age_days=7 means the Monday list expires by next Monday.
    """
    if not WEEKLY_FOCUS_PATH.exists():
        return {}
    try:
        data     = json.loads(WEEKLY_FOCUS_PATH.read_text(encoding="utf-8"))
        gen_date = datetime.strptime(data.get("generated_date", "1970-01-01"), "%Y-%m-%d")
        if (datetime.now() - gen_date).days > max_age_days:
            return {}
        return {
            "focus_list":   data.get("focus_list", []),
            "week_summary": data.get("week_summary", ""),
        }
    except Exception:
        return {}


def get_weekly_focus_symbols(side_filter: str | None = None) -> list[str]:
    """
    Convenience: return symbol list from the current weekly focus list.

    side_filter: restrict to "bullish" / "bearish" / "watchlist". None = return all.
    """
    items = (load_weekly_focus_list().get("focus_list") or [])
    out = []
    for p in items:
        if side_filter and (p.get("side") or "").lower() != side_filter.lower():
            continue
        sym = (p.get("symbol") or "").upper().strip()
        if sym:
            out.append(sym)
    return out


if __name__ == "__main__":
    import sys
    sn = sys.argv[1] if len(sys.argv) > 1 else "premarket_us"
    if "--weekly" in sys.argv:
        print("=== Weekly Focus List Generation ===")
        result = generate_weekly_focus_list({"regime": "🟢 BULLISH"})
        print(json.dumps(result, indent=2))
    else:
        print(f"Test run: session={sn}, scheduled={is_scheduled(sn)}, enabled={is_enabled()}")
        if is_enabled() and is_scheduled(sn):
            result = find_news_driven_candidates({"regime": "🟢 BULLISH"}, sn)
            print(json.dumps(result, indent=2))
        else:
            print("Skipped (not scheduled today, or LLM not configured).")
