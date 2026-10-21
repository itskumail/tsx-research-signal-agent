"""
yf_symbols.py
-------------
Single source of truth for converting a watchlist ticker (as it appears
in IBKR / watchlist.txt) into the correct yfinance ticker symbol.

yfinance is inconsistent about Canadian symbol conventions:
  • TSX (senior) names use the `.TO` suffix.
  • TSX Venture names use the `.V` suffix.
  • Class-A / Class-B shares use a DASH, not a dot:
      RCI.A  → RCI-A.TO   (Rogers Class A)
      CTC.A  → CTC-A.TO   (Canadian Tire Class A)
      TECK.B → TECK-B.TO  (Teck Resources Class B)
      TCL.A  → TCL-A.TO   (Transcontinental Class A)
  • Some names exist on TSXV but not TSX (juniors like RCK, PALS, LMN,
    UCU, EOG — note: NOT the US oil major).
  • Unitholder structures (e.g. U.UN) sometimes need `-UN.TO`.

This helper provides two API surfaces:

  to_yf_ticker(sym)
    → returns the primary best-guess yfinance ticker
       (e.g. 'RCI.A' → 'RCI-A.TO')

  yf_ticker_candidates(sym)
    → returns an ordered list of candidate tickers to try.
       For ambiguous TSXV names this includes the .V variant as fallback.
       Caller should iterate until one returns data.

Both are pure-function and have NO external dependencies (no yfinance,
no IBKR). Safe to import anywhere.
"""
from __future__ import annotations


# Symbols that live on TSX Venture (.V) rather than TSX (.TO).
# Hand-curated from the user's watchlist verification on 2026-05-23.
# Extend this set as new TSXV names are added to the watchlist.
KNOWN_TSXV = {
    "RCK",    # Rock Tech Lithium
    "PALS",   # Pacific Booker / similar TSXV junior
    "LMN",    # Lumine Group
    "UCU",    # Ucore Rare Metals
    "EOG",    # EOG Inc. — TSXV, NOT the US oil major
    "NDA",    # Neptune Digital Assets (v5.4.2: confirmed via re-run fallback)
    "TOI",    # Topicus.com (CSU spinoff, listed on TSXV)
    # Add others here as the watchlist grows
}


def _normalize_class_share(sym: str) -> str:
    """RCI.A → RCI-A,  TECK.B → TECK-B,  CTC.A → CTC-A.
    Leaves regular names like 'RY', 'CNQ', 'T' untouched.
    U.UN is handled separately (kept as-is so .TO append yields U-UN.TO via the dash sub)."""
    s = sym.strip().upper()
    if "." not in s:
        return s
    head, _, tail = s.rpartition(".")
    # Class share letters
    if tail in {"A", "B", "C"}:
        return f"{head}-{tail}"
    # Unit / trust suffixes
    if tail in {"UN", "PR"}:
        return f"{head}-{tail}"
    # Already an exchange suffix like .TO, .V — return as-is (shouldn't happen
    # for input from watchlist.txt, but defensive)
    if tail in {"TO", "V", "CN"}:
        return s
    return s


def to_yf_ticker(symbol: str, currency: str = "CAD") -> str:
    """
    Best-guess yfinance ticker for the given watchlist symbol.

    For known TSXV names, returns the `.V` variant.
    For Class A/B names, replaces the dot with a dash before appending.
    For everything else, appends the standard TSX `.TO`.

    USD-currency symbols are returned unchanged (no suffix).
    """
    if not symbol:
        return symbol
    s = symbol.strip().upper()

    # US names: no suffix
    if currency.upper() != "CAD":
        return s

    base = _normalize_class_share(s)
    # Original head (before any class-share rewrite) for the TSXV lookup
    head = s.split(".")[0] if "." in s else s
    if head in KNOWN_TSXV:
        return f"{base}.V"
    return f"{base}.TO"


def yf_ticker_candidates(symbol: str, currency: str = "CAD") -> list[str]:
    """
    Ordered candidate list for the symbol.

    Strategy:
      1. Primary guess (per to_yf_ticker — `.V` if KNOWN_TSXV, else `.TO`)
      2. The other exchange suffix as fallback (covers symbols not yet
         in KNOWN_TSXV that turn out to be TSXV-listed)

    The bare symbol is never a candidate: on Yahoo it is a US listing that
    can be a different company (T = AT&T not TELUS, CCO = Clear Channel not
    Cameco, MX = Magnachip not Methanex). Curated US dual listings belong to
    the caller's own explicit map.

    Caller tries each in order until one returns data.
    Class-share dash conversion is applied once and shared across all candidates.
    """
    if not symbol:
        return [symbol]
    s = symbol.strip().upper()

    if currency.upper() != "CAD":
        return [s]

    base = _normalize_class_share(s)
    primary = to_yf_ticker(symbol, currency)

    candidates = [primary]
    other_suffix = ".TO" if primary.endswith(".V") else ".V"
    other = f"{base}{other_suffix}"
    if other not in candidates:
        candidates.append(other)
    return candidates


# ---------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------
if __name__ == "__main__":
    cases = [
        ("RY", "RY.TO"),
        ("CNQ", "CNQ.TO"),
        ("RCI.A", "RCI-A.TO"),
        ("CTC.A", "CTC-A.TO"),
        ("TECK.B", "TECK-B.TO"),
        ("TCL.A", "TCL-A.TO"),
        ("U.UN", "U-UN.TO"),
        ("RCK", "RCK.V"),
        ("LMN", "LMN.V"),
        ("PALS", "PALS.V"),
        ("UCU", "UCU.V"),
        ("EOG", "EOG.V"),
    ]
    ok = 0
    for sym, expected in cases:
        got = to_yf_ticker(sym)
        marker = "OK " if got == expected else "FAIL"
        if got == expected:
            ok += 1
        print(f"  {marker}  {sym:8} -> {got:12} (expected {expected})")
    print(f"\n{ok}/{len(cases)} passed")
