"""
telegram_notify.py
------------------
Send trading signals and status messages to Telegram.
"""

from __future__ import annotations

import html
import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None


def _load_env(env_path: Path = Path(r"C:\Trading\.env")) -> dict:
    env = {}
    if not env_path.exists():
        return env
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


_env = _load_env()
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or _env.get("TELEGRAM_BOT_TOKEN")
CHATID = os.environ.get("TELEGRAM_CHAT_ID") or _env.get("TELEGRAM_CHAT_ID")


def esc(value) -> str:
    """HTML-escape dynamic text (news, model output, web data) for parse_mode=HTML."""
    return html.escape(str(value), quote=False) if value is not None else ""


_TELEGRAM_TAG = re.compile(r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|a|span|tg-spoiler|blockquote)(?:\s[^<>]*)?>", re.I)


def _plain_text(text: str) -> str:
    """Remove only Telegram formatting tags; keep stray '<' or '&' from data."""
    return html.unescape(_TELEGRAM_TAG.sub("", text))


def _is_entity_error(response) -> bool:
    if response.status_code != 400:
        return False
    try:
        description = str(response.json().get("description", ""))
    except Exception:
        return False
    return "can't parse entities" in description.lower()


def send(text: str, parse_mode: str = "HTML", disable_preview: bool = True) -> bool:
    if requests is None:
        print("ERROR: requests library not installed. Run: pip install requests")
        return False
    if not TOKEN or not CHATID:
        print("ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing from .env")
        return False

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHATID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_preview,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200 and r.json().get("ok"):
            return True
        # Telegram rejected the HTML before delivery, so one plain-text retry
        # cannot duplicate the message; an unescaped value no longer drops a card.
        if parse_mode == "HTML" and _is_entity_error(r):
            print("Telegram HTML rejected; retrying once as plain text")
            payload.update(text=_plain_text(text), parse_mode="")
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 200 and r.json().get("ok"):
                return True
        print(f"Telegram send failed: HTTP {r.status_code}")
        return False
    except Exception as e:
        # Request exceptions can include the bot-token URL and private payload.
        print(f"Telegram send exception: {type(e).__name__}")
        return False


def send_signal(
    action: str,
    symbol: str,
    *,
    timeframe: str = "1-DAY",
    confidence: int = 3,
    entry_type: str = "LIMIT",
    entry_price: float = 0.0,
    qty: int = 0,
    stop_loss: float = 0.0,
    take_profit: float = 0.0,
    target_date: str = "",
    reasons: list[str] | None = None,
    regime: str = "",
    gold_tag: str = "",
    currency: str = "CAD",
    note: str = "",
    setup_type: str = "",
    score: int | None = None,
    weighted_score: float | None = None,
    min_score_required: int | None = None,
    external: dict | None = None,
) -> bool:
    action_emoji = {"BUY": "🟢 BUY", "SELL": "🔴 SELL", "HOLD": "🟡 HOLD",
                    "EXIT": "⚪ EXIT"}.get(action.upper(), "🔔 SIGNAL")
    cf = max(1, min(5, confidence))
    stars = "★" * cf + "☆" * (5 - cf)
    tf_icon = {
        "INTRADAY": "⚡ INTRADAY",
        "1-DAY":    "📅 DAY",
        "2-DAY":    "📅 2-DAY",
        "1-WEEK":   "📆 WEEK",
        "2-WEEK":   "📆 2-WEEK",
        "1-MONTH":  "🗓 MONTH",
        "3-MONTH":  "🗓 3-MONTH",
    }.get(timeframe.upper(), "📅 DAY")

    # Reasons can carry news headlines ("S&P/TSX") or comparisons ("close < EMA").
    symbol, currency, target_date = esc(symbol), esc(currency), esc(target_date)
    setup_type, regime, gold_tag, note = esc(setup_type), esc(regime), esc(gold_tag), esc(note)
    reasons_txt = "\n".join(f"  • {esc(r)}" for r in (reasons or [])) or "  • (no detail)"
    entry_s = f"${entry_price:.2f}" if entry_price else "(see scan)"
    gross_s = f"${entry_price * qty:,.0f}" if entry_price and qty else "—"

    # Stop / target with distance from entry + R:R ratio
    if entry_price:
        if stop_loss:
            stop_pct = (stop_loss - entry_price) / entry_price * 100.0
            stop_s = f"${stop_loss:.2f}  ({stop_pct:+.2f}%)"
        else:
            stop_s = "—"
        if take_profit:
            tp_pct = (take_profit - entry_price) / entry_price * 100.0
            tp_s = f"${take_profit:.2f}  ({tp_pct:+.2f}%)"
        else:
            tp_s = "—"
        if stop_loss and take_profit and stop_loss < entry_price < take_profit:
            risk   = entry_price - stop_loss
            reward = take_profit - entry_price
            rr     = reward / risk if risk > 0 else 0
            rr_s   = f"R:R = <b>{rr:.2f}</b> ({reward/entry_price*100:.1f}% reward / {(-stop_pct):.1f}% risk)"
        else:
            rr_s = ""
    else:
        stop_s = f"${stop_loss:.2f}" if stop_loss else "—"
        tp_s   = f"${take_profit:.2f}" if take_profit else "—"
        rr_s   = ""

    score_line = ""
    if score is not None and min_score_required is not None:
        score_line = f"📊 <b>Score:</b> {score}/{min_score_required}"
        if weighted_score is not None:
            score_line += f"  · weighted <b>{weighted_score:.2f}</b>"
        score_line += "\n"

    msg = (
        f"<b>{action_emoji} {symbol}</b>  ({currency})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{tf_icon}"
        + (f"  · target: {target_date}" if target_date else "")
        + "\n"
        + (f"🎯 <b>Setup:</b> {setup_type}\n" if setup_type else "")
        + f"📈 <b>Confidence:</b> {stars} ({confidence}/5)\n"
        + score_line
        + (f"🌐 <b>Regime:</b> {regime}" + (f"  ·  {gold_tag}" if gold_tag else "") + "\n" if regime else "")
        + "\n"
        + f"<b>Entry:</b>   {entry_type} @ {entry_s}\n"
        + f"<b>Size:</b>    {qty} shares  ({gross_s})\n"
        + f"<b>Stop:</b>    {stop_s}\n"
        + f"<b>Target:</b>  {tp_s}\n"
        + (f"<b>{rr_s}</b>\n" if rr_s else "")
        + ("\n<b>Why:</b>\n" + reasons_txt + "\n" if reasons else "")
        + _external_footer(external)
        + (f"\n<i>{note}</i>\n" if note else "")
        + "\n⚠️ <i>TSX manual entry — place in IBKR mobile.</i>"
    )
    return send(msg)


