"""
mx_options.py
-------------
IBKR-Gateway-based fetcher for Montreal Exchange (CDE) option chains in CAD.

Provides a primary, CAD-native source for the options pipeline. When this
fails (no CDE listing for the symbol, no subscription, gateway error) the
caller falls back to the yfinance US-dual-listed chain via options_signals.

Public functions
----------------
fetch_chain_metadata(ib, symbol)
    Returns dict with normalized chain metadata (expiries, strikes,
    exchange, tradingClass, multiplier, underlying_conid, currency="CAD")
    or None.

fetch_contract_snapshot(ib, symbol, expiry_yyyymmdd, strike, right,
                        trading_class, multiplier, exchange="CDE")
    Returns dict with bid/ask/last/close/model_price/delta/gamma/iv (all
    CAD) or None. bid/ask may be -1 without an active CDE options
    market-data subscription — model_price / close are reliable.

get_usdcad_rate(ib)
    Returns live USD->CAD FX rate via IDEALPRO ticker, or None.

Notes
-----
- Uses delayed-frozen market data (reqMarketDataType(3)) by default; this is
  enough for greeks/close. Live bid/ask requires a CDE-options subscription.
- All prices and strikes are in CAD on this exchange.
- 0DTE is left to the caller's existing _pick_expiry filter — this module
  does not filter expiries.
"""
from __future__ import annotations
from datetime import datetime
from ib_insync import IB, Stock, Option, Forex


def _normalize_expiry(yyyymmdd: str) -> str:
    """20260529 -> 2026-05-29 to match yfinance format."""
    try:
        return datetime.strptime(yyyymmdd, "%Y%m%d").strftime("%Y-%m-%d")
    except Exception:
        return yyyymmdd


def _denormalize_expiry(yyyy_mm_dd: str) -> str:
    """2026-05-29 -> 20260529 for IBKR API."""
    try:
        return datetime.strptime(yyyy_mm_dd, "%Y-%m-%d").strftime("%Y%m%d")
    except Exception:
        return yyyy_mm_dd.replace("-", "")


def _ensure_delayed_data(ib: IB) -> None:
    """
    Ensure delayed-frozen data mode is enabled before any option request.
    Without this, CDE option market-data requests return Error 354 on
    paper accounts that lack the live CDE-options subscription. Idempotent.
    """
    try:
        ib.reqMarketDataType(3)
    except Exception:
        pass


def fetch_chain_metadata(ib: IB, symbol: str) -> dict | None:
    """
    Fetch CDE option-chain metadata for a TSX symbol.
    Returns None if no CDE listing or gateway error.
    """
    try:
        _ensure_delayed_data(ib)
        stk = Stock(symbol, "SMART", "CAD")
        qualified = ib.qualifyContracts(stk)
        if not qualified:
            return None
        stk = qualified[0]

        params = ib.reqSecDefOptParams(
            underlyingSymbol=stk.symbol,
            futFopExchange="",
            underlyingSecType=stk.secType,
            underlyingConId=stk.conId,
        )
        if not params:
            return None

        # Prefer CDE (Montreal Exchange). Fall back to any CAD-denominated
        # listing only if nothing on CDE.
        cde = next((p for p in params if p.exchange in ("CDE", "MX")), None)
        if cde is None:
            return None

        return {
            "currency": "CAD",
            "source": "MX",
            "exchange": cde.exchange,
            "trading_class": cde.tradingClass,
            "multiplier": cde.multiplier,
            "underlying_conid": stk.conId,
            "expiries_yyyymmdd": sorted(cde.expirations),
            "expiries": [_normalize_expiry(e) for e in sorted(cde.expirations)],
            "strikes": sorted(cde.strikes),
        }
    except Exception as e:
        print(f"  [mx] fetch_chain_metadata({symbol}) error: {e}")
        return None


