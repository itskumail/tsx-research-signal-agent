"""
enrichment.py
-------------
External-data enrichment for signal candidates. Two sources:

  PRIMARY:   yfinance — earnings dates, recent news headlines,
             analyst recommendation mean, target price, P/E.
             Reliable, no scraping, no Cloudflare. Used for every
             hot-name candidate.

  SECONDARY: Barchart — Technical Opinion strength, options nearest
             expiry / put-call positioning. Cloudflare-protected, so
             best-effort with a 3.5-sec inter-request delay. Only
             called for signals that already passed the rubric (small
             N per session) so we don't burn time on rejects.

The Telegram card formatter (telegram_notify.send_signal) reads the
combined enrichment dict and adds a small "External" footer below the
quant reasons.

Failures are non-fatal — every function returns a dict with whatever
fields it could fetch and an `error` key on failure.
"""
from __future__ import annotations
import sys
import time
import re
import html as html_lib
import urllib.request
import urllib.error
from datetime import datetime, timedelta

# Force UTF-8 stdio (Task Scheduler safety)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ---------------------------------------------------------------
# yfinance enrichment
# ---------------------------------------------------------------
def enrich_yfinance(symbol: str, currency: str = "CAD") -> dict:
    """Pulls earnings, headlines, analyst rec via yfinance library."""
    out = {"symbol": symbol, "source": "yfinance"}
    try:
        import yfinance as yf
    except ImportError:
        out["error"] = "yfinance not installed"
        return out

    # v5.4.2: use central resolver for correct Class-A/B + TSXV handling
    from yf_symbols import to_yf_ticker
    yf_sym = to_yf_ticker(symbol, currency=currency)
    try:
        t = yf.Ticker(yf_sym)
    except Exception as e:
        out["error"] = f"ticker init: {e}"
        return out

    # Fast metadata (single .info call — yfinance caches)
    try:
        info = t.info or {}
        out["analyst_rec"]    = info.get("recommendationMean")          # 1=Strong Buy → 5=Strong Sell
        out["analyst_count"]  = info.get("numberOfAnalystOpinions")
        out["target_mean"]    = info.get("targetMeanPrice")
        out["target_high"]    = info.get("targetHighPrice")
        out["target_low"]     = info.get("targetLowPrice")
        out["pe"]             = info.get("trailingPE")
        out["sector"]         = info.get("sector")
        out["industry"]       = info.get("industry")
        out["market_cap"]     = info.get("marketCap")
        out["dividend_yield"] = info.get("dividendYield")
    except Exception as e:
        out.setdefault("warnings", []).append(f"info: {e}")

    # Earnings date (calendar gives the next one)
    try:
        cal = t.calendar
        if cal is not None:
            ed = None
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
            else:  # DataFrame
                try: ed = cal.loc["Earnings Date"].iloc[0]
                except Exception: pass
            if ed:
                if hasattr(ed, "__iter__") and not isinstance(ed, str):
                    ed = list(ed)
                    ed = ed[0] if ed else None
                if ed:
                    out["next_earnings"] = str(ed)[:10]
    except Exception as e:
        out.setdefault("warnings", []).append(f"calendar: {e}")

    # Recent news headlines (top 5) — yfinance schema is now nested under
    # ['content']['title'] (changed late 2024). Old top-level title still
    # works for older versions; try both.
    try:
        news = t.news or []
        titles = []
        for n in news[:5]:
            c = n.get("content") or {}
            title = c.get("title") or n.get("title")
            if title:
                titles.append(title)
        out["headlines"] = titles
    except Exception as e:
        out.setdefault("warnings", []).append(f"news: {e}")

    # Recent upgrades/downgrades (last 30 days)
    try:
        ud = t.upgrades_downgrades
        if ud is not None and len(ud) > 0:
            recent = ud.head(3)
            out["recent_actions"] = [
                f"{r['Firm']}: {r.get('ToGrade', r.get('Action',''))}"
                for _, r in recent.iterrows()
            ]
    except Exception:
        pass

    return out


# ---------------------------------------------------------------
# Barchart fetch (best-effort, Cloudflare-tolerant)
# ---------------------------------------------------------------
_BARCHART_LAST_REQUEST = [0.0]
_BARCHART_DELAY_SEC    = 3.5