def _external_footer(external: dict | None) -> str:
    if not external:
        return ""
    notes = "\n".join(esc(external.get(key, "")) for key in
                      ("_data_basis", "_browser_note", "_browser_ai") if external.get(key))
    try:
        from enrichment import format_card_footer

        footer = format_card_footer(external)
        return (("\n" + footer + "\n") if footer else "") + ("\n" + notes + "\n" if notes else "")
    except Exception:
        return "\n" + notes + "\n" if notes else ""


def send_status(title: str, body: str = "", icon: str = "INFO") -> bool:
    msg = f"<b>{icon} {title}</b>"
    if body:
        msg += f"\n\n{body}"
    return send(msg)


def send_no_signal(session_name: str, reason: str = "") -> bool:
    msg = f"<b>{session_name}</b>\nNo high-conviction signals this scan."
    if reason:
        msg += f"\n<i>{reason}</i>"
    return send(msg)


def send_session_summary(
    session_name: str,
    *,
    regime: str,
    gold_tag: str,
    signals_count: int,
    positions_count: int | None,
    extras: str = "",
) -> bool:
    msg = (
        f"<b>{session_name} complete</b>\n"
        f"-----------------------\n"
        f"Regime: {regime}\n"
        f"Gold: {gold_tag}\n"
        f"Signals sent: {signals_count}\n"
        f"Open positions: {positions_count if positions_count is not None else 'unavailable (Gateway offline)'}\n"
    )
    if extras:
        msg += f"\n{extras}"
    return send(msg)


def send_options_signal(
    sig,  # options_signals.OptionsSignal
    *,
    regime: str = "",
    gold_tag: str = "",
) -> bool:
    """Send an OptionsSignal Telegram card. Returns True on success."""
    try:
        from options_signals import format_options_card
        msg = format_options_card(sig, regime=regime, gold_tag=gold_tag)
    except Exception as e:
        # Fallback plain-text card if formatter fails
        price_label = ("Previous completed close" if
                       getattr(sig, "spot_reference", "quote") == "previous_completed_close" else "Spot")
        msg = (
            f"<b>📊 OPTIONS — {esc(sig.symbol)}</b>\n"
            f"Type: {esc(sig.option_type)} | Strike: ${sig.strike:.2f}\n"
            f"Expiry: {esc(sig.expiry)} ({sig.dte}d) | {price_label}: ${sig.spot:.2f}\n"
            f"<i>Formatter error: {type(e).__name__}</i>"
        )
    return send(msg)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage: python telegram_notify.py <message>")
        print("   or: python telegram_notify.py --test")
        sys.exit(1)

    if args[0] == "--test":
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ok = send(
            f"<b>Trading bot connected</b>\n\n"
            f"Test message sent at <code>{now}</code> Calgary time.\n"
            f"If you can read this, the Telegram signal pipeline is live.\n\n"
            f"You will receive session summaries and manual-entry signals."
        )
        print("Test sent:", ok)
        sys.exit(0 if ok else 1)

    ok = send(" ".join(args))
    print("Sent:", ok)
    sys.exit(0 if ok else 1)
