"""
fundamentals.py
---------------
Lightweight fundamentals fetcher for TSX-listed Canadian names.

Pulls from yfinance only — free, no API key. Computes TTM FCF, YoY growth,
FCF yield (FCF/market cap), and Net Debt / EBITDA. Returns a normalized
dict per ticker plus a coarse quality tag (🟢/🟡/🔴) and a flags list.

Used as a soft-overlay layer:
  • The card formatter displays the numbers + quality tag.
  • llm_researcher feeds the numbers to DeepSeek for synthesis.
  • Soft-veto: if DeepSeek concerns contain red-flag keywords, the card
    is still sent but with a ⚠️ AI flagged badge and 1-star confidence drop.

Intentionally simple — no analyst targets here (already in
session_runner's external.yf block via yfinance.Ticker.info), no DCF
fair-value model. Keep it boring.

USAGE
-----
    from fundamentals import fetch_fundamentals
    data = fetch_fundamentals("RY")   # accepts plain ticker; we add .TO
    if data and data["available"]:
        print(data["fcf_ttm"], data["fcf_yoy_pct"], data["quality_tag"])

NEVER called for the full 69-name watchlist — only the shortlist that
survives technical scoring. yfinance is rate-limited and slow.
"""
from __future__ import annotations
import math
import warnings
warnings.filterwarnings("ignore")

# Yahoo column-name variants we look for. Yahoo is inconsistent across
# tickers (especially Canadian banks vs energy vs miners) so we accept
# any of these as the "free cash flow" row.
FCF_ROWS = [
    "Free Cash Flow",
    "FreeCashFlow",
    "free_cash_flow",
]

# Operating cash flow fallback rows (used when FCF row missing — common
# for Canadian banks where Yahoo doesn't compute FCF)
OCF_ROWS = [
    "Operating Cash Flow",
    "Total Cash From Operating Activities",
    "Cash Flow From Continuing Operating Activities",
    "OperatingCashFlow",
]

# Capex rows (subtract from OCF if computing FCF ourselves)
CAPEX_ROWS = [
    "Capital Expenditure",
    "Capital Expenditures",
    "CapitalExpenditures",
]

EBITDA_ROWS = [
    "EBITDA",
    "Normalized EBITDA",
]

TOTAL_DEBT_ROWS = [
    "Total Debt",
    "TotalDebt",
]

CASH_ROWS = [
    "Cash And Cash Equivalents",
    "Cash And Short Term Investments",
    "CashAndCashEquivalents",
]


def _get_row(df, candidates):
    """Return the first matching row (Series) from df.index or None."""
    if df is None or df.empty:
        return None
    idx = list(df.index)
    for cand in candidates:
        for ix in idx:
            if str(ix).strip().lower() == cand.strip().lower():
                return df.loc[ix]
    return None


def _safe_sum(values):
    """Sum a list/Series of values, treating NaN/None as zero. Returns None if all missing."""
    if values is None:
        return None
    out = 0.0
    found = False
    for v in values:
        try:
            f = float(v)
            if math.isnan(f):
                continue
            out += f
            found = True
        except Exception:
            continue
    return out if found else None


def _compute_fcf_from_components(cashflow_df, periods=4):
    """
    Compute FCF = OCF - |Capex| for the most recent N periods.
    Used when Yahoo doesn't expose a 'Free Cash Flow' row directly.
    """
    ocf = _get_row(cashflow_df, OCF_ROWS)
    capex = _get_row(cashflow_df, CAPEX_ROWS)
    if ocf is None:
        return None
    # Take first N columns (most recent quarters/years)
    ocf_vals = list(ocf.iloc[:periods])
    if capex is not None:
        capex_vals = list(capex.iloc[:periods])
        # Capex is typically negative on the cashflow statement; we add it
        # algebraically (so OCF + negative_capex = OCF - |capex|).
        return _safe_sum(
            [(o or 0) + (c or 0) for o, c in zip(ocf_vals, capex_vals)]
        )
    return _safe_sum(ocf_vals)


def _ttm_value(df, candidates, periods=4):
    """Sum the most recent N periods for a row. None on missing."""
    row = _get_row(df, candidates)
    if row is None:
        return None
    return _safe_sum(list(row.iloc[:periods]))


