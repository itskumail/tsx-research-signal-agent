"""
llm_researcher.py
-----------------
Pluggable LLM "final research" layer for Telegram signal candidates.

Architecture:
  • Default: NO-OP. If no LLM_PROVIDER / LLM_API_KEY set in .env, this
    module returns the candidate list unchanged. The bot continues to
    work exactly as before — no API calls, no extra cost.
  • Activated: set LLM_PROVIDER=claude|deepseek|openai and LLM_API_KEY=...
    in .env. The module then calls the chosen API to do final research
    on the shortlisted survivors and returns a re-ranked list with
    LLM rationale per candidate.

This is invoked AFTER the rule-based scorer produces survivors and
AFTER yfinance/Barchart/options enrichment is complete — so the LLM
gets the full context and just makes the final shortlist + reasoning.

API providers supported:
  • claude   → Anthropic API (claude-haiku-4.5 default)
  • deepseek → DeepSeek API (deepseek-chat default, OpenAI-compatible)
  • openai   → OpenAI API (gpt-4o-mini default)

Add the API key when you're ready. Until then, the bot operates without it.
"""
from __future__ import annotations
import os
import re
import json
import warnings
warnings.filterwarnings("ignore")
from datetime import datetime
from pathlib import Path
from market_context import format_market_context


# Env loader (mirror pattern from telegram_notify / news_fetcher)
def _load_env() -> dict:
    env_path = Path(r"C:\Trading\.env")
    if not env_path.exists():
        return {}
    env = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


_env = _load_env()
PROVIDER = (os.environ.get("LLM_PROVIDER") or _env.get("LLM_PROVIDER") or "").lower().strip()
API_KEY  = os.environ.get("LLM_API_KEY")  or _env.get("LLM_API_KEY")
MODEL    = os.environ.get("LLM_MODEL")    or _env.get("LLM_MODEL")  # optional override

# Default models per provider.
# deepseek-reasoner uses internal chain-of-thought before answering — slower
# but significantly better at structured ranking and nuanced market analysis.
# The reasoning tokens are hidden; only the final JSON answer is returned in
# choices[0].message.content (same field as deepseek-chat). No code changes
# needed beyond the model name. Average response: 30–90 s vs 5–15 s for chat.
DEFAULT_MODELS = {
    "claude":   "claude-haiku-4-5",
    "deepseek": "deepseek-reasoner",
    "openai":   "gpt-4o-mini",
}


def is_enabled() -> bool:
    """Returns True only if an API key + provider are configured."""
    return bool(PROVIDER and API_KEY and PROVIDER in DEFAULT_MODELS)