def fetch_contract_snapshot(
    ib: IB,
    symbol: str,
    expiry_yyyymmdd: str,
    strike: float,
    right: str,                 # "C" or "P"
    trading_class: str,
    multiplier: str = "100",
    exchange: str = "CDE",
    wait_seconds: float = 4.0,
) -> dict | None:
    """
    Qualify a single CDE option contract and snap a quote.
    Returns dict with CAD-denominated fields, or None on failure.
    """
    try:
        _ensure_delayed_data(ib)
        opt = Option(symbol, expiry_yyyymmdd, strike, right, exchange,
                     tradingClass=trading_class, currency="CAD",
                     multiplier=multiplier)
        q = ib.qualifyContracts(opt)
        if not q:
            # Try without currency constraint
            opt = Option(symbol, expiry_yyyymmdd, strike, right, exchange,
                         tradingClass=trading_class, multiplier=multiplier)
            q = ib.qualifyContracts(opt)
        if not q:
            return None
        opt = q[0]

        t = ib.reqMktData(opt, "", False, False)
        ib.sleep(wait_seconds)

        import math as _math
        def _clean(v):
            if v is None: return None
            try:
                f = float(v)
                if _math.isnan(f) or f == -1.0:
                    return None
                return f
            except Exception:
                return None

        mg = getattr(t, "modelGreeks", None)
        snap = {
            "currency": "CAD",
            "exchange": opt.exchange,
            "conid": opt.conId,
            "bid":   _clean(t.bid),
            "ask":   _clean(t.ask),
            "last":  _clean(t.last),
            "close": _clean(t.close),
            "delta": _clean(getattr(mg, "delta", None)) if mg else None,
            "gamma": _clean(getattr(mg, "gamma", None)) if mg else None,
            "iv":    _clean(getattr(mg, "impliedVol", None)) if mg else None,
            "model_price": _clean(getattr(mg, "optPrice", None)) if mg else None,
            "und_price":   _clean(getattr(mg, "undPrice", None)) if mg else None,
        }
        try:
            ib.cancelMktData(opt)
        except Exception:
            pass
        return snap
    except Exception as e:
        print(f"  [mx] snapshot({symbol} {expiry_yyyymmdd} {strike}{right}) error: {e}")
        return None


def get_usdcad_rate(ib: IB) -> float | None:
    """Live USD/CAD FX rate via IDEALPRO. Returns mid, or None on failure."""
    try:
        fx = Forex("USDCAD")
        ib.qualifyContracts(fx)
        t = ib.reqMktData(fx, "", False, False)
        ib.sleep(2)
        rate = None
        if t.bid and t.ask and t.bid > 0 and t.ask > 0:
            rate = (t.bid + t.ask) / 2
        elif t.last and t.last > 0:
            rate = t.last
        elif t.close and t.close > 0:
            rate = t.close
        try:
            ib.cancelMktData(fx)
        except Exception:
            pass
        return float(rate) if rate else None
    except Exception:
        # yfinance fallback
        try:
            import yfinance as yf
            h = yf.Ticker("USDCAD=X").history(period="2d")
            if not h.empty:
                return float(h["Close"].iloc[-1])
        except Exception:
            pass
        return None


def pick_atm_strike(strikes: list[float], spot: float, right: str) -> float | None:
    """
    Choose the CDE strike nearest to spot.
    Note: CDE strikes are union across all expiries. If the chosen strike
    isn't listed for the chosen expiry, fetch_contract_snapshot will fail
    and the caller should retry with the next nearest strike.
    """
    if not strikes or spot <= 0:
        return None
    if right.upper() == "C":
        above = [s for s in strikes if s >= spot]
        return min(above) if above else max(strikes)
    else:
        below = [s for s in strikes if s <= spot]
        return max(below) if below else min(strikes)


def nearest_alternate_strikes(strikes: list[float], target: float, n: int = 4) -> list[float]:
    """Return the n strikes closest to target, sorted by distance."""
    if not strikes:
        return []
    return sorted(strikes, key=lambda s: abs(s - target))[:n]
