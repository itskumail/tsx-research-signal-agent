"""Offline regression checks for dated prices, regimes and Gateway fallback."""

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from market_context import (chart_snapshot, usable_pct, canadian_regime,
                            us_regime, format_market_context)
import session_runner as runner
import telegram_notify
import llm_researcher
import news_llm_filter
from signals import Signal


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def chart(symbol="XIC.TO", price=55.92, previous=56.89):
    # October 7: XIC fell 1.70%; the five-day baseline suggests a gain.
    return {
        "meta": {"symbol": symbol, "regularMarketPrice": price,
                 "regularMarketTime": utc("2026-10-07T16:59:55").timestamp(),
                 "chartPreviousClose": 54.0,
                 "exchangeTimezoneName": "America/Toronto"},
        "timestamp": [utc(f"2026-10-{day}T13:30:00").timestamp()
                      for day in ("05", "06", "07")],
        "indicators": {"quote": [{"close": [54.0, previous, price]}]},
    }


class MarketContextTests(unittest.TestCase):
    now = utc("2026-10-07T17:00:00")

    def test_october_7_replay_uses_daily_close_not_chart_baseline(self):
        ca = chart_snapshot("XIC.TO", chart(), self.now)
        q = chart_snapshot("QQQ", chart("QQQ", 757.73, 759.66), self.now)
        s = chart_snapshot("SPY", chart("SPY", 100, 100.24), self.now)
        self.assertAlmostEqual(ca["pct"] * 100, -1.705, places=2)
        self.assertAlmostEqual(q["pct"] * 100, -0.254, places=2)
        self.assertEqual(ca["reference_date"], "2026-10-06")
        self.assertEqual(canadian_regime(ca), "CANADA BEARISH")
        self.assertEqual(us_regime(q, s), "US NEUTRAL")

    def test_canada_can_be_bearish_when_us_is_bullish(self):
        ca = chart_snapshot("XIC.TO", chart(), self.now)
        us = {"pct": 0.02, "status": "live"}
        self.assertEqual(canadian_regime(ca), "CANADA BEARISH")
        self.assertEqual(us_regime(us, us), "US BULLISH")

    def test_premarket_does_not_relabel_yesterdays_close_as_today(self):
        ca = chart_snapshot("XIC.TO", chart(), utc("2026-10-08T12:00:00"))
        self.assertEqual(canadian_regime(ca), "CANADA NOT OPEN")
        self.assertIsNone(usable_pct(ca))
        text = format_market_context({"regime": canadian_regime(ca), "snapshots": {"XIC.TO": ca}})
        self.assertIn("last regular quote", text)
        self.assertIn("10-07", text)

    def test_us_premarket_is_not_unknown(self):
        preopen = {"pct": -0.02, "status": "premarket; last regular quote"}
        self.assertEqual(us_regime(preopen, preopen), "US NOT OPEN")
        self.assertIsNone(usable_pct(preopen))
        legacy = {**preopen, "status": "premarket; last regular close"}
        self.assertEqual(us_regime(legacy, legacy), "US NOT OPEN")

    def test_us_weekend_is_distinct_from_unknown(self):
        closed = {"pct": -0.02, "status": "market closed"}
        self.assertEqual(us_regime(closed, closed), "US MARKET CLOSED")

    def test_us_missing_and_stale_inputs_identify_the_actual_problem(self):
        live = {"pct": 0.01, "status": "live"}
        for status in ("unavailable", "stale", "previous session", "invalid timestamp"):
            with self.subTest(status=status):
                self.assertEqual(us_regime(live, {"pct": 0.01, "status": status}),
                                 f"US UNKNOWN (SPY {status})")
        preopen = {"pct": 0.01, "status": "premarket; last regular quote"}
        mixed = us_regime(preopen, {"status": "unavailable"})
        self.assertIn("UNKNOWN", mixed)
        self.assertIn("SPY unavailable", mixed)

    def test_us_missing_daily_reference_is_not_described_as_not_open(self):
        live = {"pct": 0.01, "status": "live"}
        self.assertEqual(us_regime({"pct": None, "status": "live"}, live),
                         "US UNKNOWN (QQQ daily comparison unavailable)")

    def test_sector_reference_quotes_are_displayed_but_not_used_as_live_inputs(self):
        preopen = chart_snapshot("XEG.TO", chart("XEG.TO"), utc("2026-10-08T12:00:00"))
        market = {"sector_etf_pct": {"XEG": None}, "snapshots": {"XEG.TO": preopen}}
        text = format_market_context(market)
        self.assertIn("XEG: -1.71% [premarket; last regular quote; quote 10-07 10:59 MDT; vs 2026-10-06]", text)
        self.assertIn("pre-open values are reference only", text)
        self.assertNotIn("XEG: unavailable", text)
        self.assertIsNone(usable_pct(preopen))
        self.assertIsNone(market["sector_etf_pct"]["XEG"])

    def test_sector_missing_stale_and_zero_values_remain_distinct(self):
        snapshots = {
            "XEG.TO": {"status": "unavailable", "pct": None},
            "XGD.TO": {"status": "stale", "pct": -0.02},
            "XFN.TO": {"status": "live", "pct": 0.0},
        }
        text = format_market_context({"snapshots": snapshots})
        self.assertIn("XEG: unavailable [unavailable;", text)
        self.assertIn("XGD: -2.00% [stale;", text)
        self.assertIn("XFN: +0.00% [live;", text)
        self.assertIsNone(usable_pct(snapshots["XGD.TO"]))

    def test_legacy_sector_values_without_snapshots_do_not_invent_quote_timing(self):
        text = format_market_context({"sector_etf_pct": {"XEG": 0.01, "XGD": None}})
        self.assertIn("XEG: +1.00% [quote timing unavailable]", text)
        self.assertIn("XGD: unavailable [quote timing unavailable]", text)

    def test_vix_reference_level_has_status_and_time_without_affecting_live_gate(self):
        snap = {"status": "premarket; last regular close", "price": 18.61,
                "quote_time": "2026-10-16T20:00:00+00:00"}
        market = {"vix_level": None, "snapshots": {"^VIX": snap}}
        text = format_market_context(market)
        self.assertIn("VIX: 18.61 [premarket; last regular quote; quote 10-16 14:00 MDT]", text)
        self.assertIsNone(market["vix_level"])

    def test_determine_regime_keeps_premarket_sector_gates_unknown(self):
        preopen = chart_snapshot("XIC.TO", chart(), utc("2026-10-08T12:00:00"))
        with patch.object(runner, "_yahoo_market_snapshot", return_value=preopen):
            regime, _, market = runner.determine_regime()
        self.assertEqual(regime, "CANADA NOT OPEN")
        self.assertEqual(market["us_regime"], "US NOT OPEN")
        self.assertEqual(len(market["sector_etf_pct"]), 8)
        self.assertTrue(all(value is None for value in market["sector_etf_pct"].values()))
        text = format_market_context(market)
        for symbol in market["sector_etf_pct"]:
            self.assertIn(f"{symbol}: -1.71% [premarket;", text)
        self.assertLess(len(text), 3000)  # Leave room for Telegram heading/browser notes.

    def test_previous_session_and_stale_quotes_are_not_live_direction(self):
        for now in (utc("2026-10-08T17:00:00"), utc("2026-10-07T18:00:00")):
            ca = chart_snapshot("XIC.TO", chart(), now)
            self.assertIsNone(usable_pct(ca))
            self.assertIn("UNKNOWN", canadian_regime(ca))

    def test_friday_close_is_correct_reference_on_monday(self):
        data = chart()
        data["timestamp"] = [utc("2026-10-08T13:30:00").timestamp(),
                             utc("2026-10-09T13:30:00").timestamp(),
                             utc("2026-10-12T13:30:00").timestamp()]
        data["meta"]["regularMarketTime"] = utc("2026-10-12T16:59:55").timestamp()
        ca = chart_snapshot("QQQ", data, utc("2026-10-12T17:00:00"))
        self.assertEqual(ca["reference_date"], "2026-10-09")
        self.assertAlmostEqual(ca["pct"], 55.92 / 56.89 - 1)

    def test_missing_prior_close_does_not_jump_back_another_day(self):
        data = chart()
        data["indicators"]["quote"][0]["close"][1] = None
        ca = chart_snapshot("XIC.TO", data, self.now)
        self.assertIsNone(ca["pct"])
        self.assertIn("UNKNOWN", canadian_regime(ca))

    def test_quote_without_current_daily_bar_is_unavailable(self):
        data = chart()
        data["timestamp"].pop()
        data["indicators"]["quote"][0]["close"].pop()
        self.assertIsNone(chart_snapshot("XIC.TO", data, self.now)["pct"])

    def test_futures_crossing_midnight_use_previous_session_close(self):
        data = chart("GC=F", 4158.3, 4140.7)
        data["timestamp"] = [utc("2026-10-06T04:00:00").timestamp(),
                             utc("2026-10-07T04:00:00").timestamp(),
                             utc("2026-10-08T04:00:00").timestamp()]
        data["meta"]["regularMarketTime"] = utc("2026-10-08T06:00:00").timestamp()
        result = chart_snapshot("GC=F", data, utc("2026-10-08T06:01:00"))
        self.assertAlmostEqual(result["pct"], 4158.3 / 4140.7 - 1)

    def test_early_close_is_recognized(self):
        data = chart()
        data["meta"]["currentTradingPeriod"] = {"regular": {
            "start": utc("2026-10-07T13:30:00").timestamp(),
            "end": utc("2026-10-07T17:00:00").timestamp()}}
        self.assertEqual(chart_snapshot("XIC.TO", data, utc("2026-10-07T18:00:00"))["status"], "closed")

    def test_canadian_regime_and_missing_sector_data_propagate(self):
        snap = chart_snapshot("XIC.TO", chart(), self.now)
        def fetch(symbol, now):
            if symbol == "XIC.TO":
                return snap
            if symbol in {"QQQ", "SPY"}:
                return {"pct": 0.02, "price": 100, "status": "live"}
            return {"pct": None, "price": None, "status": "unavailable"}
        with patch.object(runner, "_yahoo_market_snapshot", side_effect=fetch):
            regime, gold, market = runner.determine_regime()
        self.assertEqual(regime, "CANADA BEARISH")
        self.assertEqual(market["us_regime"], "US BULLISH")
        self.assertIsNone(market["sector_etf_pct"]["XEG"])
        self.assertIn("UNKNOWN", gold)

    def test_all_ai_prompts_preserve_units_and_missing_data(self):
        market = {"regime": "CANADA BEARISH", "us_regime": "US NEUTRAL",
                  "xic_pct": -0.01705, "qqq_pct": -0.00254, "wti_pct": None}
        prompts = [llm_researcher._build_prompt([], market),
                   llm_researcher._build_triage_prompt([], market),
                   llm_researcher._build_options_prompt([], market),
                   news_llm_filter._build_prompt([], [], market),
                   news_llm_filter._build_weekly_focus_prompt([], [], market)]
        for prompt in prompts:
            self.assertIn("XIC.TO: -1.70%", prompt)
            self.assertIn("CL=F: unavailable", prompt)
            self.assertIn("US NEUTRAL", prompt)

    def test_unknown_holdings_are_not_reported_as_zero(self):
        with patch.object(telegram_notify, "send", return_value=True) as send:
            telegram_notify.send_session_summary("EOD", regime="CANADA BEARISH",
                                                  gold_tag="GOLD-WEAK", signals_count=2,
                                                  positions_count=None)
        self.assertIn("Open positions: unavailable", send.call_args.args[0])