# ---------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------
def _build_prompt(candidates: list[dict], market_context: dict) -> str:
    """
    Build the research prompt for the LLM. Each candidate dict includes
    score, setup_type, reasons, warnings, and external enrichment.
    """
    cand_blocks = []
    for c in candidates:
        sym = c.get("symbol", "?")
        block = [
            f"### {sym}",
            f"  Setup: {c.get('setup_type', '?')}",
            f"  Score: {c.get('score', '?')}/{c.get('min_score_required', '?')} (weighted {c.get('weighted_score', '?')})",
            f"  Entry: ${c.get('entry_price', 0):.2f} · Stop: ${c.get('stop_loss', 0):.2f} · Target: ${c.get('take_profit', 0):.2f}",
            f"  Reasons: " + "; ".join(c.get("reasons", [])[:6]),
        ]
        if c.get("warnings"):
            block.append(f"  Warnings: " + "; ".join(c["warnings"][:4]))
        # v5.5 — news-driven lane. These names did NOT pass the technical trend
        # rubric; they're here purely on a fresh catalyst. DeepSeek is the
        # gatekeeper: decide (send_ok) whether the catalyst justifies a trade.
        if c.get("news_driven"):
            block.append(f"  ⚡ NEWS-DRIVEN — did NOT pass the trend rubric. "
                         f"Catalyst: {c.get('catalyst') or 'n/a'}")
        elif c.get("catalyst"):
            block.append(f"  Catalyst: {c.get('catalyst')}")
        # v5.6 — first-pass triage verdict from the full-watchlist deep scan
        if c.get("triage"):
            block.append(f"  Watchlist triage (first-pass AI): {c['triage']}")
        ext = c.get("external") or {}
        yf_d = ext.get("yf") or {}
        bc_d = ext.get("bc") or {}
        opt  = ext.get("options") or {}
        own  = ext.get("ownership") or {}
        ins  = ext.get("insider") or {}
        if yf_d.get("next_earnings"):
            block.append(f"  Earnings: {yf_d['next_earnings']}")
        if yf_d.get("analyst_rec"):
            block.append(f"  yfinance analyst: {yf_d['analyst_rec']:.1f}/5 (n={yf_d.get('analyst_count','?')})  target ${yf_d.get('target_mean', 0) or 0:.2f}")
        # v5.1 — fundamentals (FCF / yield / leverage)
        fund = ext.get("fundamentals") or {}
        if fund.get("available"):
            fparts = []
            if fund.get("fcf_ttm") is not None:
                fcf = fund["fcf_ttm"]
                fcf_str = f"${fcf/1e9:.2f}B" if abs(fcf) >= 1e9 else f"${fcf/1e6:.0f}M"
                yoy = fund.get("fcf_yoy_pct")
                fparts.append(f"FCF TTM {fcf_str}" + (f" ({yoy:+.0f}% YoY)" if yoy is not None else ""))
            if fund.get("fcf_yield_pct") is not None:
                fparts.append(f"FCF yield {fund['fcf_yield_pct']:+.2f}%")
            if fund.get("net_debt_to_ebitda") is not None:
                fparts.append(f"ND/EBITDA {fund['net_debt_to_ebitda']:.2f}x")
            if fparts:
                block.append(f"  Fundamentals: " + " | ".join(fparts) + f"  [{fund.get('quality_tag','')}]")
            if fund.get("flags"):
                block.append(f"  Fund. flags: " + "; ".join(fund["flags"][:2]))
        if bc_d.get("technical_opinion"):
            block.append(f"  Barchart Opinion: {bc_d['technical_opinion']}")
        if bc_d.get("barchart_analyst_consensus"):
            block.append(f"  Barchart analyst: {bc_d['barchart_analyst_consensus']}  target ${bc_d.get('barchart_target_mean', 0) or 0:.2f}")
        if opt.get("max_pain"):
            block.append(f"  US dual-chain USD context exp {opt.get('expiry')}: max pain ${opt['max_pain']:.2f}, OI P/C {opt.get('pc_ratio')}; not proof of direction or Canadian price levels")
        if own.get("institutional_pct") is not None:
            block.append(f"  Ownership: {own['institutional_pct']*100:.1f}% institutional, {(own.get('insider_pct') or 0)*100:.1f}% insider")
        if ins.get("buy_count"):
            block.append(f"  Insider 30d: {ins.get('buy_count', 0)} buy / {ins.get('sell_count', 0)} sell · net ${ins.get('net_value_usd', 0):+,.0f}")
        if yf_d.get("headlines"):
            heads = "; ".join(yf_d["headlines"][:3])
            block.append(f"  Recent news: {heads}")
        cand_blocks.append("\n".join(block))

    return f"""You are a Canadian-market trading analyst. Below are candidate BUY signals
that already passed a quantitative rubric. Your job is to do a final
research pass and rank them by quality, considering:
  • Setup quality (technical pattern alignment)
  • Fundamentals (FCF trend, FCF yield, leverage). Note: for BANKS and
    INSURERS, traditional FCF is meaningless (deposits inflate it) — focus
    instead on regime/sector tape for those names.
  • News context and catalysts
  • Analyst / Barchart consensus
  • Options positioning (max pain, walls)
  • Insider activity
  • Market regime and sector tape

MARKET CONTEXT:
{format_market_context(market_context)}
Canadian conditions govern Canadian stock selection. US readings are supporting
context. A long-term uptrend does not imply a positive session today.

CANDIDATES ({len(candidates)}):

{chr(10).join(cand_blocks)}

Some candidates are tagged "⚡ NEWS-DRIVEN" — they did NOT pass the technical
trend rubric and are present ONLY because of a fresh catalyst (news, earnings,
a price/volume thrust). For THESE names you are the gatekeeper: set
"send_ok": true only when the catalyst is strong and specific enough to justify
a trade despite weak/mixed technicals (e.g. a real earnings beat, an upgrade
cascade, an M&A/contract catalyst with follow-through). If it's stale, vague,
or already priced in, set "send_ok": false and they will NOT be sent. For
normal (rubric-passing) candidates, omit send_ok or set it true.

OUTPUT JSON ONLY (no other text), schema:
{{
  "ranked": [
    {{
      "symbol": "TICKER",
      "rank": 1,
      "rationale": "1-2 sentences why this is best, citing specific technicals, fundamentals, or the catalyst for news-driven names",
      "concerns": "any red flags — be specific (e.g. 'FCF down 30% YoY', 'leverage above 4x', 'sector headwinds')",
      "veto_flag": false,
      "send_ok": true
    }},
    ...
  ],
  "summary": "1-2 sentences on overall regime + quality of today's slate"
}}

Set "veto_flag": true ONLY when one of these is true:
  • Fundamentals deteriorating sharply AND no offsetting catalyst
    (e.g. FCF down >25% YoY with no clear bullish story)
  • Net debt / EBITDA above 4x without clear deleveraging path
  • Imminent earnings risk + negative pre-announcement signals
  • Major news catalyst (lawsuit, fraud, dividend cut, downgrade cascade)

veto_flag=true causes a soft veto — the card is still sent but with a
"⚠️ AI flagged" badge and 1-star confidence drop. Use sparingly. When
in doubt, leave veto_flag=false and put the concern in "concerns".

Rank ALL candidates from best (1) to worst.
"""