def _classify_quality(fcf_yoy_pct, fcf_yield_pct, net_debt_to_ebitda):
    """
    Coarse quality tag from the three core ratios:
      🟢 strong   = FCF growing, decent yield, low leverage
      🟡 mixed   = at least one weak metric
      🔴 weak    = multiple red flags or negative FCF
    """
    flags = []
    score = 0  # +ve = better

    if fcf_yoy_pct is not None:
        if fcf_yoy_pct > 10: score += 2
        elif fcf_yoy_pct > 0: score += 1
        elif fcf_yoy_pct < -20:
            score -= 2
            flags.append(f"FCF down {fcf_yoy_pct:.0f}% YoY")
        elif fcf_yoy_pct < 0:
            score -= 1
            flags.append(f"FCF declining ({fcf_yoy_pct:+.0f}% YoY)")

    if fcf_yield_pct is not None:
        if fcf_yield_pct > 8: score += 2
        elif fcf_yield_pct > 4: score += 1
        elif fcf_yield_pct < 0:
            score -= 2
            flags.append(f"Negative FCF yield ({fcf_yield_pct:+.1f}%)")
        elif fcf_yield_pct < 2:
            flags.append(f"Low FCF yield ({fcf_yield_pct:.1f}%)")

    if net_debt_to_ebitda is not None:
        if net_debt_to_ebitda < 1.5: score += 1
        elif net_debt_to_ebitda > 4.0:
            score -= 2
            flags.append(f"High leverage (ND/EBITDA {net_debt_to_ebitda:.1f}x)")
        elif net_debt_to_ebitda > 3.0:
            score -= 1
            flags.append(f"Moderate leverage (ND/EBITDA {net_debt_to_ebitda:.1f}x)")

    if score >= 3:
        tag = "🟢"
    elif score >= 0:
        tag = "🟡"
    else:
        tag = "🔴"
    return tag, flags


def fetch_fundamentals(symbol: str, exchange_suffix: str = ".TO") -> dict | None:
    """
    Fetch + normalize fundamentals for a TSX-listed name.

    Parameters
    ----------
    symbol           : plain ticker as it appears in the watchlist (e.g.
                       "RY", "RCI.A", "TECK.B"). Class-A/B and TSXV
                       resolution is handled by yf_symbols.to_yf_ticker.
    exchange_suffix  : LEGACY argument — retained for backwards-compat.
                       ".TO" means infer via resolver (default).
                       "" forces no suffix (US names).

    Returns
    -------
    dict with keys:
      available, ticker_used, fcf_ttm, fcf_prev_ttm, fcf_yoy_pct,
      fcf_yield_pct, net_debt_to_ebitda, ebitda_ttm, market_cap,
      quality_tag, flags, source

    Returns None if yfinance is not installed.
    Returns {"available": False, ...} when data unavailable for the name.
    """
    try:
        import yfinance as yf
    except ImportError:
        return None

    # v5.4.2: route via resolver. Empty suffix → US (no rewrite). Anything
    # else → assume CAD and let resolver pick the right TSX/TSXV variant.
    if exchange_suffix == "":
        ticker_str = symbol.upper()
    else:
        from yf_symbols import to_yf_ticker
        ticker_str = to_yf_ticker(symbol, currency="CAD")
    out = {
        "available": False,
        "ticker_used": ticker_str,
        "fcf_ttm": None,
        "fcf_prev_ttm": None,
        "fcf_yoy_pct": None,
        "fcf_yield_pct": None,
        "net_debt_to_ebitda": None,
        "ebitda_ttm": None,
        "market_cap": None,
        "quality_tag": "⚪",
        "flags": [],
        "source": "yfinance",
    }

    try:
        t = yf.Ticker(ticker_str)
        cf  = t.quarterly_cashflow            # quarterly cashflow statement
        bs  = t.quarterly_balance_sheet       # quarterly balance sheet
        fi  = t.quarterly_financials          # quarterly income statement
        cfa = t.cashflow                      # annual cashflow (fallback for YoY)
        info = t.info or {}
    except Exception as e:
        out["flags"].append(f"yfinance fetch error: {e}")
        return out

    if cf is None or cf.empty:
        out["flags"].append("No quarterly cashflow data")
        return out

    # ---------- FCF ----------
    fcf_row = _get_row(cf, FCF_ROWS)
    if fcf_row is not None:
        fcf_ttm      = _safe_sum(list(fcf_row.iloc[:4]))
        fcf_prev_ttm = _safe_sum(list(fcf_row.iloc[4:8])) if len(fcf_row) >= 8 else None
    else:
        fcf_ttm      = _compute_fcf_from_components(cf.iloc[:, :4], periods=4)
        fcf_prev_ttm = (_compute_fcf_from_components(cf.iloc[:, 4:8], periods=4)
                        if cf.shape[1] >= 8 else None)

    # ---------- YoY fallback via ANNUAL cashflow ----------
    # Quarterly data often only goes back 5 quarters, so the 4-quarter-prior
    # window is incomplete. Use annual data (years -1 vs -2) as YoY proxy.
    if fcf_prev_ttm is None and cfa is not None and not cfa.empty:
        fcf_row_a = _get_row(cfa, FCF_ROWS)
        if fcf_row_a is not None and len(fcf_row_a) >= 2:
            # iloc[0] = most recent FY, iloc[1] = prior FY
            try:    fcf_prev_ttm = float(fcf_row_a.iloc[1])
            except: pass
            # Use annual current-year as TTM when quarterly missing
            if fcf_ttm is None:
                try:    fcf_ttm = float(fcf_row_a.iloc[0])
                except: pass
        elif fcf_row_a is None:
            # Compute from annual components
            try:
                fcf_ttm_a = _compute_fcf_from_components(cfa.iloc[:, :1], periods=1)
                fcf_prev_a = (_compute_fcf_from_components(cfa.iloc[:, 1:2], periods=1)
                              if cfa.shape[1] >= 2 else None)
                if fcf_ttm is None: fcf_ttm = fcf_ttm_a
                if fcf_prev_ttm is None: fcf_prev_ttm = fcf_prev_a
            except Exception:
                pass

    out["fcf_ttm"]      = fcf_ttm
    out["fcf_prev_ttm"] = fcf_prev_ttm
    if fcf_ttm is not None and fcf_prev_ttm not in (None, 0):
        out["fcf_yoy_pct"] = round((fcf_ttm - fcf_prev_ttm) / abs(fcf_prev_ttm) * 100, 1)

    # ---------- Market cap + FCF yield ----------
    market_cap = info.get("marketCap")
    out["market_cap"] = market_cap
    if fcf_ttm is not None and market_cap and market_cap > 0:
        out["fcf_yield_pct"] = round(fcf_ttm / market_cap * 100, 2)

    # ---------- EBITDA + leverage ----------
    ebitda_ttm = _ttm_value(fi, EBITDA_ROWS, periods=4)
    out["ebitda_ttm"] = ebitda_ttm

    total_debt = None
    cash = None
    if bs is not None and not bs.empty:
        td_row = _get_row(bs, TOTAL_DEBT_ROWS)
        cs_row = _get_row(bs, CASH_ROWS)
        # Most recent column = most recent quarter
        if td_row is not None and len(td_row) > 0:
            try:    total_debt = float(td_row.iloc[0])
            except: total_debt = None
        if cs_row is not None and len(cs_row) > 0:
            try:    cash = float(cs_row.iloc[0])
            except: cash = None

    if total_debt is not None and cash is not None and ebitda_ttm and ebitda_ttm > 0:
        net_debt = total_debt - cash
        out["net_debt_to_ebitda"] = round(net_debt / ebitda_ttm, 2)

    # ---------- Quality tag ----------
    tag, flags = _classify_quality(
        out["fcf_yoy_pct"], out["fcf_yield_pct"], out["net_debt_to_ebitda"]
    )
    out["quality_tag"] = tag
    out["flags"] = flags

    # Available if we got at least one of the three core metrics
    out["available"] = any([
        out["fcf_ttm"] is not None,
        out["fcf_yield_pct"] is not None,
        out["net_debt_to_ebitda"] is not None,
    ])
    return out


