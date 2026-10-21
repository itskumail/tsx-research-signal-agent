"""Offline contract provenance, mandatory gates and history regression tests."""
import copy
from datetime import date, datetime, timedelta, timezone
import json
import shlex
import time
import unittest
from unittest.mock import Mock, patch

import pandas as pd

import filters
from research_pipeline import validated_contract, parse_mx_contracts, _agent_review, verify_shortlist
from browser_research import CanadianResearchBrowser
from options_signals import _resolve_chain_source, _pick_expiry, OptionsSignal, format_options_card
from signals import score_symbol
import session_runner as runner


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.row = {"symbol": "RY", "currency": "CAD", "exchange": "CDE", "right": "CALL",
                    "expiry": (self.now.date() + timedelta(days=28)).isoformat(), "strike": 100,
                    "bid": 2, "ask": 2.2, "open_interest": 150, "volume": 0,
                    "quote_time": (self.now - timedelta(minutes=15)).isoformat(),
                    "source": "MX visible table", "source_url": "https://www.m-x.ca/en/trading/data/quotes?symbol=RY",
                    "data_mode": "delayed", "multiplier": None}

    def test_valid_delayed_native_contract(self):
        checked = validated_contract(self.row, "RY", self.now)
        self.assertEqual(checked["mid"], 2.1)
        chain = _resolve_chain_source("RY", "RY", 100, "CALL", None, True, {"contracts": [self.row]})
        self.assertEqual(chain["strike"], 100)
        self.assertIsNone(chain["delta"])

    def test_missing_native_contract_never_calls_us_or_gateway_fallback(self):
        client = Mock()
        with patch("yfinance.Ticker") as ticker:
            self.assertIsNone(_resolve_chain_source("RY", "RY", 100, "CALL", client, True))
        ticker.assert_not_called()
        self.assertFalse(client.mock_calls)

    def test_reject_bad_provenance_liquidity_dates_and_numbers(self):
        changes = [{"currency": "USD"}, {"exchange": "SMART"}, {"symbol": "TD"},
                   {"source_url": "https://www.m-x.ca.evil.test/"}, {"source": "AI"},
                   {"quote_time": (self.now - timedelta(minutes=31)).isoformat()},
                   {"quote_time": (self.now + timedelta(minutes=5)).isoformat()},
                   {"quote_time": "2026-10-09T10:00:00"}, {"strike": float("nan")},
                   {"ask": 1}, {"ask": 5}, {"bid": 0}, {"open_interest": 0, "volume": 0},
                   {"expiry": self.now.date().isoformat()}]
        for change in changes:
            with self.subTest(change=change):
                self.assertIsNone(validated_contract({**self.row, **change}, "RY", self.now))

    def test_read_time_is_not_quote_time_and_unknown_layout_is_rejected(self):
        self.assertEqual(parse_mx_contracts({"text": "Page viewed today", "tables": []}, "RY", self.row["source_url"]), [])
        self.assertEqual(parse_mx_contracts({"text": "Last update: 2026-10-09 10:00", "tables": [[['bid','ask']]]}, "RY", self.row["source_url"]), [])

    def test_observed_mx_grouped_layout_and_english_timestamp(self):
        from zoneinfo import ZoneInfo
        local = self.now.astimezone(ZoneInfo("America/Toronto")) - timedelta(minutes=15)
        stamp = local.strftime("%B %d, %Y at %I:%M ") + ("p.m." if local.hour >= 12 else "a.m.")
        expiry = (self.now.date() + timedelta(days=28)).strftime("%B %d, %Y")
        header = ["Bid price", "Ask price", "Last price", "Net change", "Open int.", "Vol.",
                  "Strike", "Bid price", "Ask price", "Last price", "Net change", "Open int.", "Vol."]
        cells = ["2", "2.2", "2.1", "0", "150", "0", "100", "2", "2.2", "2.1", "0", "150", "0"]
        page = {"title": "RY Option Price, Quotes and Trading Data", "text": "Last update: " + stamp +
                " (Real-time)\nLast price: 100.00", "tables": [[header, [expiry + " (Weekly)"], cells]]}
        rows = parse_mx_contracts(page, "RY", self.row["source_url"])
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["right"] for row in rows}, {"CALL", "PUT"})
        self.assertEqual(rows[0]["strike"], 100)
        self.assertEqual(rows[0]["spot"], 100)
        self.assertEqual(rows[0]["data_mode"], "MX page labelled real-time")
        page["title"] = "Wrong instrument"
        self.assertEqual(parse_mx_contracts(page, "RY", self.row["source_url"]), [])

    def test_expiries_are_only_21_to_45_days(self):
        expiry = lambda d: (self.now.date() + timedelta(days=d)).isoformat()
        with patch("options_signals.date", wraps=date) as clock:
            clock.today.return_value = self.now.date()
            self.assertEqual(_pick_expiry([expiry(0), expiry(7), expiry(35), expiry(21), expiry(60)]), (expiry(21), 21))
            self.assertIsNone(_pick_expiry([expiry(0), expiry(7), expiry(46)]))

    def test_option_premium_units_and_unknown_multiplier(self):
        signal = OptionsSignal("RY", "CALL", 100, self.row["expiry"], 28, 100, None, None, None, None, 2.1, "RY")
        card = format_options_card(signal)
        self.assertIn("/share", card)
        self.assertNotIn("/contract", card)
        signal.multiplier = 100
        self.assertIn("C$210.00", format_options_card(signal))

    def test_ai_review_cannot_add_quotes_or_contracts(self):
        browser = CanadianResearchBrowser()
        payload = {"ok": True, "final": json.dumps({"reviews": [{"symbol": "RY", "summary": "Observed risk",
                     "contracts": [self.row]}, {"symbol": "FAKE", "summary": "ignore"}]}), "toolSummary": {"calls": 0}}
        with patch("research_pipeline.subprocess.run", return_value=Mock(returncode=0, stdout=json.dumps(payload))) as run:
            review = _agent_review(browser, {"RY": {}}, 30)
        self.assertEqual(review, {"RY": {"summary": "Observed risk", "concerns": ""}})
        remote = run.call_args.args[0][-1]
        bridge = shlex.split(remote)[-1]
        self.assertIn("CLAUDE_CODE_SAFE_MODE: '1'", bridge)
        self.assertIn("/usr/local/bin", remote)
        self.assertIn('tools: []', bridge)
        self.assertNotIn("Observed risk", remote)
        self.assertNotIn("auth login", remote)
        self.assertIn("--model", remote)
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['thinking'], 'medium')
        self.assertLessEqual(run.call_args.kwargs["timeout"], 50)

    def test_ai_review_with_tool_calls_or_failed_run_is_not_accepted(self):
        for payload, code in (({"ok": True, "toolSummary": {"calls": 1}}, 0),
                              ({"ok": False}, 0), ({"ok": True}, 124)):
            with self.subTest(code=code, payload=payload), patch("research_pipeline.subprocess.run", return_value=Mock(
                    returncode=code, stdout=json.dumps(payload))):
                self.assertEqual(_agent_review(CanadianResearchBrowser(), {"RY": {}}, 30), {})

    def test_ai_review_input_is_bounded_without_mutating_evidence(self):
        evidence = {"RY": {"status": "observed", "contracts": [self.row] * 20,
                            "sources": [{"url": self.row["source_url"], "excerpt": "x" * 90000}] * 3}}
        original = copy.deepcopy(evidence)
        payload = {"ok": True, "final": '{"reviews":[]}', "toolSummary": {"calls": 0}}
        with patch("research_pipeline.subprocess.run", return_value=Mock(
                returncode=0, stdout=json.dumps(payload))) as run:
            _agent_review(CanadianResearchBrowser(), evidence, 30)
        prompt = json.loads(run.call_args.kwargs["input"])['prompt']
        projected = json.loads(prompt[prompt.index('{"RY":'):])["RY"]
        self.assertEqual(len(projected["contract_sample"]), 6)
        self.assertEqual(projected["qualified_contract_count"], 20)
        self.assertEqual(len(projected["sources"]), 2)
        self.assertEqual(len(projected["sources"][0]["excerpt"]), 1500)
        self.assertLess(len(prompt), 6000)
        self.assertEqual({k: {field: value for field, value in item.items() if field != 'ai_review_meta'}
                          for k, item in evidence.items()}, original)
        self.assertEqual(evidence['RY']['ai_review_meta']['reason'], 'no_valid_notes')