# ---------------------------------------------------------------
# API callers (one per provider)
# ---------------------------------------------------------------
def _call_anthropic(prompt: str, *, timeout: float | None = None) -> str | None:
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic(api_key=API_KEY, **({'timeout': timeout, 'max_retries': 0} if timeout else {}))
        resp = client.messages.create(
            model=MODEL or DEFAULT_MODELS["claude"],
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
    except Exception as e:
        print(f"[llm_researcher] Anthropic call failed: {type(e).__name__}")
        return None


def _call_openai_compat(prompt: str, base_url: str | None = None, *, timeout: float | None = None) -> str | None:
    """Used by both deepseek (with base_url) and openai (without).

    deepseek-reasoner note: the model performs internal chain-of-thought
    reasoning before writing the answer. That reasoning is stored in
    choices[0].message.reasoning_content (DeepSeek-side attribute, not
    visible in the standard OpenAI SDK type). The final answer we need is
    in choices[0].message.content — same field as every other model.
    Empty final content is not an answer; reasoning text is never substituted.
    """
    try:
        import openai
    except ImportError:
        return None
    try:
        client = openai.OpenAI(api_key=API_KEY, base_url=base_url,
                               **({'timeout': timeout, 'max_retries': 0} if timeout else {}))
        # Preserve the existing output cap; report missing final JSON honestly
        # rather than parsing internal reasoning as candidate approvals.
        model_name = MODEL or DEFAULT_MODELS[PROVIDER]
        max_out = 8000 if "reasoner" in (model_name or "").lower() else 4000
        resp = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_out,
            # Force JSON output mode where supported (deepseek + openai). Eliminates
            # most parse failures by guaranteeing the response is valid JSON.
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        if not content:
            finish = getattr(resp.choices[0], 'finish_reason', None)
            reason = 'output_limit' if finish == 'length' else 'no_final_answer'
            print(f'[llm_researcher] Final JSON unavailable: {reason}.')
            return None
        return content or None
    except Exception as e:
        print(f"[llm_researcher] {PROVIDER} call failed: {type(e).__name__}")
        return None


# ---------------------------------------------------------------
# Robust JSON parsing — tolerates truncation, code fences, prose preambles,
# trailing commas, unterminated strings. Critical for deepseek-reasoner
# whose long internal reasoning can occasionally truncate the answer.
# ---------------------------------------------------------------
# DeepSeek sometimes leaks its reasoning (including draft JSON) into the
# answer field, ending it with this marker. Only text after the LAST marker is
# the final answer; a draft before it must never be parsed as the result.
_END_OF_THINKING = re.compile(r"<\s*[|｜]\s*end[\s_▁]of[\s_▁]thinking\s*[|｜]\s*>|</think>", re.I)


def _final_answer(raw: str) -> str:
    parts = _END_OF_THINKING.split(raw or "")
    return parts[-1]


def _strip_fences(raw: str) -> str:
    """Strip markdown code fences and any prose preamble before the JSON."""
    raw = _final_answer(raw).strip()
    if not raw:
        return ""
    # Triple-backtick fences
    if raw.startswith("```"):
        # ```json ... ``` or ``` ... ```
        m = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, re.DOTALL)
        if m:
            raw = m.group(1).strip()
        else:
            raw = raw.lstrip("`").lstrip("json").strip()
    # Drop everything before the first '{' — some models add a preamble
    first_brace = raw.find("{")
    if first_brace > 0:
        raw = raw[first_brace:]
    return raw


