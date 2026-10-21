"""
ownership_data.py
-----------------
Lightweight yfinance wrapper for ownership data — institutional %,
insider %, and top holders. Free, no scraping. Returns {} on any error.

Called only for shortlisted candidates (after rubric pass) to keep
yfinance request count low.
"""
from __future__ import annotations
import html
import warnings
warnings.filterwarnings("ignore")


# Dual-listing fallback (some Canadian names — yfinance returns better
# ownership data via the US ticker)
US_DUAL = {
    "RY": "RY", "TD": "TD", "BMO": "BMO", "BNS": "BNS", "CM": "CM", "NA": "NA",
    "SU": "SU", "CNQ": "CNQ", "ENB": "ENB", "TRP": "TRP", "CVE": "CVE",
    "MFC": "MFC", "SLF": "SLF",
    "SHOP": "SHOP",
    "NTR": "NTR", "AEM": "AEM",
    "ABX": "GOLD", "CCO": "CCJ", "TECK.B": "TECK",
    "WCN": "WCN", "GIB.A": "GIB",
    "CNR": "CNI", "CP": "CP", "BCE": "BCE", "T": "TU",
}


def get_ownership(symbol: str, currency: str = "CAD") -> dict:
    """
    Returns {institutional_pct, insider_pct, top_holders[], src} or {}.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}

    suffix = ".TO" if currency.upper() == "CAD" else ""
    candidates = [f"{symbol}{suffix}"] if suffix else [symbol]
    if currency.upper() == "CAD" and symbol in US_DUAL:
        if US_DUAL[symbol] not in candidates:
            candidates.append(US_DUAL[symbol])

    for cand in candidates:
        try:
            t = yf.Ticker(cand)
            mh = t.major_holders
        except Exception:
            continue
        if mh is None or len(mh) == 0:
            continue

        out = {"symbol": symbol, "src": cand}

        # major_holders is a DataFrame with columns ['Value', 'Breakdown']
        # Or a 2-column df with percentage strings
        try:
            for _, row in mh.iterrows():
                vals = row.tolist()
                if len(vals) < 2:
                    continue
                pct_str = str(vals[0])
                label   = str(vals[1]).lower()
                # Try parsing percentage
                pct = None
                try:
                    pct = float(pct_str.replace("%", "").strip()) / 100.0
                except Exception:
                    pass
                if pct is None:
                    continue
                if "insider" in label:
                    out["insider_pct"] = pct
                elif "institution" in label:
                    out["institutional_pct"] = pct
                elif "float" in label and "institution" in label:
                    out["institutional_float_pct"] = pct
        except Exception:
            pass

        # Try newer yfinance schema where info has held_percent_*
        try:
            info = t.info or {}
            if "heldPercentInsiders" in info and "insider_pct" not in out:
                out["insider_pct"] = float(info["heldPercentInsiders"])
            if "heldPercentInstitutions" in info and "institutional_pct" not in out:
                out["institutional_pct"] = float(info["heldPercentInstitutions"])
        except Exception:
            pass

        # Top institutional holders (best-effort, yfinance often returns this only for US tickers)
        try:
            ih = t.institutional_holders
            if ih is not None and len(ih):
                top = []
                for _, row in ih.head(3).iterrows():
                    name = str(row.get("Holder") or "").strip()
                    pct  = row.get("pctHeld") or row.get("% Out") or row.get("pct_held")
                    try:
                        pct = float(pct) / (100.0 if (pct is not None and abs(float(pct)) > 1) else 1.0)
                    except Exception:
                        pct = None
                    if name:
                        top.append({"name": name, "pct": pct})
                if top:
                    out["top_holders"] = top
        except Exception:
            pass

        if out.get("institutional_pct") is not None or out.get("insider_pct") is not None:
            return out
        # Otherwise try next candidate

    return {}


def format_ownership_lines(own: dict) -> list[str]:
    """Telegram card lines for ownership data. Empty if nothing useful."""
    if not own:
        return []
    lines = []
    inst = own.get("institutional_pct")
    ins  = own.get("insider_pct")
    if inst is not None:
        lines.append(f"  • Institutional: <b>{inst*100:.1f}%</b>")
    if ins is not None:
        lines.append(f"  • Insider: <b>{ins*100:.1f}%</b>")
    top = own.get("top_holders") or []
    if top:
        bits = []
        for h in top[:2]:
            n = h.get("name", "")
            p = h.get("pct")
            if n:
                # Holder names such as "Dodge & Cox" must be escaped for HTML cards.
                bits.append(html.escape(str(n), quote=False) + (f" {p*100:.1f}%" if p is not None else ""))
        if bits:
            lines.append(f"  • Top: " + ", ".join(bits))
    return lines


if __name__ == "__main__":
    import sys, json
    sym = sys.argv[1] if len(sys.argv) > 1 else "RY"
    print(json.dumps(get_ownership(sym), indent=2, default=str))