class GateTests(unittest.TestCase):
    def test_canadian_holiday_and_weekends_are_not_signal_sessions(self):
        from trading_calendar import signal_window
        from zoneinfo import ZoneInfo
        for day in ("2026-10-12", "2026-10-10"):
            self.assertFalse(signal_window(datetime.fromisoformat(day + "T11:00:00").replace(
                tzinfo=ZoneInfo("America/Denver")))[0])
        self.assertTrue(signal_window(datetime(2026, 10, 13, 11, tzinfo=ZoneInfo("America/Denver")))[0])

    def test_early_close_is_respected(self):
        from trading_calendar import signal_window
        from zoneinfo import ZoneInfo
        self.assertFalse(signal_window(datetime(2026, 12, 24, 13, tzinfo=ZoneInfo("America/Toronto")))[0])

    def test_earnings_blackout_counts_trading_days_over_canadian_holiday(self):
        from trading_calendar import earnings_within
        from datetime import date
        now = datetime(2026, 10, 9, 15, tzinfo=timezone.utc)
        self.assertTrue(earnings_within(date(2026, 10, 15), now, 3))
        self.assertFalse(earnings_within(date(2026, 10, 16), now, 3))

    def test_nonfinite_prices_volume_and_crossed_quotes_fail(self):
        for extra in ({"last": float("nan")}, {"volume": float("inf")}, {"bid": 101, "ask": 100}):
            self.assertFalse(filters.passes_tradability({"last": 100, "volume": 100000, **extra})[0])

    def test_insufficient_history_is_a_hard_blocker(self):
        bars = pd.DataFrame({"close": [100.] * 60})
        signal = score_symbol(bars, "RY", timeframe="1-DAY", skip_external_io=True)
        self.assertFalse(signal.sendable)
        self.assertTrue(signal.hard_blockers)

    def test_disabled_research_never_connects(self):
        with patch.dict("os.environ", {"OPENCLAW_RESEARCH_ENABLED": "false"}), \
                patch("research_pipeline.CanadianResearchBrowser") as browser:
            self.assertEqual(verify_shortlist(["RY"]), {})
        browser.assert_not_called()

    def test_two_year_duration_requests_two_year_fallback(self):
        with patch.object(runner, "_yahoo_history", return_value=pd.DataFrame()) as history:
            runner.fetch_bars(None, "RY", "2 Y", "1 day", "CAD")
        self.assertEqual(history.call_args.kwargs["period_days"], 504)