def _save_debug(raw: str, label: str, exc: Exception | None = None):
    """Persist a failed LLM response for offline inspection."""
    try:
        logs_dir = Path(r"C:\Trading\logs")
        logs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = logs_dir / f"llm_debug_{label}_{ts}.txt"
        with path.open("w", encoding="utf-8") as f:
            if exc is not None:
                f.write(f"# Parse error: {exc}\n# {'='*60}\n")
            f.write(raw or "")
        print(f"[llm_researcher] Raw response saved to {path}")
    except Exception:
        pass


def _repair_truncated_json(raw: str) -> str:
    """
    Best-effort repair for common deepseek-reasoner truncation modes:

      • Unterminated string at end → close the quote
      • Missing closing brackets / braces → infer from open count
      • Trailing comma before close → drop it

    Always returns a string. Caller still needs to try json.loads on it
    and fall back if parse still fails.
    """
    if not raw:
        return raw
    s = raw.rstrip()
    # Strip a dangling trailing comma
    s = re.sub(r",\s*$", "", s)

    # Walk the string tracking string-state and bracket depth, skipping
    # escaped characters. Anything we don't see closed by the end gets
    # auto-closed.
    in_string = False
    escape = False
    stack = []  # list of '{' or '['
    for ch in s:
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack and ((ch == "}" and stack[-1] == "{") or (ch == "]" and stack[-1] == "[")):
                stack.pop()

    # Close any open string first
    if in_string:
        s += '"'
    # Drop trailing comma that may now precede an inferred close
    s = re.sub(r",\s*$", "", s)
    # Close brackets in reverse open order
    for opener in reversed(stack):
        s += "}" if opener == "{" else "]"
    return s


def _extract_partial_ranked(raw: str) -> list[dict]:
    """
    Last-resort: scrape every well-formed ranked entry from a corrupt response.

    Looks for objects of the shape
        {"symbol": "...", ...}
    and parses each one independently. Anything that fails to parse is
    skipped silently. Used when both direct parse + repair both fail —
    we still salvage whatever ranked entries the model managed to write.
    """
    out: list[dict] = []
    # Find every {...} object that contains a "symbol" key (greedy nested-safe)
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(raw):
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                snippet = raw[start:i+1]
                if '"symbol"' in snippet:
                    try:
                        obj = json.loads(snippet)
                        if isinstance(obj, dict) and obj.get("symbol"):
                            out.append(obj)
                    except Exception:
                        pass
                start = -1
    return out


def _robust_parse(raw: str, *, label: str) -> dict | None:
    """
    Three-stage parse:
      1. Strip fences/preamble, try json.loads directly
      2. Repair common truncation, try json.loads again
      3. Salvage any well-formed {symbol,...} objects into a synthetic
         {"ranked": [...]} dict
    On total failure, save the raw response to logs/llm_debug_<label>_<ts>.txt
    and return None so the caller can fall back to rule-based ordering.
    """
    cleaned = _strip_fences(raw)
    if not cleaned:
        if (raw or "").strip():
            print(f"[llm_researcher] {label}: reasoning only, no final JSON after it")
            _save_debug(raw, label)
        return None

    # Stage 1
    try:
        return json.loads(cleaned)
    except Exception as e1:
        first_err = e1

    # Stage 2 — repair
    try:
        repaired = _repair_truncated_json(cleaned)
        result = json.loads(repaired)
        print(f"[llm_researcher] {label}: repaired truncated JSON ({first_err})")
        return result
    except Exception:
        pass

    # Stage 3 — salvage individual entries
    salvaged = _extract_partial_ranked(cleaned)
    if salvaged:
        print(
            f"[llm_researcher] {label}: salvaged {len(salvaged)} entries "
            f"from corrupt response (original error: {first_err})"
        )
        return {"ranked": salvaged, "summary": ""}

    # Total failure — persist for debugging
    print(f"[llm_researcher] {label}: JSON parse failed ({first_err}) — saving raw response")
    _save_debug(raw, label, first_err)
    return None