class GatewayFallbackTests(unittest.TestCase):
    def run_offline(self, session, connect_fails=False, gateway_available=False,
                    browser_available=False, browser_error=False, gateway_enabled=True,
                    blocked=False, window_open=True):
        market = {"regime": "CANADA BEARISH", "us_regime": "US NEUTRAL",
                  "gold_tag": "GOLD-WEAK", "xic_pct": -0.017,
                  "sector_etf_pct": {}}
        sig = Signal(symbol="RY", action="BUY", timeframe="1-DAY", score=7,
                     weighted_score=7, min_score_required=6, confidence=3,
                     entry_price=100, qty=40, stop_loss=95, take_profit=108,
                     currency="CAD", sendable=True, indicators={"close": 100})
        if blocked:
            sig.sendable = False
            sig.hard_blockers = ["Earnings blackout"]
        df = pd.DataFrame({"open": [100.] * 260, "high": [101.] * 260,
                           "low": [99.] * 260, "close": [100.] * 260,
                           "volume": [100000.] * 260},
                          index=pd.date_range("2025-10-01", periods=260))
        with ExitStack() as stack:
            temp = stack.enter_context(tempfile.TemporaryDirectory())
            stack.enter_context(patch.dict("os.environ", {
                "IBKR_GATEWAY_UP": "1" if connect_fails or gateway_available else "0",
                "IBKR_GATEWAY_ENABLED": "true" if gateway_enabled else "false",
                "REQUIRE_GATEWAY": "false"}))
            stack.enter_context(patch.object(runner, "LOGS_DIR", Path(temp)))
            # Any unexpected network call is a test failure, including Telegram.
            stack.enter_context(patch("urllib.request.urlopen", side_effect=AssertionError("unexpected network")))
            stack.enter_context(patch("requests.post", side_effect=AssertionError("unexpected Telegram send")))
            client = stack.enter_context(patch.object(runner, "IBKRClient"))
            if not gateway_available:
                client.return_value.connect.side_effect = ConnectionRefusedError("offline")
            client.return_value.get_historical_bars.return_value = []
            client.return_value.get_quotes.return_value = []
            stack.enter_context(patch.object(
                runner, "optional_browser_status",
                return_value={"profile": "canadian-stocks", "available": browser_available,
                              "status": "ready" if browser_available else "unavailable"},
                side_effect=RuntimeError("browser probe failed") if browser_error else None))
            for obj, name, value in [
                (runner.news_fetcher, "run_fetch", 0), (runner.news_rss_fetcher, "run_fetch", 0),
                (runner.premarket_scan, "scan_and_alert_catalysts", 0),
                (runner, "determine_regime", ("CANADA BEARISH", "GOLD-WEAK", market)),
                (runner, "signals_sent_today", []), (runner, "options_signals_sent_today", []),
                (runner.filters, "load_news_events_today", []),
                (runner.filters, "audit_sector_coverage", {"unmapped": [], "tech_not_in_sectormap": [], "gold_not_in_sectormap": []}),
                (runner, "load_watchlist", ["RY"]), (runner, "fetch_bars", df),
                (runner, "score_symbol", sig), (runner, "_detect_catalyst", None),
                (runner.deep_scan, "build_factsheet", {"symbol": "RY"}),
                (runner.deep_scan, "run_triage", {"RY": {"direction": "LONG", "conviction": 5, "reason": "AI approves"}} if blocked else {}), (runner.enrichment, "enrich", {}),
                (llm_researcher, "is_enabled", False),
                (news_llm_filter, "find_news_driven_candidates", {}),
                (runner, "recently_sent_symbols", set()), (runner, "append_signal_log", None),
                (runner, "verify_shortlist", {}), (runner.filters, "in_signal_window", (window_open, "window closed" if not window_open else None)),
            ]:
                stack.enter_context(patch.object(obj, name, return_value=value))
            sends = {name: stack.enter_context(patch.object(runner, name, return_value=True))
                     for name in ["send", "send_status", "send_session_summary", "send_no_signal", "maybe_send"]}
            options = stack.enter_context(patch.object(runner, "run_options_scan", return_value=1))
            reviews = stack.enter_context(patch.object(runner, "review_existing_positions",
                                                       return_value=(set(), [])))
            stack.enter_context(redirect_stdout(io.StringIO()))
            runner.run_session(session)
            uses_gateway = gateway_enabled and gateway_available and not session.startswith("premarket")
            if uses_gateway:
                reviews.assert_called_once()
            else:
                reviews.assert_not_called()
            if gateway_enabled and (connect_fails or uses_gateway):
                client.return_value.connect.assert_called_once()
                client.return_value.disconnect.assert_called_once()
            else:
                client.assert_not_called()
            if session.startswith("premarket"):
                sends["send"].assert_called_once()
            elif (blocked or not window_open) and session != "session4":
                sends["send_no_signal"].assert_called_once()
            else:
                sends["send_session_summary"].assert_called_once()
                count = sends["send_session_summary"].call_args.kwargs["positions_count"]
                self.assertEqual(count, 0 if uses_gateway else None)
            if session in {"session1", "session2", "session3"}:
                if blocked or not window_open:
                    sends["maybe_send"].assert_not_called()
                else:
                    sends["maybe_send"].assert_called_once()
            if session == "session2":
                options.assert_called_once()
                self.assertEqual(options.call_args.args[0], client.return_value if uses_gateway else None)
            else:
                options.assert_not_called()
            saved = list(Path(temp).glob("market_snapshot_*.json"))
            self.assertEqual(len(saved), 1)
            snapshot = json.loads(saved[0].read_text())
            self.assertEqual(snapshot["regime"], "CANADA BEARISH")
            self.assertEqual(snapshot["optional_browser"]["available"],
                             browser_available and not browser_error)
            if session.startswith("premarket"):
                message = sends["send"].call_args.args[0]
            elif (blocked or not window_open) and session != "session4":
                call = sends["send_no_signal"].call_args
                message = call.kwargs.get("reason", call.args[1] if len(call.args) > 1 else "")
            else:
                message = sends["send_session_summary"].call_args.kwargs["extras"]
            self.assertIn("Research Chrome: " + ("ready" if browser_available and not browser_error else
                                                "unavailable"), message)
            self.assertIn("Optional read-only verification", message)

    def test_disabled_gateway_never_connects_even_if_probe_and_browser_are_up(self):
        for session in runner.SESSION_CONFIG:
            with self.subTest(session=session):
                self.run_offline(session, gateway_available=True, browser_available=True, gateway_enabled=False)

    def test_ai_long_approval_cannot_promote_a_mandatory_blocker(self):
        self.run_offline("session1", blocked=True)

    def test_window_is_rechecked_before_dispatch(self):
        self.run_offline("session1", window_open=False)

    def test_all_six_sessions_continue_with_gateway_down(self):
        for session in runner.SESSION_CONFIG:
            with self.subTest(session=session):
                self.run_offline(session)

    def test_probe_up_but_login_missing_still_sends_eod_summary(self):
        self.run_offline("session4", connect_fails=True)

    def test_gateway_and_chrome_are_independent_for_all_six_sessions(self):
        for gateway in (False, True):
            for chrome in (False, True):
                for session in runner.SESSION_CONFIG:
                    with self.subTest(gateway=gateway, chrome=chrome, session=session):
                        self.run_offline(session, gateway_available=gateway, browser_available=chrome)

    def test_browser_probe_exception_does_not_block_telegram(self):
        for session in runner.SESSION_CONFIG:
            with self.subTest(session=session):
                self.run_offline(session, browser_error=True)

    def test_share_class_fallback_uses_canadian_symbol(self):
        data = chart("TECK-B.TO")
        data["indicators"]["quote"][0].update(
            open=[54, 56, 55], high=[55, 57, 56], low=[53, 55, 54], volume=[100000] * 3)
        response = Mock()
        response.read.return_value = json.dumps({"chart": {"result": [data]}}).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("urllib.request.urlopen", return_value=response) as request:
            bars = runner._yahoo_history("TECK.B")
        self.assertEqual(len(bars), 3)
        request.assert_called_once()
        self.assertIn("/TECK-B.TO?", request.call_args.args[0].full_url)