_BARCHART_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


def _barchart_wait():
    """Enforce 3.5-sec gap between Barchart requests."""
    elapsed = time.time() - _BARCHART_LAST_REQUEST[0]
    if elapsed < _BARCHART_DELAY_SEC:
        time.sleep(_BARCHART_DELAY_SEC - elapsed)
    _BARCHART_LAST_REQUEST[0] = time.time()


def _barchart_get(url: str) -> str | None:
    """GET helper that respects rate limit and tolerates Cloudflare 403."""
    _barchart_wait()
    try:
        req = urllib.request.Request(url, headers=_BARCHART_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429, 503):
            return None  # Cloudflare blocked — caller treats as no-data
        return None
    except Exception:
        return None


def _plain_text(raw_html: str) -> str:
    """Collapse Barchart HTML into searchable text while keeping word spacing."""
    if not raw_html:
        return ""
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw_html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = str(value).replace(",", "").replace("$", "").replace("%", "").strip()
    if not cleaned or cleaned.upper() == "N/A":
        return None
    try:
        return float(cleaned)
    except Exception:
        return None


def _future_barchart_date(value: str | None) -> str | None:
    """Return mm/dd/yy-ish dates only when they are not stale/past dates."""
    if not value:
        return None
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            d = datetime.strptime(value.strip(), fmt).date()
            if d >= datetime.now().date():
                return value.strip()
            return None
        except Exception:
            continue
    return None


def _html_table_rows(raw_html: str, header_text: str) -> list[list[str]]:
    """
    Extract rows from the first HTML table containing header_text.
    This stays dependency-free so Task Scheduler can run with the current venv.
    """
    if not raw_html or header_text not in raw_html:
        return []
    tables = re.findall(r"<table\b.*?</table>", raw_html, flags=re.IGNORECASE | re.DOTALL)
    for table in tables:
        if header_text not in table:
            continue
        rows = []
        row_htmls = re.findall(r"<tr\b[^>]*>(.*?)</tr>", table, flags=re.IGNORECASE | re.DOTALL)
        for row_html in row_htmls:
            cells = []
            cell_htmls = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, flags=re.IGNORECASE | re.DOTALL)
            for cell_html in cell_htmls:
                cell = _plain_text(cell_html)
                if cell:
                    cells.append(cell)
            if cells:
                rows.append(cells)
        return rows
    return []