def _call_llm(prompt: str, *, timeout: float | None = None) -> str | None:
    options = {'timeout': timeout} if timeout else {}
    if PROVIDER == "claude":
        return _call_anthropic(prompt, **options)
    if PROVIDER == "deepseek":
        return _call_openai_compat(prompt, base_url="https://api.deepseek.com/v1", **options)
    if PROVIDER == "openai":
        return _call_openai_compat(prompt, **options)
    return None


# ---------------------------------------------------------------
# Main entry — called by session_runner before sending Telegram
# ---------------------------------------------------------------
def research_candidates(candidates: list[dict], market_context: dict,
                        max_send: int | None = None) -> list[dict]:
    """
    Returns candidates re-ranked by LLM (if enabled), or unchanged.

    Each input candidate dict should include:
      symbol, score, weighted_score, min_score_required, setup_type,
      reasons, warnings, entry_price, stop_loss, take_profit, external

    Output: same list, re-ordered, with optional 'llm_rationale' field
    on each candidate. If LLM is disabled or call fails, returns the
    input unchanged so the rule-based ordering is preserved.
    """
    if not is_enabled() or not candidates:
        return candidates

    print(f"[llm_researcher] Calling {PROVIDER} ({MODEL or DEFAULT_MODELS[PROVIDER]}) "
          f"to research {len(candidates)} candidates...")
    prompt = _build_prompt(candidates, market_context)
    raw = _call_llm(prompt)
    if not raw:
        return candidates

    data = _robust_parse(raw, label="equity")
    if not data:
        return candidates

    ranked = data.get("ranked") or []
    if not ranked:
        return candidates

    # Build a lookup by symbol → rank info
    rank_by_sym = {}
    for r in ranked:
        sym = (r.get("symbol") or "").upper()
        if sym:
            rank_by_sym[sym] = r

    # Annotate originals with rationale + sort by LLM rank
    out = []
    for c in candidates:
        sym = (c.get("symbol") or "").upper()
        info = rank_by_sym.get(sym, {})
        if info:
            c["llm_rank"]      = info.get("rank", 99)
            c["llm_rationale"] = info.get("rationale") or ""
            c["llm_concerns"]  = info.get("concerns")  or ""
            c["llm_veto"]      = bool(info.get("veto_flag", False))
            # v5.5 — only set llm_send_ok when the model actually returned it,
            # so the caller's default (True for rubric names, False for
            # news-driven) applies when the field is absent.
            if "send_ok" in info:
                c["llm_send_ok"] = bool(info.get("send_ok"))
        else:
            c["llm_rank"] = 99
            c["llm_veto"] = False
        out.append(c)

    out.sort(key=lambda x: x.get("llm_rank", 99))

    summary = data.get("summary", "")
    if summary:
        print(f"[llm_researcher] Summary: {summary}")

    if max_send is not None:
        out = out[:max_send]

    return out