class IbkrChainTests(unittest.TestCase):
    """IBKR CDE quotes read from the Paper tab; offline fixtures only."""

    def setUp(self):
        import ibkr_options
        env = patch.dict("os.environ", {"OPENCLAW_RESEARCH_ENABLED": "true", "OPENCLAW_IBKR_CHAINS": "true",
                                       "OPENCLAW_RESEARCH_TIMEOUT": "300", "OPENCLAW_RESEARCH_MAX_STOCKS": "6"})
        env.start()
        self.addCleanup(env.stop)
        self.ibkr = ibkr_options
        self.now = datetime.now(timezone.utc)
        self.expiry = self.now.date() + timedelta(days=30)
        self.contract = {"conid": 934355811, "right": "P", "strike": 28.5,
                         "maturity": self.expiry.strftime("%Y%m%d"), "multiplier": "100",
                         "exchange": "CDE", "currency": "CAD"}
        self.quote = {"84": "0.95", "86": "1.01", "88": "95", "85": "54", "7638": "844", "7762": "12",
                      "6509": "DBd", "7308": "-0.458", "7633": "27.6%",
                      "_updated": int(self.now.timestamp() * 1000)}

    def rows(self, quote=None):
        return self.ibkr.contract_rows("BCE", [self.contract], {"934355811": quote or self.quote}, 28.58, self.now)

    def test_scripts_only_issue_allow_listed_read_only_gets(self):
        for script in (self.ibkr.RESOLVE_FN, self.ibkr.SNAPSHOT_FN):
            lowered = script.lower()
            for forbidden in ("post", "order", "account", "portfolio", "reply", "transfer", ".click("):
                self.assertNotIn(forbidden, lowered)
            self.assertIn("method: 'GET'", script)
            self.assertIn("blocked_path", script)
            self.assertNotIn("unsubscribe", script)
        self.assertNotIn("SPEC", self.ibkr._script(self.ibkr.RESOLVE_FN, {"items": []}))

    def test_month_codes_cover_window_across_year_end(self):
        self.assertEqual(self.ibkr.month_codes(date(2026, 11, 5), date(2026, 11, 29)), ["NOV26"])
        self.assertEqual(self.ibkr.month_codes(date(2026, 12, 15), date(2027, 1, 20)), ["DEC26", "JAN27"])

    def test_numbers_accept_ibkr_formats_and_reject_prior_close_prefix(self):
        for raw, expected in (("1,234", 1234), ("1.2K", 1200), ("0.95", 0.95), ("C1.37", None), (None, None), ("", None)):
            self.assertEqual(self.ibkr.ibkr_number(raw), expected)

    def test_delayed_quote_passes_shared_gates_with_shifted_timestamp(self):
        row = self.rows()[0]
        self.assertEqual(row["right"], "PUT")
        self.assertEqual(row["multiplier"], 100)
        self.assertAlmostEqual(row["implied_vol"], 0.276)
        self.assertLess(datetime.fromisoformat(row["quote_time"]), self.now - timedelta(minutes=14))
        checked = validated_contract(row, "BCE", self.now)
        self.assertEqual(checked["mid"], 0.98)
        chain = _resolve_chain_source("BCE", None, 28.58, "PUT", None, True, {"contracts": [checked]})
        self.assertEqual(chain["chain_source"], "IBKR")
        self.assertEqual(chain["delta"], -0.458)
        self.assertEqual(chain["multiplier"], 100)
        self.assertEqual(row["spot_reference"], "previous_completed_close")
        self.assertEqual(chain["spot_reference"], "previous_completed_close")

    def test_ibkr_previous_close_is_not_current_spot_or_moneyness_in_card(self):
        row = validated_contract(self.rows()[0], "BCE", self.now)
        chain = _resolve_chain_source("BCE", None, 28.58, "PUT", None, True, {"contracts": [row]})
        sig = OptionsSignal("BCE", "PUT", 28.5, row["expiry"], 30, chain["spot"], None, None, None, None,
                            row["mid"], "BCE", chain_source="IBKR", spot_reference=chain["spot_reference"])
        card = format_options_card(sig)
        self.assertIn("Previous completed close: C$28.58", card)
        self.assertIn("moneyness unverified", card)
        self.assertNotIn("Spot:", card)
        self.assertNotIn("(ATM)", card)

    def test_mx_spot_remains_quoted_but_missing_spot_is_labelled_close(self):
        row = {**self.rows()[0], "source": "MX visible table",
               "source_url": "https://www.m-x.ca/en/trading/data/quotes?symbol=BCE"}
        row.pop("spot_reference")
        chain = _resolve_chain_source("BCE", None, 28.58, "PUT", None, True, {"contracts": [row]})
        self.assertEqual(chain["spot_reference"], "quote")
        row["spot"] = None
        chain = _resolve_chain_source("BCE", None, 28.58, "PUT", None, True, {"contracts": [row]})
        self.assertEqual(chain["spot_reference"], "previous_completed_close")

    def test_frozen_unsubscribed_prefixed_or_unidentified_quotes_are_rejected(self):
        bad = [{**self.quote, "6509": "ZBd"}, {**self.quote, "6509": "N"}, {**self.quote, "84": "C0.95"},
               {**self.quote, "7638": "3", "7762": "0"}, {k: v for k, v in self.quote.items() if k != "_updated"}]
        for quote in bad:
            with self.subTest(quote=quote):
                self.assertIsNone(validated_contract(self.rows(quote)[0], "BCE", self.now))
        row = {**self.rows()[0], "ibkr_conid": "934355811"}
        self.assertIsNone(validated_contract(row, "BCE", self.now))

    def test_collect_quotes_builds_evidence_and_respects_deadline(self):
        browser = Mock(keeper="ibkr")
        browser._command.side_effect = [
            {"result": [{"symbol": "BCE", "conid": 39984079, "contracts": [self.contract]},
                        {"symbol": "NDA", "contracts": [], "reason": "ibkr_no_cde_options"}]},
            {"result": {"934355811": self.quote}}]
        import time
        evidence = self.ibkr.collect_quotes(browser, {"BCE": "PUT", "NDA": "PUT"}, {"BCE": 28.58, "NDA": 1.2},
                                            time.monotonic() + 120, validate=validated_contract)
        self.assertEqual(len(evidence["BCE"]["contracts"]), 1)
        self.assertEqual(evidence["BCE"]["source_kind"], "ibkr")
        self.assertEqual(evidence["NDA"]["collection_reason"], "ibkr_no_cde_options")
        commands = [c.args for c in browser._command.call_args_list]
        self.assertTrue(all(c[0] == "evaluate" for c in commands))
        browser._command.reset_mock()
        self.assertEqual(self.ibkr.collect_quotes(browser, {"BCE": "PUT"}, {"BCE": 28.58}, time.monotonic() + 10,
                                                  validate=validated_contract), {})
        browser._command.assert_not_called()

    def test_quote_order_rotates_names_best_contracts_first(self):
        resolved = {
            "BCE": {"contracts": [{"conid": 1, "maturity": "20261120", "strike": 28.5},
                                  {"conid": 2, "maturity": "20261106", "strike": 30},
                                  {"conid": 3, "maturity": "20261106", "strike": 28.5}]},
            "CNQ": {"contracts": [{"conid": 4, "maturity": "20261106", "strike": 74},
                                  {"conid": 5, "maturity": "20261106", "strike": 80}]}}
        self.assertEqual(self.ibkr.quote_order(resolved, {"BCE": 28.58, "CNQ": 74.2}), [3, 4, 2, 5, 1])

    def test_unquoted_contracts_are_counted_apart_from_zero_bids_and_allow_mx_retry(self):
        browser = Mock(keeper="ibkr")
        contracts = [dict(self.contract, conid=10 + i) for i in range(45)]
        browser._command.side_effect = [{"result": [{"symbol": "BCE", "contracts": contracts}]}, {"result": {}}]
        import time
        clock = iter([0, 0])   # resolve check, first quote batch; then past the deadline
        with patch("ibkr_options.time.monotonic", side_effect=lambda: next(clock, 1000)):
            evidence = self.ibkr.collect_quotes(browser, {"BCE": "PUT"}, {"BCE": 28.58}, 100,
                                                validate=validated_contract)
        rejected = evidence["BCE"]["contract_validation"]["PUT"]["rejected"]
        self.assertEqual(rejected, {"ibkr_quote_not_received": 40, "ibkr_quote_not_requested": 5})
        self.assertEqual(evidence["BCE"]["collection_reason"], "ibkr_quotes_unavailable")

    def test_invalid_quality_sample_allows_mx_retry_without_weakening_gates(self):
        cases = ({"7638": "", "7762": "", "87": ""}, {"86": "3"},
                 {"6509": "ZBd"}, {"_updated": int((self.now - timedelta(hours=2)).timestamp() * 1000)})
        for change in cases:
            with self.subTest(change=change):
                browser = Mock(keeper="ibkr")
                browser._command.side_effect = [
                    {"result": [{"symbol": "BCE", "contracts": [self.contract]}]},
                    {"result": {"934355811": {**self.quote, **change}}}]
                evidence = self.ibkr.collect_quotes(browser, {"BCE": "PUT"}, {"BCE": 28.58},
                            time.monotonic() + 120, validate=validated_contract)
                self.assertEqual(evidence["BCE"]["contracts"], [])
                self.assertEqual(evidence["BCE"]["collection_reason"], "ibkr_no_qualified_contracts")
                self.assertTrue(evidence["BCE"]["contract_validation"]["PUT"]["rejected"])

    def test_mx_fallback_preserves_ibkr_diagnostics_and_existing_contracts(self):
        original = {"status": "observed", "source_kind": "ibkr", "contracts": [],
                    "contract_validation": {"PUT": {"rejected": {"contract_liquidity_unknown": 8}}},
                    "ibkr_contracts_checked": 8, "ibkr_contracts_quoted": 8}
        for mx_fails in (False, True):
            for reason in (None, "ibkr_no_qualified_contracts", "ibkr_lookup_failed", "ibkr_quotes_unavailable"):
                with self.subTest(mx_fails=mx_fails, reason=reason):
                    prior = {**original, **({"collection_reason": reason} if reason else {})}
                    if reason == "ibkr_lookup_failed":
                        prior["contracts"] = [{**self.rows()[0], "right": "CALL"}]
                    untouched = copy.deepcopy(prior)
                    browser = Mock(last_cleanup=None)
                    browser.paper_session.return_value = {"status": "ready"}
                    browser.run_research.side_effect = lambda collect: collect(browser)
                    browser.visible_page.side_effect = (RuntimeError("fixture source failure") if mx_fails
                        else lambda url: {"title": "BCE Option Price", "text": "", "tables": []})
                    mx = {**self.rows()[0], "source": "MX visible table"}
                    with patch("research_pipeline.CanadianResearchBrowser", return_value=browser), \
                            patch("ibkr_options.collect_quotes", return_value={"BCE": prior}), \
                            patch("research_pipeline.parse_mx_contracts", return_value=[mx]), \
                            patch.dict("os.environ", {"OPENCLAW_AI_REVIEW": "false",
                                "OPENCLAW_IBKR_CHAINS": "true", "OPENCLAW_RESEARCH_ENABLED": "true"}), \
                            patch("builtins.print"):
                        evidence = verify_shortlist(["BCE"], option_directions={"BCE": "PUT"}, spots={"BCE": 28.58})
                    self.assertTrue(any("m-x.ca" in c.args[0] for c in browser.visible_page.call_args_list))
                    self.assertEqual(evidence["BCE"]["ibkr_evidence"], prior)
                    self.assertEqual(evidence["BCE"]["source_kind"], "ibkr+mx")
                    self.assertEqual(evidence["BCE"]["contracts"], prior["contracts"] + ([] if mx_fails else [mx]))
                    self.assertEqual(prior, untouched)

    def test_qualified_matching_ibkr_contract_skips_mx(self):
        prior = {"status": "observed", "source_kind": "ibkr", "contracts": [self.rows()[0]], "sources": []}
        browser = Mock(last_cleanup=None)
        browser.paper_session.return_value = {"status": "ready"}
        browser.run_research.side_effect = lambda collect: collect(browser)
        browser.visible_page.return_value = {"title": "fixture", "text": ""}
        with patch("research_pipeline.CanadianResearchBrowser", return_value=browser), \
                patch("ibkr_options.collect_quotes", return_value={"BCE": prior}), \
                patch.dict("os.environ", {"OPENCLAW_AI_REVIEW": "false", "OPENCLAW_IBKR_CHAINS": "true"}), \
                patch("builtins.print"):
            evidence = verify_shortlist(["BCE"], option_directions={"BCE": "PUT"}, spots={"BCE": 28.58})
        self.assertEqual(evidence["BCE"]["source_kind"], "ibkr")
        self.assertFalse(any("m-x.ca" in c.args[0] for c in browser.visible_page.call_args_list))

    def test_ibkr_covered_names_skip_mx_pages_and_logged_out_falls_back(self):
        covered = {"BCE": {"status": "observed", "source_kind": "ibkr", "contracts": [],
                           "collection_reason": "ibkr_no_contract_in_window"}}
        for paper, expected_mx in (("ready", ["CNQ"]), ("login_required", ["BCE", "CNQ"])):
            browser = Mock(last_cleanup=None)
            browser.paper_session.return_value = {"status": paper}
            browser.visible_page.return_value = {"title": "x", "text": "", "tables": []}
            browser.run_research.side_effect = lambda collect: collect(browser)
            with self.subTest(paper=paper), patch("research_pipeline.CanadianResearchBrowser", return_value=browser), \
                    patch("ibkr_options.collect_quotes", return_value=covered) as collect, \
                    patch.dict("os.environ", {"OPENCLAW_AI_REVIEW": "false"}), patch("builtins.print"):
                evidence = verify_shortlist(["BCE", "CNQ"], option_directions={"BCE": "PUT", "CNQ": "CALL"},
                                            spots={"BCE": 28.58, "CNQ": 74.0})
            mx = [c.args[0].rsplit("=", 1)[1] for c in browser.visible_page.call_args_list if "m-x.ca" in c.args[0]]
            self.assertEqual(mx, expected_mx)
            self.assertEqual(collect.called, paper == "ready")
            self.assertIn("BCE", evidence)

    def test_card_names_ibkr_source_and_contract_size(self):
        sig = OptionsSignal("BCE", "PUT", 28.5, self.expiry.isoformat(), 30, 28.58, None, None, None, None,
                            0.98, "BCE", chain_source="IBKR", bid=0.95, ask=1.01, multiplier=100,
                            quote_time=self.now.isoformat(), data_mode="IBKR Paper delayed ~15 min")
        sig.confidence = 4
        sig.delta = -0.458
        card = format_options_card(sig)
        self.assertIn("IBKR Paper", card)
        self.assertIn("Contract premium: ~C$98.00 (100 shares)", card)
        self.assertIn("-0.458", card)


if __name__ == "__main__":
    unittest.main()
