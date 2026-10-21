"""
news_analyzer.py
----------------
Ticker / alias / sector-keyword matcher for incoming news messages.
Used by news_listener.py.

Given a Telegram message body, returns a list of (ticker, match_type)
hits restricted to the user's Canadian watchlist or known sectors.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------
# Watchlist + alias map
# ---------------------------------------------------------------
WATCHLIST_FILE = Path(r"C:\Trading\watchlist.txt")


def load_watchlist() -> list[str]:
    if not WATCHLIST_FILE.exists():
        return []
    out = []
    for line in WATCHLIST_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line.upper())
    return out


# Common-name → ticker map (extend as needed)
ALIAS_TO_TICKER = {
    # Banks
    "royal bank":        "RY",   "royal bank of canada": "RY",
    "td bank":           "TD",   "toronto-dominion":     "TD",  "td":  "TD",
    "bmo":               "BMO",  "bank of montreal":     "BMO",
    "cibc":              "CM",   "canadian imperial":    "CM",
    "scotiabank":        "BNS",  "bank of nova scotia":  "BNS",
    "national bank":     "NA",
    "manulife":          "MFC",
    "sun life":          "SLF",
    # Energy
    "suncor":            "SU",
    "canadian natural":  "CNQ",  "cnrl":                 "CNQ",
    "cenovus":           "CVE",
    "enbridge":          "ENB",
    "tourmaline":        "TOU",
    "arc resources":     "ARX",
    "parex":             "PRL",
    "prairie sky":       "PSK",
    "paramount resources": "POU",
    # Miners / gold
    "agnico":            "AEM",  "agnico eagle":         "AEM",
    "barrick":           "ABX",  "barrick gold":         "ABX",
    "b2gold":            "BTO",
    "lundin mining":     "LUN",
    "ivanhoe":           "IVN",
    "centerra":          "CG",
    "algoma steel":      "ASTL",
    "russel metals":     "RUS",
    "foran mining":      "FOM",
    "rock tech":         "RCK",
    # Tech
    "constellation software": "CSU",
    "kinaxis":           "KXS",
    "lightspeed":        "LMN",  # mostly: LSPD or LMN — context-dependent
    "celestica":         "CLS",
    "shopify":           "SHOP",
    "open text":         "OTEX",
    # Telecom
    "bce":               "BCE",  "bell canada":          "BCE",
    "telus":             "T",
    "rogers":            "RCI.A","rogers communications":"RCI.A",
    # Rails
    "canadian national": "CNR",  "cn rail":              "CNR",
    "canadian pacific":  "CP",   "cp rail":              "CP",
    # Other
    "nutrien":           "NTR",
    "couche-tard":       "ATD",
    "weyerhaeuser":      "WFG",  "west fraser":          "WFG",
    "teck":              "TECK.B",
    "air canada":        "AC",
    "cameco":            "CCO",
    "nexgen":            "NXE",
    "denison":           "DML",
}

# Sector keywords → list of tickers most exposed
SECTOR_KEYWORDS = {
    "rate cut":           ["RY", "TD", "BMO", "CM", "BNS", "NA", "EQB", "MFC", "SLF", "GSY"],
    "rate hike":          ["RY", "TD", "BMO", "CM", "BNS", "NA", "EQB", "MFC", "SLF", "GSY"],
    "bank of canada":     ["RY", "TD", "BMO", "CM", "BNS", "NA", "EQB", "MFC", "SLF"],
    "boc":                ["RY", "TD", "BMO", "CM", "BNS", "NA", "EQB", "MFC", "SLF"],
    "interest rate":      ["RY", "TD", "BMO", "CM", "BNS", "NA", "EQB", "MFC", "SLF", "GSY"],
    "oil prices":         ["SU", "CNQ", "CVE", "ENB", "TOU", "ARX", "POU", "PSK", "EOG", "PRL"],
    "crude oil":          ["SU", "CNQ", "CVE", "ENB", "TOU", "ARX", "POU", "PSK", "EOG", "PRL"],
    "natural gas":        ["TOU", "ARX", "POU", "PRL", "CNQ", "CVE"],
    "opec":               ["SU", "CNQ", "CVE", "ENB", "TOU", "ARX", "POU", "PSK", "EOG", "PRL"],
    "gold rally":         ["AEM", "ABX", "BTO", "LUN", "IVN", "AII", "RIC", "CG"],
    "gold prices":        ["AEM", "ABX", "BTO", "LUN", "IVN", "AII", "RIC", "CG"],
    "base metals":        ["TECK.B", "LUN", "IVN", "MX", "RUS", "ASTL", "FOM", "RCK"],
    "copper":             ["TECK.B", "LUN", "IVN", "FOM"],
    "steel":              ["ASTL", "RUS"],
    "uranium":            ["CCO", "NXE", "DML", "U.UN", "UCU"],
    "lumber":             ["WFG", "RUS"],
    "housing":            ["EQB", "GSY", "MEQ", "PRL"],
    "inflation":          ["MFC", "SLF", "RY", "TD", "BMO"],  # banks + insurers
    "cpi":                ["MFC", "SLF", "RY", "TD", "BMO"],
}


def match_message(text: str) -> list[tuple[str, str]]:
    """
    Returns a list of (ticker, match_type) tuples found in `text`.
    match_type ∈ {"ticker", "alias", "sector"}.
    Deduplicates tickers, preferring ticker > alias > sector match types.
    """
    if not text:
        return []
    lower = text.lower()
    upper_text = " " + text.upper() + " "

    hits: dict[str, str] = {}

    # 1. Direct ticker match (uppercase, word-boundaried)
    watchlist = load_watchlist()
    watchlist_set = set(watchlist)
    for tk in watchlist:
        # Allow tickers like "TECK.B" — escape regex chars.
        # IMPORTANT: Single-letter tickers (e.g. "T") cause many false positives
        # (e.g. "T-Mobile"). Only match them in explicit finance formats.
        if len(tk) == 1:
            # Accept: "$T", "TSX:T", "T.TO", "(T)" but NOT "T-" prefixes.
            pat = re.escape(tk)
            if re.search(rf"(\${pat}\b|\bTSX:{pat}\b|\b{pat}\.TO\b|\({pat}\))", upper_text):
                hits[tk] = "ticker"
            continue

        pat = re.escape(tk)
        # Exclude common hyphenated-word false positives for short tickers (e.g. "AB-" patterns).
        if re.search(rf"(?<![A-Z]){pat}(?![A-Z0-9])", upper_text):
            hits[tk] = "ticker"

    # 2. Alias match (lowercased phrase)
    for alias, tk in ALIAS_TO_TICKER.items():
        if tk not in watchlist_set:
            continue
        # For short aliases (<=3 chars), require word boundaries to avoid
        # accidental matches (e.g. "TD" in "The Daily T", "BMO" inside longer tokens).
        if len(alias) <= 3:
            if re.search(rf"\b{re.escape(alias)}\b", lower):
                if tk not in hits:
                    hits[tk] = "alias"
        else:
            if alias in lower:
                if tk not in hits:
                    hits[tk] = "alias"

    # 3. Sector keyword match (broadcasts to a list)
    for kw, tickers in SECTOR_KEYWORDS.items():
        if kw in lower:
            for tk in tickers:
                if tk not in watchlist_set:
                    continue
                if tk not in hits:
                    hits[tk] = "sector"

    return list(hits.items())


# ---------------------------------------------------------------
# Sentiment classification (simple keyword-based)
# ---------------------------------------------------------------
BULLISH = [
    "beat", "beats estimates", "earnings beat", "raised guidance",
    "upgrade", "upgraded", "buyback", "share repurchase", "acquisition",
    "merger", "approval", "fda approval", "contract win", "record results",
    "all-time high", "rally", "surge", "soar", "strong demand",
]
BEARISH = [
    "miss", "missed estimates", "earnings miss", "downgrade", "downgraded",
    "lawsuit", "investigation", "probe", "guidance cut", "lowered guidance",
    "halt", "halted", "restructuring", "layoffs", "ceo steps down",
    "fraud", "going concern", "bankruptcy", "recall", "delist",
    "plunge", "tumble", "slump", "warning",
]


def sentiment(text: str) -> int:
    """+1 bullish, -1 bearish, 0 neutral."""
    if not text:
        return 0
    lower = text.lower()
    pos = sum(1 for kw in BULLISH if kw in lower)
    neg = sum(1 for kw in BEARISH if kw in lower)
    if pos > neg:
        return 1
    if neg > pos:
        return -1
    return 0


# ---------------------------------------------------------------
# Quick CLI test
# ---------------------------------------------------------------
if __name__ == "__main__":
    import sys
    sample = " ".join(sys.argv[1:]) or (
        "Bank of Canada cuts interest rate 0.25%, banks rally; "
        "Suncor reports earnings beat. Investigation into Bombardier announced."
    )
    print("Text:", sample)
    print("Matches:", match_message(sample))
    print("Sentiment:", sentiment(sample))