# ---------------------------------------------------------------
# v5.6 — Full-watchlist triage (deep_scan.py)
# ---------------------------------------------------------------
def _build_triage_prompt(factsheets: list[dict], market_context: dict) -> str:
    lines = []
    for fs in factsheets:
        parts = [f"### {fs.get('symbol')}"]
        mom = []
        for k, label in (("ret_1w_pct", "1w"), ("ret_1m_pct", "1m"),
                         ("ret_3m_pct", "3m"), ("dist_60d_high_pct", "vs 60d-high")):
            v = fs.get(k)
            if v is not None:
                mom.append(f"{label} {v:+.1f}%")
        if mom:
            parts.append("  Momentum: " + " | ".join(mom))
        tech = []
        if fs.get("trend"):
            tech.append(f"trend {fs['trend']}")
        if fs.get("rsi"):
            tech.append(f"RSI {fs['rsi']}")
        if fs.get("vol_ratio"):
            tech.append(f"vol {fs['vol_ratio']}x avg")
        tech.append("passed trend rubric" if fs.get("passed_rubric")
                    else "FAILED trend rubric")
        parts.append("  Technicals: " + ", ".join(tech))
        f = fs.get("fundamentals")
        if f:
            fp = []
            if f.get("fcf_ttm") is not None:
                v = f["fcf_ttm"]
                fp.append(f"FCF {('$%.1fB' % (v/1e9)) if abs(v) >= 1e9 else ('$%.0fM' % (v/1e6))}")
            if f.get("fcf_yoy_pct") is not None:
                fp.append(f"FCF YoY {f['fcf_yoy_pct']:+.0f}%")
            if f.get("fcf_yield_pct") is not None:
                fp.append(f"FCF yield {f['fcf_yield_pct']:+.1f}%")
            if f.get("net_debt_to_ebitda") is not None:
                fp.append(f"ND/EBITDA {f['net_debt_to_ebitda']:.1f}x")
            if fp:
                parts.append(f"  Fundamentals: " + " | ".join(fp)
                             + f"  [{f.get('quality') or ''}]")
        pc = fs.get("options_positioning")
        if pc:
            parts.append(f"  US-chain USD context: P/C OI {pc['pc_ratio']} (direction unknown)"
                         + (f", USD max pain ${pc['max_pain']:.0f} exp {pc.get('expiry')}"
                            if pc.get("max_pain") else ""))
        if fs.get("catalyst"):
            parts.append(f"  Catalyst: {fs['catalyst']}")
        if fs.get("news"):
            parts.append(f"  News: {fs['news']}")
        lines.append("\n".join(parts))

    return f"""You are a Canadian-market (TSX/TSXV) trading analyst doing a FIRST-PASS TRIAGE
of an entire watchlist — large caps and small caps treated EQUALLY. For each
name below, decide the most probable direction over the next 1-3 weeks using
ALL the evidence: momentum, trend, fundamentals (cash flow quality, leverage),
options open-interest context (P/C does not identify buying, selling, hedging or direction),
catalysts and news.

Market context:
{format_market_context(market_context)}
Canadian conditions govern this watchlist. US readings are supporting context.

Watchlist factsheets:

{chr(10).join(lines)}

Rules:
- Do NOT favour large caps. A small cap with strong momentum + improving cash
  flow outranks a flat mega-cap. US-chain USD levels are not Canadian levels.
- "FAILED trend rubric" is NOT disqualifying — a fresh catalyst, a turnaround
  in fundamentals can justify LONG anyway, subject to mandatory blockers.
- SHORT means you expect the name to FALL (used for PUT option signals).
  Only call SHORT on real evidence (deteriorating fundamentals, broken trend,
  weak operating outlook) — never from P/C alone or because a strong stock looks "extended".
- conviction: 5 = act today, 4 = strong, 3 = interesting, 1-2 = noise.
  Be selective: at most ~6 names should get conviction >= 4.
- NONE for everything without a clear edge. Most names should be NONE.

Respond with ONLY this JSON (no markdown fences):
{{
  "verdicts": [
    {{"symbol": "XYZ", "direction": "LONG|SHORT|NONE", "conviction": 1, "reason": "<= 15 words"}}
  ],
  "summary": "<= 30 words on where the best opportunities are>"
}}
Include EVERY symbol exactly once."""


def triage_watchlist(factsheets: list[dict], market_context: dict, *, timeout: float | None = None) -> list[dict]:
    """
    First-pass direction triage over factsheets (see deep_scan.py).
    Returns the 'verdicts' list, or [] on any failure.
    """
    if not is_enabled() or not factsheets:
        return []
    print(f"[llm_researcher] Triage: {PROVIDER} over {len(factsheets)} factsheets...")
    prompt = _build_triage_prompt(factsheets, market_context)
    raw = _call_llm(prompt, **({'timeout': timeout} if timeout else {}))
    if not raw:
        return []
    data = _robust_parse(raw, label="triage")
    if not data:
        return []
    # _robust_parse stage-3 salvage returns entries under "ranked"
    verdicts = data.get("verdicts") or data.get("ranked") or []
    summary = data.get("summary", "")
    if summary:
        print(f"[llm_researcher] Triage summary: {summary}")
    return verdicts