def _parse_barchart_analyst(raw_html: str) -> dict:
    """Parse the Current row from Barchart's Average Analyst Ratings block."""
    text = _plain_text(raw_html)
    out = {}
    ratings = r"Strong Buy|Moderate Buy|Buy|Hold|Moderate Sell|Strong Sell|Sell"
    m = re.search(
        rf"Average Analyst Ratings\b.*?\bCurrent\s+({ratings})\s+"
        r"([0-5](?:\.\d+)?)\s+Based on\s+(\d+)\s+analysts?",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        out["barchart_analyst_consensus"] = m.group(1).title()
        score = _to_float(m.group(2))
        if score is not None:
            out["barchart_analyst_score"] = score
        try:
            out["barchart_analyst_count"] = int(m.group(3))
        except Exception:
            pass

    m = re.search(r'"meanTargetEstimate"\s*:\s*"?(\d+(?:\.\d+)?)"?', raw_html)
    if m:
        target = _to_float(m.group(1))
        if target is not None:
            out["barchart_target_mean"] = target
    return out


def _parse_barchart_insider(raw_html: str) -> dict:
    """Parse the Last 3 Months insider summary instead of counting page words."""
    text = _plain_text(raw_html)
    m = re.search(
        r"Insider Transaction Discretionary Trades Summary\s+Last 3 Months\s+"
        r"(\d+)\s+Buys\s+([\d,]+)\s+Shares\s+(\d+)\s+Sells\s+([\d,]+)\s+Shares",
        text,
        flags=re.IGNORECASE,
    )
    if not m:
        return {}
    buys = int(m.group(1))
    sells = int(m.group(3))
    return {
        "barchart_insider_period": "3mo",
        "barchart_insider_net": buys - sells,
        "barchart_insider_buy_count": buys,
        "barchart_insider_sell_count": sells,
        "barchart_insider_buy_shares": int(m.group(2).replace(",", "")),
        "barchart_insider_sell_shares": int(m.group(4).replace(",", "")),
        "barchart_insider_net_shares": int(m.group(2).replace(",", "")) - int(m.group(4).replace(",", "")),
    }


def _parse_barchart_technical(raw_html: str) -> dict:
    """Parse 14-day RSI and ADX from Barchart's static technical tables."""
    out = {}
    for row in _html_table_rows(raw_html, "Relative Strength"):
        if row and row[0].lower() == "14-day" and len(row) >= 5:
            rsi = _to_float(row[4])
            if rsi is not None:
                out["barchart_rsi_14"] = rsi
            break

    for row in _html_table_rows(raw_html, "Directional Index (ADX)"):
        if row and row[0].lower() == "14-day" and len(row) >= 2:
            adx = _to_float(row[1])
            if adx is not None:
                out["barchart_adx_14"] = adx
            break
    return out


def _parse_barchart_profile(raw_html: str) -> dict:
    """Parse conservative fundamentals from Barchart's /profile page."""
    text = _plain_text(raw_html)
    out = {}

    m = re.search(r"Market Capitalization,\s*\$K\s+([\d,]+)", text, re.IGNORECASE)
    if m:
        cap_k = _to_float(m.group(1))
        if cap_k is not None:
            out["barchart_market_cap"] = cap_k * 1000

    m = re.search(r"Price/Earnings ttm\s+(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if m:
        pe = _to_float(m.group(1))
        if pe is not None:
            out["barchart_pe"] = pe

    m = re.search(r"Annual Dividend & Yield \(Fwd\)\s+[\d.]+\s+\((\d+(?:\.\d+)?)%\)", text, re.IGNORECASE)
    if not m:
        m = re.search(r"Annual Dividend & Yield \(Paid\)\s+[\d.]+\s+\((\d+(?:\.\d+)?)%\)", text, re.IGNORECASE)
    if m:
        dy = _to_float(m.group(1))
        if dy is not None:
            out["barchart_div_yield"] = dy / 100.0

    m = re.search(r"Next Earnings Date\s+(\d{1,2}/\d{1,2}/\d{2,4})", text, re.IGNORECASE)
    earn = _future_barchart_date(m.group(1) if m else None)
    if earn:
        out["barchart_earnings"] = earn
    return out


def enrich_barchart(symbol: str, currency: str = "CAD") -> dict:
    """
    Pulls Technical Opinion + nearest options expiry data from Barchart.
    Best-effort — returns {"error": ...} if Cloudflare blocks or parse fails.
    """
    out = {"symbol": symbol, "source": "barchart"}
    suffix = ".TO" if currency.upper() == "CAD" else ""

    # 1. Quote/overview page → Technical Opinion + earnings + sometimes news
    quote_url = f"https://www.barchart.com/stocks/quotes/{symbol}{suffix}"
    html = _barchart_get(quote_url)
    if not html:
        out["error"] = "blocked or unreachable (likely Cloudflare)"
    else:
        text = _plain_text(html)
        m = re.search(r'(\d{1,3})\s*%\s*(Buy|Sell|Hold)', text, re.IGNORECASE)
        if m and int(m.group(1)) <= 100:
            out["technical_opinion"] = f"{m.group(1)}% {m.group(2).title()}"

        m = re.search(r'[Nn]ext\s+[Ee]arnings.{0,80}?(\d{1,2}/\d{1,2}/\d{2,4})', text, re.DOTALL)
        earn = _future_barchart_date(m.group(1) if m else None)
        if earn:
            out["barchart_earnings"] = earn

    # 2. Options page → nearest expiry, P/C ratio
    opt_url = f"https://www.barchart.com/stocks/quotes/{symbol}{suffix}/options"
    opt_html = _barchart_get(opt_url)
    if opt_html:
        m = re.search(r'(\d{4}-\d{2}-\d{2})', opt_html)
        if m:
            out["nearest_expiry"] = m.group(1)
        m = re.search(r'[Pp]ut.{0,3}[Cc]all.{0,30}?(\d+\.\d+)', opt_html)
        if m:
            try: out["put_call_ratio"] = float(m.group(1))
            except Exception: pass

    # v5.0 — Extended Barchart endpoints (called only for survivors of rubric).
    # Each endpoint costs one 3.5-sec rate-limited request. Best-effort: if any
    # one fails (Cloudflare 403), we just skip that field, others continue.

    # 3. Analyst ratings → consensus + target price
    ar_url = f"https://www.barchart.com/stocks/quotes/{symbol}{suffix}/analyst-ratings"
    ar_html = _barchart_get(ar_url)
    if ar_html:
        out.update(_parse_barchart_analyst(ar_html))

    # 4. Insider trades → net activity in last 30/90 days
    it_url = f"https://www.barchart.com/stocks/quotes/{symbol}{suffix}/insider-trades"
    it_html = _barchart_get(it_url)
    if it_html:
        out.update(_parse_barchart_insider(it_html))

    # 5. Technical analysis → numeric RSI / MACD / ADX (cross-check vs ours)
    ta_url = f"https://www.barchart.com/stocks/quotes/{symbol}{suffix}/technical-analysis"
    ta_html = _barchart_get(ta_url)
    if ta_html:
        out.update(_parse_barchart_technical(ta_html))

    # 6. Profile page -> P/E, market cap, dividend yield. Barchart's menu calls
    # this "Key Statistics", but the stable URL is /profile.
    profile_url = f"https://www.barchart.com/stocks/quotes/{symbol}{suffix}/profile"
    profile_html = _barchart_get(profile_url)
    if profile_html:
        out.update(_parse_barchart_profile(profile_html))

    return out


# ---------------------------------------------------------------
# Combined enrichment
# ---------------------------------------------------------------
def enrich(symbol: str, currency: str = "CAD",
           include_barchart: bool = True,
           include_options: bool = True,
           include_ownership: bool = True,
           include_insider: bool = True,
           include_fundamentals: bool = True) -> dict:
    """
    Returns a merged dict of yfinance + Barchart + options + ownership +
    insider + fundamentals data. Each layer is best-effort and degrades
    gracefully. Called only for shortlisted survivors of the rubric.
    """
    out = {"symbol": symbol}
    yf_data = enrich_yfinance(symbol, currency)
    out["yf"] = yf_data
    if include_barchart:
        bc_data = enrich_barchart(symbol, currency)
        out["bc"] = bc_data
    if include_options:
        try:
            import options_analyzer
            opt = options_analyzer.analyze_options(symbol, currency=currency)
            if opt:
                out["options"] = opt
        except Exception:
            pass
    if include_ownership:
        try:
            import ownership_data
            own = ownership_data.get_ownership(symbol, currency=currency)
            if own:
                out["ownership"] = own
        except Exception:
            pass
    if include_insider:
        try:
            import insider_transactions
            ins = insider_transactions.get_insider_summary(symbol, currency=currency)
            if ins:
                out["insider"] = ins
        except Exception:
            pass
    if include_fundamentals:
        # v5.1 — FCF / FCF yield / Net debt-to-EBITDA from quarterly + annual
        # cashflow + balance sheet. Best-effort; banks are noisy (FCF concept
        # doesn't translate to deposit-heavy businesses) — DeepSeek handles
        # the interpretation downstream.
        try:
            import fundamentals as _fund
            suffix = ".TO" if currency.upper() == "CAD" else ""
            fdata = _fund.fetch_fundamentals(symbol, exchange_suffix=suffix)
            if fdata and fdata.get("available"):
                out["fundamentals"] = fdata
        except Exception as e:
            print(f"[enrichment] fundamentals fetch {symbol} failed: {e}")
    return out


# ---------------------------------------------------------------
# Card-footer formatter
# ---------------------------------------------------------------
def _esc(value) -> str:
    """Escape external/model text before it enters a Telegram HTML card."""
    return html_lib.escape(str(value), quote=False) if value is not None else ""


def format_card_footer(enrichment_dict: dict) -> str:
    """
    Produces the lines added below the quant reasons in the Telegram
    BUY card. Empty string if we have no useful data.
    """
    if not enrichment_dict:
        return ""
    yf_d = enrichment_dict.get("yf", {})
    bc_d = enrichment_dict.get("bc", {})
    lines = []

    # Earnings — prefer yfinance, fall back to Barchart parse
    earn = yf_d.get("next_earnings") or bc_d.get("barchart_earnings")
    if earn:
        lines.append(f"  • Earnings: <b>{_esc(earn)}</b>")

    # Analyst rec
    rec = yf_d.get("analyst_rec")
    cnt = yf_d.get("analyst_count")
    if rec is not None:
        label = "Strong Buy" if rec < 1.5 else "Buy" if rec < 2.5 else "Hold" if rec < 3.5 else "Sell"
        lines.append(f"  • Analyst: <b>{rec:.1f}</b>/5 ({label}, n={cnt or '?'})")

    # Target price
    tgt = yf_d.get("target_mean")
    if tgt:
        lines.append(f"  • Target mean: <b>${tgt:.2f}</b>")

    # Barchart Technical Opinion
    op = bc_d.get("technical_opinion")
    if op:
        lines.append(f"  • Barchart Opinion: <b>{_esc(op)}</b>")

    # Barchart analyst consensus + target (v5.0)
    bcc = bc_d.get("barchart_analyst_consensus")
    bct = bc_d.get("barchart_target_mean")
    bcn = bc_d.get("barchart_analyst_count")
    bcs = bc_d.get("barchart_analyst_score")
    if bcc or bct or bcs:
        bits = []
        if bcc: bits.append(f"<b>{_esc(bcc)}</b>")
        if bcs is not None: bits.append(f"score {bcs:.2f}/5")
        if bct: bits.append(f"target <b>${bct:.2f}</b>")
        if bcn: bits.append(f"n={bcn}")
        lines.append("  • Barchart Analyst: " + " · ".join(bits))

    # Barchart insider activity is a 3-month summary. Show share volume when
    # available so transaction count alone does not imply net buying/selling.
    net = bc_d.get("barchart_insider_net")
    if net is not None:
        bb = bc_d.get("barchart_insider_buy_count", 0)
        ss = bc_d.get("barchart_insider_sell_count", 0)
        ns = bc_d.get("barchart_insider_net_shares")
        if ns is not None:
            icon = "🟢" if ns > 0 else "🔴" if ns < 0 else "⚪"
            lines.append(f"  • {icon} Barchart insider 3mo: {bb}B / {ss}S · shares {ns:+,}")
        elif net != 0:
            icon = "🟢" if net > 0 else "🔴"
            lines.append(f"  • {icon} Barchart insider 3mo: {net:+} txns ({bb}B / {ss}S)")

    # Barchart cross-check on RSI/ADX (sanity vs our own indicators)
    bc_rsi = bc_d.get("barchart_rsi_14")
    bc_adx = bc_d.get("barchart_adx_14")
    if bc_rsi or bc_adx:
        bits = []
        if bc_rsi is not None: bits.append(f"RSI {bc_rsi:.1f}")
        if bc_adx is not None: bits.append(f"ADX {bc_adx:.1f}")
        lines.append("  • Barchart tech: " + " · ".join(bits))

    # Fundamentals (P/E, divyield)
    bc_pe = bc_d.get("barchart_pe")
    bc_dy = bc_d.get("barchart_div_yield")
    bc_cap = bc_d.get("barchart_market_cap")
    if bc_pe or bc_dy or bc_cap:
        bits = []
        if bc_pe is not None: bits.append(f"P/E {bc_pe:.1f}")
        if bc_dy is not None: bits.append(f"div {bc_dy*100:.2f}%")
        if bc_cap is not None:
            bits.append(f"cap ${bc_cap/1_000_000_000:.1f}B")
        lines.append("  • Fundamentals: " + " · ".join(bits))

    # Options
    pc = bc_d.get("put_call_ratio")
    exp = bc_d.get("nearest_expiry")
    if pc is not None or exp:
        bits = []
        if pc is not None:
            bits.append(f"P/C {pc:.2f} (OI context, not direction)")
        if exp:
            bits.append(f"next exp {_esc(exp)}")
        lines.append("  • Options: " + " · ".join(bits))

    # Recent upgrades/downgrades
    actions = yf_d.get("recent_actions") or []
    if actions:
        lines.append(f"  • Recent: {_esc(actions[0])}")

    # v5.0 — ownership block (institutional / insider %)
    own = enrichment_dict.get("ownership") or {}
    if own:
        try:
            from ownership_data import format_ownership_lines
            for ln in format_ownership_lines(own):
                lines.append(ln)
        except Exception:
            pass

    # v5.0 — insider transactions block
    ins_data = enrichment_dict.get("insider") or {}
    if ins_data:
        try:
            from insider_transactions import format_insider_lines
            for ln in format_insider_lines(ins_data):
                lines.append(ln)
        except Exception:
            pass

    # v4.9 — full options analysis (max pain / put wall / call wall)
    opt_full = enrichment_dict.get("options") or {}
    opt_section = ""
    if opt_full:
        try:
            from options_analyzer import format_options_footer
            opt_section = format_options_footer(opt_full)
        except Exception:
            opt_section = ""

    # v5.1 — Fundamentals block (FCF / yield / leverage)
    fund_data = enrichment_dict.get("fundamentals") or {}
    if fund_data.get("available"):
        try:
            from fundamentals import format_fundamentals_block
            block = format_fundamentals_block(fund_data, currency="CAD")
            if block.strip():
                lines.append("")  # spacer
                lines.append("<b>📊 Fundamentals:</b>")
                for ln in block.rstrip().split("\n"):
                    lines.append(ln)
        except Exception:
            pass

    # v5.0 — LLM AI Research footer (only present when LLM is enabled)
    llm_rat = enrichment_dict.get("_llm_rationale") or ""
    llm_con = enrichment_dict.get("_llm_concerns") or ""
    llm_veto = bool(enrichment_dict.get("_llm_veto", False))
    llm_section = ""
    if llm_rat:
        header = "🧠 AI Research"
        if llm_veto:
            header = "⚠️ AI Flagged — review carefully"
        llm_section = f"\n<b>{header}:</b>\n  • {_esc(llm_rat)}"
        if llm_con:
            llm_section += f"\n  • <i>Concerns: {_esc(llm_con)}</i>"
        llm_section += "\n"

    # v5.1 — Signal origin tag + news-LLM catalyst (broad-scope pre-pass)
    origin       = enrichment_dict.get("_signal_origin", "")
    news_pick    = enrichment_dict.get("_news_llm_pick") or {}
    origin_section = ""
    if origin == "BOTH" and news_pick:
        cat  = news_pick.get("catalyst", "")
        conf = news_pick.get("confidence", "")
        tfr  = news_pick.get("timeframe_relevance", "")
        origin_section = (
            f"\n<b>🟢 BOTH AGREE — News + Technical</b>\n"
            f"  • <b>News catalyst:</b> {_esc(cat)}\n"
            f"  • Confidence: {_esc(conf)} · Plays out: {_esc(tfr)}\n"
        )
    elif origin == "NEWS-DRIVEN":
        # v5.5 — name that did NOT pass the trend rubric; pulled in by a fresh
        # catalyst and approved by DeepSeek (the news-driven inclusion lane).
        cat = enrichment_dict.get("_catalyst", "")
        origin_section = (
            f"\n<b>⚡ NEWS-DRIVEN</b> (catalyst pick — AI-approved, not a trend setup)\n"
            + (f"  • <b>Catalyst:</b> {_esc(cat)}\n" if cat else "")
        )
    elif origin == "DEEP-SCAN":
        # v5.6 — full-watchlist triage pick: DeepSeek flagged it on momentum/
        # fundamentals/options-positioning, then approved it in stage 2.
        cat = enrichment_dict.get("_catalyst", "")
        origin_section = (
            f"\n<b>🔬 DEEP-SCAN</b> (full-watchlist AI pick — momentum/fundamentals/options flow)\n"
            + (f"  • <b>Why:</b> {_esc(cat)}\n" if cat else "")
        )
    elif origin == "TECHNICAL":
        origin_section = "\n<b>📊 Origin:</b> Technical (rubric-driven)\n"

    if not lines and not opt_section and not llm_section and not origin_section:
        return ""
    external_block = "\n<b>📊 External:</b>\n" + "\n".join(lines) if lines else ""
    return origin_section + external_block + opt_section + llm_section


# ---------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------
if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "RY"
    print(f"Enriching {sym}.TO ...")
    data = enrich(sym)
    import json
    print(json.dumps(data, indent=2, default=str))
    print("\n--- Telegram card footer ---")
    print(format_card_footer(data))