def format_fundamentals_block(f: dict, currency: str = "CAD") -> str:
    """
    Render a 3-4 line plain-text block for inclusion in a Telegram card.
    Caller is responsible for wrapping in <b> headers etc.
    """
    if not f or not f.get("available"):
        return "  • (fundamentals not available)\n"
    cs = "C$" if currency.upper() == "CAD" else "US$"
    lines = []
    # FCF line
    if f["fcf_ttm"] is not None:
        # Display in millions or billions
        fcf = f["fcf_ttm"]
        if abs(fcf) >= 1e9:
            fcf_str = f"{cs}{fcf/1e9:.2f}B"
        else:
            fcf_str = f"{cs}{fcf/1e6:.0f}M"
        yoy = f["fcf_yoy_pct"]
        yoy_str = f" ({yoy:+.0f}% YoY)" if yoy is not None else ""
        lines.append(f"  • FCF (TTM):      {fcf_str}{yoy_str}")
    if f["fcf_yield_pct"] is not None:
        lines.append(f"  • FCF yield:      {f['fcf_yield_pct']:+.2f}%")
    if f["net_debt_to_ebitda"] is not None:
        lev = f["net_debt_to_ebitda"]
        verdict = "healthy" if lev < 2 else "moderate" if lev < 4 else "high"
        lines.append(f"  • Net debt/EBITDA: {lev:.2f}x ({verdict})")
    lines.append(f"  • Quality:        {f['quality_tag']}")
    if f["flags"]:
        lines.append("  • Flags:          " + "; ".join(f["flags"][:2]))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import sys, json
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else "CNQ"
    print(f"Fetching fundamentals for {sym}...")
    d = fetch_fundamentals(sym)
    if d is None:
        print("yfinance not installed")
        sys.exit(1)
    # Pretty
    for k, v in d.items():
        if isinstance(v, float):
            print(f"  {k:22} {v:,.2f}")
        else:
            print(f"  {k:22} {v}")
    print("\nCard block preview:\n")
    print(format_fundamentals_block(d, currency="CAD"))