# ---------------------------------------------------------------
# Options research (separate prompt — options-specific context)
# ---------------------------------------------------------------
def _build_options_prompt(candidates: list[dict], market_context: dict) -> str:
    """
    Build a research prompt specifically for CALL/PUT signal candidates.

    The LLM is asked to evaluate:
      • Direction validity (is the CALL/PUT technically well-grounded?)
      • Delta quality (ATM/OTM/ITM suitability for a directional play)
      • DTE adequacy (enough time for the underlying move to materialise?)
      • IV level (cheap or rich premium relative to expected move?)
      • Gamma risk (dangerous pin/acceleration near expiry?)
      • Beta/Alpha alignment (does stock risk profile match the direction?)
      • Any red flags (regime mismatch, earnings proximity, high volatility)
    """
    blocks = []
    for c in candidates:
        sym   = c.get("symbol", "?")
        otype = c.get("option_type", "?")
        iv    = c.get("implied_vol")
        delta = c.get("delta")
        gamma = c.get("gamma")
        beta  = c.get("beta")
        alpha = c.get("alpha")
        pc    = c.get("pc_ratio")
        mp    = c.get("max_pain")
        pw    = c.get("put_wall")
        cw    = c.get("call_wall")
        previous_close = c.get("spot_reference") == "previous_completed_close"
        price_label = "Previous completed close (not current spot)" if previous_close else "Spot"

        lines = [
            f"### {sym} — {otype}",
            f"  Strike: ${c.get('strike', 0):.2f}  |  Expiry: {c.get('expiry','?')} ({c.get('dte','?')}d)  |  {price_label}: ${c.get('spot', 0):.2f}",
            f"  Est. premium: ${c.get('est_premium') or 0:.2f}/share",
        ]
        if previous_close:
            lines.append("  Strike-selection reference only; current moneyness is unverified.")
        greek_parts = []
        if delta  is not None: greek_parts.append(f"Δ={delta:+.3f}")
        if gamma  is not None: greek_parts.append(f"Γ={gamma:.5f}")
        if iv     is not None: greek_parts.append(f"IV={iv*100:.1f}%")
        if beta   is not None: greek_parts.append(f"β={beta:.2f}")
        if alpha  is not None: greek_parts.append(f"α={alpha:+.1f}%/yr")
        if greek_parts:
            lines.append("  Greeks: " + "  |  ".join(greek_parts))
        if mp or pw or cw:
            lines.append(
                f"  Max pain: ${mp or 0:.2f}  |  Put wall: ${pw or 0:.2f}  |  Call wall: ${cw or 0:.2f}"
            )
        if pc is not None:
            lines.append(f"  P/C OI ratio: {pc:.2f} (context only, not direction)")
        if c.get("reasons"):
            lines.append("  Basis: " + "; ".join(c["reasons"][:4]))
        if c.get("warnings"):
            lines.append("  Warnings: " + "; ".join(c["warnings"][:3]))
        # v5.1 — fundamentals on the underlying (passed via candidate dict)
        fund = c.get("fundamentals") or {}
        if fund.get("available"):
            fparts = []
            if fund.get("fcf_ttm") is not None:
                fcf = fund["fcf_ttm"]
                fcf_str = f"${fcf/1e9:.2f}B" if abs(fcf) >= 1e9 else f"${fcf/1e6:.0f}M"
                yoy = fund.get("fcf_yoy_pct")
                fparts.append(f"FCF {fcf_str}" + (f" ({yoy:+.0f}% YoY)" if yoy is not None else ""))
            if fund.get("fcf_yield_pct") is not None:
                fparts.append(f"yield {fund['fcf_yield_pct']:+.2f}%")
            if fund.get("net_debt_to_ebitda") is not None:
                fparts.append(f"ND/EBITDA {fund['net_debt_to_ebitda']:.2f}x")
            if fparts:
                lines.append("  Underlying fund: " + " | ".join(fparts) + f"  [{fund.get('quality_tag','')}]")
        blocks.append("\n".join(lines))

    return f"""You are a Canadian options trading analyst. Below are directional CALL/PUT
signal candidates on TSX-listed stocks with native Canadian contracts. Each
passed a quantitative technical rubric. Do a final research pass evaluating:

  1. Is the directional call (CALL/PUT) genuinely well-supported by the
     underlying technicals? Could the direction reverse quickly?
  2. Assess only the quoted strike and supplied Greeks. Do not assume it is ATM
     or infer current moneyness from a previous-close reference. Missing data stays unknown.
  3. DTE: does {chr(10)}the time horizon give the underlying enough room to move?
     Too short = theta decay kills the trade. Too long = more expensive.
  4. IV level: is implied volatility low (cheap to buy) or high (overpriced)?
     Compare to typical volatility for the stock.
  5. Gamma profile: high gamma at low DTE can cause rapid delta swings — is
     this acceptable for a directional bet?
  6. Beta/Alpha: does the stock's market sensitivity and recent performance
     confirm the directional bias?
  7. Red flags: regime mismatch, sector headwinds, earnings risk, VIX spike.

MARKET CONTEXT:
{format_market_context(market_context)}

CANDIDATES ({len(candidates)}):

{chr(10).join(blocks)}

OUTPUT JSON ONLY (no other text), schema:
{{
  "ranked": [
    {{
      "symbol": "TICKER",
      "option_type": "CALL",
      "rank": 1,
      "rationale": "1-2 sentences: why this is the strongest options setup — cite specific Greeks, fundamentals if relevant, or context",
      "concerns": "any red flags, time-decay risk, IV issues, or direction doubts",
      "veto_flag": false
    }},
    ...
  ],
  "summary": "1-2 sentences on overall options slate quality and whether the regime favours buying options today"
}}

Set "veto_flag": true ONLY when:
  • The directional thesis is fundamentally contradicted by underlying data
    (e.g. CALL on a name with FCF down 30% and downgrade cascade,
     PUT on a name with strong cashflow growth and no negative catalyst)
  • IV is so high the trade is structurally unfavorable
  • Earnings risk within DTE window with high IV crush probability

veto_flag=true triggers a soft veto: card still sent but with "⚠️ AI flagged"
badge and 1-star confidence drop. Use sparingly.

Rank ALL candidates from best (1) to worst. Be specific — mention delta, DTE,
IV richness, fundamentals where relevant, and whether the underlying move is
likely within the expiry window.
"""