class SchedulerWrapperTests(unittest.TestCase):
    def test_exit_codes_and_fallback_markers(self):
        source = Path(r"C:\Trading\scheduled\run_session.ps1").read_text(encoding="utf-8-sig")
        for gateway_exit, session_exit in [(0, 7), (1, 0), (0, 0)]:
            with self.subTest(gateway=gateway_exit, session=session_exit), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                logs = root / "logs"
                logs.mkdir()
                (root / "check_gateway.py").write_text(
                    "import os,sys\nsys.exit(int(os.environ['TEST_GATEWAY_EXIT']))\n")
                (root / "session_runner.py").write_text(
                    "import os,sys\nsys.exit(int(os.environ['TEST_SESSION_EXIT']))\n")
                (root / "telegram_notify.py").write_text(
                    "def send_status(*args, **kwargs):\n    print('MOCK TELEGRAM')\n")
                script = source.replace('$TradingDir = "C:\\Trading"', f'$TradingDir = "{root}"')
                script = script.replace('$venvPython = "$TradingDir\\.venv\\Scripts\\python.exe"',
                                        '$venvPython = "C:\\Trading\\.venv\\Scripts\\python.exe"')
                wrapper = root / "run_session.ps1"
                wrapper.write_text(script, encoding="utf-8-sig")
                today = datetime.now().strftime("%Y%m%d")
                own = logs / f"skipped_{today}_session1.json"
                other = logs / f"skipped_{today}_session2.json"
                own.write_text("{}")
                other.write_text("{}")
                env = {**os.environ, "TEST_GATEWAY_EXIT": str(gateway_exit),
                       "IBKR_GATEWAY_ENABLED": "true",
                       "TEST_SESSION_EXIT": str(session_exit)}
                result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                         "-File", str(wrapper), "-Session", "session1"],
                                        env=env, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 1 if session_exit else 0, result.stderr)
                self.assertTrue(other.exists())
                self.assertEqual(own.exists(), bool(gateway_exit or session_exit))
                log = next(logs.glob("scheduler_*.log")).read_text(encoding="utf-16")
                if session_exit:
                    self.assertIn("MOCK TELEGRAM", log)
                    self.assertIn("exit code 7", log)


if __name__ == "__main__":
    unittest.main()
