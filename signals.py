"""
signals.py
----------
Signal scoring + formatting layer.

IMPORTANT: Named `signals.py` (plural). Do NOT rename to `signal.py` -
that would shadow Python's stdlib `signal` module and break asyncio / ib_insync.

Given an OHLCV DataFrame + a timeframe tag, produces a scored signal and
optionally pushes it to Telegram.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from indicators import add_all, latest_snapshot
import telegram_notify
import filters


Timeframe = Literal[
    "INTRADAY", "1-DAY", "2-DAY", "1-WEEK", "2-WEEK", "1-MONTH", "3-MONTH"
]

TIMEFRAME_RULES = {
    "INTRADAY": {"tp": 0.05, "sl": 0.025, "min_score": 5, "min_history_days": 30},
    "1-DAY": {"tp": 0.05, "sl": 0.025, "min_score": 5, "min_history_days": 30},
    "2-DAY": {"tp": 0.06, "sl": 0.03, "min_score": 5, "min_history_days": 30},
    "1-WEEK": {"tp": 0.08, "sl": 0.04, "min_score": 5, "min_history_days": 60},
    "2-WEEK": {"tp": 0.12, "sl": 0.06, "min_score": 6, "min_history_days": 90},
    "1-MONTH": {"tp": 0.15, "sl": 0.07, "min_score": 6, "min_history_days": 120},
    "3-MONTH": {"tp": 0.25, "sl": 0.12, "min_score": 6, "min_history_days": 200},
}


CHECK_WEIGHTS = {
    "trend_short": 1.0,
    "trend_long": 1.0,
    "rsi_zone": 1.0,
    "macd_cross": 1.25,
    "macd_above": 1.0,
    "volume": 1.0,
    "bollinger": 0.75,
    "support": 1.0,
    "breakout": 1.25,
    "golden_cross": 0.75,
    "rs_vs_xic": 0.75,
    "adx_bonus": 0.5,
    "weekly_trend": 1.0,
    "news_boost": 0.5,
    "insider": 0.5,
    "options_bullish": 0.5,
    "futures_basis": 0.75, # Added for US lead-lag funding
}


@dataclass
class Signal:
    symbol: str
    action: str
    timeframe: str
    score: int
    weighted_score: float
    min_score_required: int
    confidence: int
    entry_price: float
    qty: int
    stop_loss: float
    take_profit: float
    currency: str
    setup_type: str = ""
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    indicators: dict = field(default_factory=dict)
    sendable: bool = False
    hard_blockers: list[str] = field(default_factory=list)


def _infer_setup_type(
    close: float,
    snap: dict,
    *,
    rsi_ok: bool,
    macd_ok: bool,
    vol_ratio: float | None,
    breakout_ok: bool,
    support_ok: bool,
) -> str:
    vol_ok = vol_ratio is not None and not math.isnan(vol_ratio) and vol_ratio > 1.2

    if breakout_ok and macd_ok:
        return "Breakout"
    if support_ok and rsi_ok and snap["trend"] == "bullish":
        return "Pullback"
    if close > snap["ema_50"] > snap["ema_200"] and macd_ok and vol_ok:
        return "Trend"
    if snap.get("golden_cross"):
        return "Recovery"
    return "Mixed"


def _confidence_from_signal(raw_score: int, weighted_score: float, min_score: int, setup_type: str) -> int:
    confidence = 1
    if raw_score >= min_score:
        confidence += 1
    if raw_score >= min_score + 1:
        confidence += 1
    if weighted_score >= min_score + 1.5:
        confidence += 1
    if setup_type in {"Breakout", "Pullback", "Trend"}:
        confidence += 1
    return max(1, min(5, confidence))


def score_symbol(
    df: pd.DataFrame,
    symbol: str,
    timeframe: Timeframe = "1-DAY",
    capital_per_position: float = 4000.0,
    currency: str = "CAD",
    ensure_indicators: bool = True,
    *,
    regime: str = "",
    gold_tag: str = "",
    xic_df: pd.DataFrame | None = None,
    weekly_df: pd.DataFrame | None = None,
    wti_pct_change: float | None = None,
    sector_etf_pct_change: dict | None = None,
    vix_level: float | None = None,
    news_events: list[dict] | None = None,
    snapshot_quote: dict | None = None,
    market: dict | None = None,
    use_atr_exits: bool = True,
    enforce_time_window: bool = False,
    adx_mode: str = "off",
    adx_threshold: float = 20.0,
    atr_sl_mult: float = 2.0,
    atr_tp_mult: float = 3.0,
    skip_external_io: bool = False,
) -> Signal:
    rules = TIMEFRAME_RULES[timeframe]
    sig = Signal(
        symbol=symbol,
        action="HOLD",
        timeframe=timeframe,
        score=0,
        weighted_score=0.0,
        min_score_required=rules["min_score"],
        confidence=0,
        entry_price=0.0,
        qty=0,
        stop_loss=0.0,
        take_profit=0.0,
        currency=currency,
    )

    def block(reason):
        sig.hard_blockers.append(reason)
        sig.warnings.append(reason)
        sig.sendable = False
        return sig

    minimum_bars = max(250, rules["min_history_days"])
    if df is None or df.empty or len(df) < minimum_bars:
        return block(f"Not enough history ({len(df) if df is not None else 0} bars, need {minimum_bars})")

    if ensure_indicators and "ema_50" not in df.columns:
        df = add_all(df)

    snap = latest_snapshot(df)
    sig.indicators = {k: v for k, v in snap.items() if not isinstance(v, bool)}
    close = snap["close"]
    if not math.isfinite(close) or close <= 0:
        return block("Invalid underlying price")

    tradability = snapshot_quote or {"last": close, "volume": float(df["volume"].iloc[-1])}
    ok, reasons = filters.passes_tradability(tradability)
    if not ok:
        for reason in reasons:
            block(f"Tradability: {reason}")
        return sig

    if enforce_time_window:
        ok, why = filters.in_signal_window()
        if not ok:
            return block(f"Time window: {why}")

    ok, why = filters.volatility_regime_ok(vix_level)
    if not ok:
        return block(f"Vol regime: {why}")

    ok, why = filters.oil_gate_for_energy(symbol, wti_pct_change)
    if not ok:
        return block(f"Energy gate: {why}")

    ok, sector_penalty, why = filters.sector_strength_ok(symbol, sector_etf_pct_change)
    if not ok:
        return block(f"Sector gate: {why}")
    if sector_penalty > 0 and why:
        sig.warnings.append(why)

    news_delta, news_headline = filters.news_sentiment(news_events or [], symbol)
    if news_delta < 0:
        return block(f"News block: {news_headline}")

    if not skip_external_io:
        has_earn, earn_date = filters.has_earnings_within(symbol, days=3, currency=currency)
        if has_earn:
            return block(f"Earnings within 3 trading sessions ({earn_date}) - blackout")
        if not earn_date:
            sig.warnings.append("Earnings date unavailable; verify before acting")

    # Gate 8: Climax filter (v4.8) — price too far above EMA-50 = chasing
    # the top. 18% over the medium-term mean is the threshold; backtest
    # losers were often climax buys that immediately reverted.
    ema_50 = snap.get("ema_50")
    if ema_50 and not math.isnan(ema_50) and ema_50 > 0:
        ext_pct = (close - ema_50) / ema_50
        if ext_pct > 0.18:
            return block(
                f"Climax: price {close:.2f} is {ext_pct*100:.1f}% above EMA-50 {ema_50:.2f} - overextended"
            )

    checks_passed: list[tuple[str, str, float]] = []
    core_checks = 0
    rsi_ok = False
    macd_ok = False
    breakout_ok = False
    support_ok = False

    def add_check(key: str, label: str, *, core: bool = False):
        nonlocal core_checks
        checks_passed.append((key, label, CHECK_WEIGHTS[key]))
        if core:
            core_checks += 1

    if close > snap["ema_50"]:
        add_check("trend_short", f"Price {close:.2f} > 50 EMA {snap['ema_50']:.2f} (short-term uptrend)", core=True)

    if close > snap["ema_200"]:
        add_check("trend_long", f"Price > 200 EMA {snap['ema_200']:.2f} (long-term uptrend)", core=True)

    # v4.8 — Trend-mode RSI: in clean uptrends (price > EMA-50, MACD > signal),
    # RSI naturally lives in 50-70 during continuation phases. Old fixed 30-55
    # window blocks every trend-continuation entry. Pullback mode keeps the
    # original sector-calibrated zone.
    rsi_v = snap["rsi_14"]
    in_uptrend = (close > snap["ema_50"]
                  and not math.isnan(snap["macd"])
                  and snap["macd"] > snap["macd_signal"])
    if in_uptrend:
        rsi_low, rsi_high = 50.0, 70.0
        rsi_mode = "continuation"
    else:
        rsi_low, rsi_high = filters.rsi_zone_for(symbol)  # banks 35-60, default 30-55
        rsi_mode = "pullback"
    if rsi_low <= rsi_v <= rsi_high:
        rsi_ok = True
        add_check("rsi_zone",
                  f"RSI {rsi_v:.1f} in {rsi_mode} zone ({rsi_low:.0f}-{rsi_high:.0f})",
                  core=True)
    elif rsi_v < rsi_low:
        sig.warnings.append(f"RSI {rsi_v:.1f} below {rsi_mode} zone {rsi_low:.0f}")
    elif rsi_v > rsi_high:
        sig.warnings.append(f"RSI {rsi_v:.1f} above {rsi_mode} zone {rsi_high:.0f}")

    if snap["macd_bull_cross"]:
        macd_ok = True
        add_check("macd_cross", "MACD bullish cross within last 3 bars", core=True)
    elif snap["macd"] > snap["macd_signal"]:
        macd_ok = True
        add_check("macd_above", "MACD above signal line (momentum positive)", core=True)

    vol_r = snap["vol_ratio"]
    if not math.isnan(vol_r) and vol_r > 1.2:
        add_check("volume", f"Volume {vol_r:.2f}x 20-day avg (strong participation)", core=True)

    bb_pb = snap["bb_percent_b"]
    if not math.isnan(bb_pb) and bb_pb < 0.95:
        add_check("bollinger", f"Bollinger %B {bb_pb:.2f} - room before upper band", core=True)
    elif bb_pb >= 0.95:
        sig.warnings.append(f"Bollinger %B {bb_pb:.2f} - overextended")

    support = snap["support"]
    if support > 0:
        dist_from_support = (close - support) / support
        if 0 < dist_from_support < 0.05:
            support_ok = True
            add_check("support", f"Near support {support:.2f} ({dist_from_support*100:.1f}% above)", core=True)

    # v4.8 — Breakouts demand stronger volume confirmation than pullbacks (1.5x
    # vs 1.2x). Weak-volume breakouts are the classic TSX false-breakout pattern.
    res_prev = snap.get("resistance_prev") or 0.0
    if res_prev and close > res_prev:
        if vol_r is not None and not math.isnan(vol_r) and vol_r > 1.5:
            breakout_ok = True
            add_check("breakout", f"Breakout above prior resistance {res_prev:.2f} with strong volume ({vol_r:.2f}x)")

    if snap["golden_cross"]:
        add_check("golden_cross", "Golden cross (EMA-50 over EMA-200 recently)")

    rs = filters.relative_strength_vs_xic(df, xic_df) if xic_df is not None else None
    if rs is not None and rs > 0:
        add_check("rs_vs_xic", f"RS vs XIC: +{rs*100:.1f}% (outperforming TSX)")
    elif rs is not None and rs < -0.05:
        sig.warnings.append(f"RS vs XIC: {rs*100:.1f}% (lagging TSX)")

    adx_v = snap.get("adx_14")
    if adx_v is not None and not math.isnan(adx_v):
        if adx_mode == "gate" and adx_v < adx_threshold:
            return block(f"ADX {adx_v:.1f} < {adx_threshold:.0f} - trend too weak")
        if adx_mode == "bonus" and adx_v >= adx_threshold:
            add_check("adx_bonus", f"ADX {adx_v:.1f} >= {adx_threshold:.0f} (strong trend)")
        elif adx_mode == "bonus" and adx_v < 15:
            sig.warnings.append(f"ADX {adx_v:.1f} - chop (no clear trend)")

    if weekly_df is not None and len(weekly_df) > 50:
        weekly_close = float(weekly_df["close"].iloc[-1])
        weekly_ema50 = float(weekly_df["close"].ewm(span=50, adjust=False).mean().iloc[-1])
        if weekly_close > weekly_ema50:
            add_check("weekly_trend", "Weekly trend bullish (close > weekly EMA-50)")
        else:
            sig.warnings.append("Weekly trend bearish - daily setup against larger timeframe")

    if news_delta > 0 and news_headline:
        add_check("news_boost", f"Bullish news: {news_headline[:80]}")

    # v5.2 — Futures Basis (Funding) Check
    # If /NQ futures are outperforming QQQ spot, it indicates aggressive institutional bias.
    # v4.3 fix: 'market' is the dict of regime data passed in by session_runner;
    # earlier this incorrectly called .get on the imported `indicators` MODULE,
    # which crashed any path that reached this line via the Yahoo fallback.
    if isinstance(market, dict):
        nq_f = market.get("nq_f_pct") or 0.0
        if nq_f > (market.get("qqq_pct") or 0.0) + 0.002:
            sig.indicators["us_futures_context"] = "NQ and QQQ returns use different session references; not a basis signal"

    # v4.9 — Options analysis (max pain / put wall / call wall).
    # Pulled only for survivors of all gates so we don't burn yfinance calls
    # on rejects. Skipped during backtest (skip_external_io=True) since
    # historical option chains aren't available via yfinance.
    options_min_score_penalty = 0
    options_data = None
    if not skip_external_io:
        try:
            import options_analyzer
            options_data = options_analyzer.analyze_options(symbol, currency=currency)
            if options_data and currency.upper() != "CAD":
                bonus_notes, penalty_notes = options_analyzer.options_signal_modifiers(
                    options_data, close
                )
                for note in bonus_notes:
                    add_check("options_bullish", f"Options: {note}")
                for note in penalty_notes:
                    sig.warnings.append(f"Options: {note}")
                    options_min_score_penalty += 1
        except Exception:
            options_data = None
    sig.indicators["options"] = options_data or {}

    # v5.0 — Insider buying as a +1 score bonus when meaningful (officer-level
    # buying or net $ > $250k in last 30 days). Selling is non-signal — no penalty.
    if not skip_external_io:
        try:
            import insider_transactions
            insider = insider_transactions.get_insider_summary(symbol, currency=currency)
            if insider:
                delta, note = insider_transactions.insider_signal_modifier(insider)
                if delta > 0 and note:
                    add_check("insider", f"Insider: {note}")  # dedicated weight (v5.5)
                sig.indicators["insider"] = insider
        except Exception:
            pass

    # v4.8 — BONUSES NOW COUNT. Previously sig.score = core_checks, which
    # meant breakout / golden_cross / RS / weekly_trend / news_boost / ADX
    # never helped a candidate reach min_score. That's why the rubric was
    # blocking too many high-context setups. Fix: score is total checks
    # passed (core + bonus). Sanity backstop: still require >= 3 of the 7
    # core technical checks so a name doesn't pass on bonuses alone.
    sig.score = len(checks_passed)
    sig.weighted_score = round(sum(weight for _, _, weight in checks_passed), 2)

    min_score = rules["min_score"] + sector_penalty
    atr_pen, atr_note = filters.atr_volatility_penalty(close, snap.get("atr_14"))
    if atr_pen > 0 and atr_note:
        sig.warnings.append(atr_note)
        min_score += atr_pen

    res_pen, res_note = filters.resistance_proximity_penalty(close, snap.get("resistance_prev"))
    if res_pen > 0 and res_note:
        sig.warnings.append(res_note)
        min_score += res_pen

    if regime and "BEARISH" in regime.upper():
        min_score += 1

    # v4.9 — options penalty (e.g. "near call wall, capped upside")
    if options_min_score_penalty > 0:
        min_score += options_min_score_penalty

    sig.min_score_required = min_score
    sig.setup_type = _infer_setup_type(
        close,
        snap,
        rsi_ok=rsi_ok,
        macd_ok=macd_ok,
        vol_ratio=vol_r,
        breakout_ok=breakout_ok,
        support_ok=support_ok,
    )

    sorted_checks = sorted(checks_passed, key=lambda item: (-item[2], item[0]))
    sig.reasons = [label for _, label, _ in sorted_checks[:6]]
    sig.confidence = _confidence_from_signal(sig.score, sig.weighted_score, min_score, sig.setup_type)

    # v4.7: REVERTED AI #2's hard structure gate. Setup-type and weighted-score
    # are kept on the Signal for display (Telegram card) but no longer block
    # the BUY decision. Backtest sweep showed pure count-rubric (min_score=5,
    # ATR 2.0/3.0, ADX off) had Calmar 5.11 — that's our reference.
    # AI #1's resistance-proximity SOFT penalty + breakout BONUS are kept
    # because they only nudge the score, never hard-block.
    if sig.setup_type == "Mixed" and not breakout_ok and not support_ok:
        sig.warnings.append("Setup mixed (no clean trend/pullback/breakout) — note only")

    # v4.8: require >= 3 core checks (of the 7-check technical rubric) so a
    # name can't qualify on bonuses alone. Bonuses help reach min_score but
    # there must be real technical structure underneath.
    if sig.score >= min_score and core_checks >= 3 and snap["trend"] != "bearish":
        sig.action = "BUY"
        sig.entry_price = round(close, 2)
        if use_atr_exits:
            atr_v = snap.get("atr_14")
            sig.stop_loss, sig.take_profit = filters.atr_based_exits(
                close, atr_v, sl_atr_mult=atr_sl_mult, tp_atr_mult=atr_tp_mult
            )
        else:
            sig.stop_loss = round(close * (1 - rules["sl"]), 2)
            sig.take_profit = round(close * (1 + rules["tp"]), 2)
        sig.qty = max(1, int(capital_per_position / close))
        sig.sendable = True
    else:
        if sig.score < min_score:
            sig.warnings.append(f"Score {sig.score}/{min_score} - below threshold for {timeframe}")
    if snap["trend"] == "bearish":
        block("Trend bearish (close < EMA-50 < EMA-200)")

    if snap["death_cross"]:
        block("Death cross (EMA-50 below EMA-200) - regime weakening")

    return sig


def maybe_send(
    signal: Signal,
    *,
    regime: str = "",
    gold_tag: str = "",
    target_date: str = "",
    note: str = "",
    force: bool = False,
    external: dict | None = None,
) -> bool:
    if not (signal.sendable or force):
        return False
    return telegram_notify.send_signal(
        action=signal.action,
        symbol=signal.symbol,
        timeframe=signal.timeframe,
        confidence=signal.confidence,
        entry_price=signal.entry_price,
        qty=signal.qty,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        reasons=signal.reasons,
        regime=regime,
        gold_tag=gold_tag,
        currency=signal.currency,
        target_date=target_date,
        note=note,
        setup_type=signal.setup_type,
        score=signal.score,
        weighted_score=signal.weighted_score,
        min_score_required=signal.min_score_required,
        external=external,
    )


if __name__ == "__main__":
    import json
    import sys

    from ibkr_client import IBKRClient

    symbol = sys.argv[1] if len(sys.argv) > 1 else "RY"
    timeframe = sys.argv[2] if len(sys.argv) > 2 else "1-WEEK"
    send_flag = "--send" in sys.argv
    currency = "USD" if "--usd" in sys.argv else "CAD"

    rules = TIMEFRAME_RULES[timeframe]
    duration = "2 Y"

    client = IBKRClient()
    client.connect()
    bars = client.get_historical_bars(symbol, duration, "1 day", currency)
    client.disconnect()

    df = pd.DataFrame(bars)
    sig = score_symbol(df, symbol=symbol, timeframe=timeframe, currency=currency)

    print(
        json.dumps(
            {
                "symbol": sig.symbol,
                "action": sig.action,
                "timeframe": sig.timeframe,
                "score": sig.score,
                "weighted_score": sig.weighted_score,
                "min_score_required": sig.min_score_required,
                "confidence": sig.confidence,
                "setup_type": sig.setup_type,
                "entry": sig.entry_price,
                "qty": sig.qty,
                "stop": sig.stop_loss,
                "tp": sig.take_profit,
                "sendable": sig.sendable,
                "reasons": sig.reasons,
                "warnings": sig.warnings,
            },
            indent=2,
        )
    )

    if send_flag and sig.sendable:
        maybe_send(sig, regime="(CLI test)", note="CLI test signal")
        print("\n[OK] Signal sent to Telegram.")