def research_options_candidates(
    candidates: list[dict],
    market_context: dict,
) -> list[dict]:
    """
    Re-rank options CALL/PUT candidates using the configured LLM.

    Each input candidate dict should have:
      symbol, option_type, strike, expiry, dte, spot, est_premium,
      delta, gamma, implied_vol, beta, alpha, pc_ratio, max_pain,
      put_wall, call_wall, reasons, warnings

    Returns the same list re-ordered (best first) with 'llm_rank',
    'llm_rationale', and 'llm_concerns' fields added to each item.
    Falls back to the original order unchanged if LLM is disabled or fails.
    """
    if not is_enabled() or not candidates:
        return candidates

    model_name = MODEL or DEFAULT_MODELS.get(PROVIDER, PROVIDER)
    print(
        f"[llm_researcher] Options research: calling {PROVIDER} ({model_name}) "
        f"for {len(candidates)} candidate(s)..."
    )
    prompt = _build_options_prompt(candidates, market_context)
    raw = _call_llm(prompt)
    if not raw:
        return candidates

    data = _robust_parse(raw, label="options")
    if not data:
        return candidates

    ranked = data.get("ranked") or []
    if not ranked:
        return candidates

    # Build lookup by (symbol, option_type) — both CALL and PUT on the same
    # underlying are theoretically possible so use the compound key.
    rank_by_key: dict = {}
    for r in ranked:
        sym = (r.get("symbol") or "").upper()
        ot  = (r.get("option_type") or "").upper()
        if sym:
            rank_by_key[f"{sym}_{ot}"] = r

    out = []
    for c in candidates:
        sym = (c.get("symbol") or "").upper()
        ot  = (c.get("option_type") or "").upper()
        info = rank_by_key.get(f"{sym}_{ot}") or rank_by_key.get(sym, {})
        if info:
            c["llm_rank"]      = info.get("rank", 99)
            c["llm_rationale"] = info.get("rationale") or ""
            c["llm_concerns"]  = info.get("concerns") or ""
            c["llm_veto"]      = bool(info.get("veto_flag", False))
        else:
            c["llm_rank"] = 99
            c["llm_veto"] = False
        out.append(c)

    out.sort(key=lambda x: x.get("llm_rank", 99))

    summary = data.get("summary", "")
    if summary:
        print(f"[llm_researcher] Options summary: {summary}")

    return out


def format_llm_footer(candidate: dict) -> str:
    """Telegram-card footer fragment with LLM rationale (only if present)."""
    if not candidate.get("llm_rationale"):
        return ""
    parts = [f"\n<b>🧠 AI Research:</b>\n  • {candidate['llm_rationale']}"]
    if candidate.get("llm_concerns"):
        parts.append(f"  • <i>Concerns: {candidate['llm_concerns']}</i>")
    return "\n".join(parts) + "\n"
