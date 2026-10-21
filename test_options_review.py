"""Offline side quotas, Canadian coverage and daily public-review routing."""

from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd

import llm_researcher
import deep_scan
import options_signals
import research_pipeline as pipeline
import session_runner as runner


class DailyReviewTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict('os.environ', {'OPENCLAW_REVIEW_CLAUDE_MODEL': 'anthropic/claude-opus-5-5',
                'OPENCLAW_REVIEW_OPENAI_MODEL': 'openai/gpt-6.1-sol',
                'OPENCLAW_REVIEW_THINKING': 'medium', 'OPENCLAW_REVIEW_FALLBACK': 'true',
                'OPENCLAW_AI_REVIEW': 'true', 'OPENCLAW_RESEARCH_ENABLED': 'true',
                'OPENCLAW_RESEARCH_TIMEOUT': '300', 'OPENCLAW_REVIEW_TIMEOUT': '90',
                'OPENCLAW_RESEARCH_MAX_STOCKS': '6'})
        env.start()
        self.addCleanup(env.stop)

    def test_daily_rotation_is_stable_and_uses_denver_date(self):
        a = pipeline.review_route(datetime(2026, 10, 14, 2, tzinfo=timezone.utc))
        b = pipeline.review_route(datetime(2026, 10, 14, 8, tzinfo=timezone.utc))
        self.assertEqual(a['requested_model'], 'anthropic/claude-opus-5-5')
        self.assertEqual(b['requested_model'], 'openai/gpt-6.1-sol')
        self.assertEqual(a['thinking'], 'medium')
        self.assertEqual(a, pipeline.review_route(datetime(2026, 10, 14, 2, tzinfo=timezone.utc)))

    def test_only_medium_and_high_are_allowed(self):
        for effort in ('xhigh', 'ultra', 'max', 'adaptive', 'low'):
            with self.subTest(effort=effort), patch.dict('os.environ', {'OPENCLAW_REVIEW_THINKING': effort}):
                with self.assertRaises(ValueError):
                    pipeline.review_route()
        with patch.dict('os.environ', {'OPENCLAW_REVIEW_THINKING': 'high'}):
            self.assertEqual(pipeline.review_route()['thinking'], 'high')

    def test_invalid_model_string_is_rejected(self):
        with patch.dict('os.environ', {'OPENCLAW_REVIEW_OPENAI_MODEL': 'openai/gpt; rm -rf /'}):
            with self.assertRaises(ValueError):
                pipeline.review_route()

    def test_review_records_actual_fallback_without_merging_contracts(self):
        evidence = {'RY': {'status': 'observed', 'contracts': []}}
        route = pipeline.review_route(datetime(2026, 10, 13, 18, tzinfo=timezone.utc))
        payload = {'ok': True, 'provider': 'openai', 'model': 'gpt-6.1-sol',
                   'reviewAttempts': [{'model': route['requested_model'], 'status': 'failed'},
                                      {'model': route['fallback_model'], 'status': 'ok'}],
                   'toolSummary': {'calls': 0}, 'final': json.dumps({'reviews': [
                       {'symbol': 'RY', 'summary': 'Date missing', 'contracts': [{'invented': True}]}]})}
        with patch.object(pipeline.subprocess, 'run', return_value=Mock(returncode=0, stdout=json.dumps(payload))) as run:
            notes = pipeline._agent_review(SimpleNamespace(cli='/test/bin/openclaw', target='user@vm'),
                                           evidence, 90, route)
        request = json.loads(run.call_args.kwargs['input'])
        self.assertEqual(request['thinking'], 'medium')
        self.assertEqual(request['fallback'], 'openai/gpt-6.1-sol')
        self.assertIn('node --input-type=module', run.call_args.args[0][-1])
        self.assertEqual(evidence['RY']['ai_review_meta']['actual_model'], 'openai/gpt-6.1-sol')
        self.assertEqual(evidence['RY']['ai_review_meta']['attempts'], payload['reviewAttempts'])
        self.assertEqual(evidence['RY']['contracts'], [])
        self.assertNotIn('contracts', notes['RY'])

    def test_fallback_can_be_disabled(self):
        with patch.dict('os.environ', {'OPENCLAW_REVIEW_FALLBACK': 'false'}), \
                patch.object(pipeline.subprocess, 'run', return_value=Mock(returncode=124, stdout='')) as run:
            evidence = {'RY': {}}
            pipeline._agent_review(SimpleNamespace(cli='/test/bin/openclaw', target='user@vm'), evidence, 30)
        self.assertNotIn('fallback', json.loads(run.call_args.kwargs['input']))
        self.assertEqual(evidence['RY']['ai_review_meta']['reason'], 'request_timeout')

    def test_known_failures_are_labelled_without_printing_provider_message(self):
        cases = [
            ('agent_error', 'Model openai/gpt-6.1-sol is not allowed for agent main.',
             'model_policy_rejected'),
            ('exception', 'Missing bearer authentication in header.',
             'authentication_unavailable'),
            ('exception', 'No route-compatible authentication source is configured for openai.',
             'authentication_unavailable'),
            ('authentication_unavailable', 'private provider exception',
             'authentication_unavailable'),
        ]
        for kind, message, reason in cases:
            with self.subTest(reason=reason):
                evidence = {'RY': {}}
                payload = {'ok': False, 'status': 'error', 'error': {
                    'kind': kind, 'message': message + ' private-secret'}}
                output = io.StringIO()
                with redirect_stdout(output), patch.object(pipeline.subprocess, 'run',
                        return_value=Mock(returncode=1, stdout=json.dumps(payload))):
                    notes = pipeline._agent_review(SimpleNamespace(cli='/test/bin/openclaw', target='user@vm'),
                                                   evidence, 90)
                self.assertEqual(notes, {})
                self.assertEqual(evidence['RY']['ai_review_meta']['reason'], reason)
                self.assertNotIn('private-secret', output.getvalue())

    def test_unreported_tool_activity_is_not_accepted(self):
        evidence = {'RY': {}}
        payload = {'ok': True, 'final': '{"reviews":[{"symbol":"RY","summary":"unsafe"}]}'}
        with patch.object(pipeline.subprocess, 'run', return_value=Mock(returncode=0, stdout=json.dumps(payload))):
            notes = pipeline._agent_review(SimpleNamespace(cli='/test/bin/openclaw', target='user@vm'), evidence, 30)
        self.assertEqual(notes, {})
        self.assertEqual(evidence['RY']['ai_review_meta']['reason'], 'tool_status_unreported')

    def test_reported_model_mismatch_does_not_silently_break_rotation(self):
        evidence = {'RY': {}}
        payload = {'ok': True, 'provider': 'openai', 'model': 'gpt-5.6-sol',
                   'toolSummary': {'calls': 0}, 'final': '{"reviews":[]}'}
        with patch.object(pipeline.subprocess, 'run', return_value=Mock(returncode=0, stdout=json.dumps(payload))):
            notes = pipeline._agent_review(SimpleNamespace(cli='/test/bin/openclaw', target='user@vm'), evidence, 30)
        self.assertEqual(notes, {})
        self.assertEqual(evidence['RY']['ai_review_meta']['reason'], 'model_mismatch')

    def test_native_claude_provider_identity_is_recorded(self):
        evidence = {'RY': {}}
        route = pipeline.review_route(datetime(2026, 10, 13, 18, tzinfo=timezone.utc))
        payload = {'ok': True, 'provider': 'claude-cli', 'model': 'claude-opus-5-5',
                   'toolSummary': {'calls': 0}, 'final': '{"reviews":[{"symbol":"RY","summary":"No quote date"}]}'}
        with patch.object(pipeline.subprocess, 'run', return_value=Mock(returncode=0, stdout=json.dumps(payload))):
            pipeline._agent_review(SimpleNamespace(cli='/test/bin/openclaw', target='user@vm'), evidence, 30, route)
        self.assertEqual(evidence['RY']['ai_review_meta']['actual_model'], 'anthropic/claude-opus-5-5')

    def collect_fixture(self, seconds, *, lock_wait=0, **env):
        clock = [0.0]
        urls = []
        class Browser:
            deadline = None
            last_cleanup = {'complete': True, 'closed': 6}
            def run_research(self, collect):
                clock[0] += lock_wait
                return collect(self)
            def visible_page(self, url):
                urls.append(url)
                clock[0] += seconds
                return {'title': 'Public fixture', 'text': 'No quote timestamp', 'tables': []}
        def review(browser, evidence, timeout, route):
            pipeline._review_status(evidence, route, 'accepted', actual_model=route['requested_model'])
            return {symbol: {'summary': 'Incomplete quote date', 'concerns': ''} for symbol in evidence}
        with patch.dict('os.environ', env), patch.object(pipeline, 'CanadianResearchBrowser', Browser), \
                patch.object(pipeline.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(pipeline, '_agent_review', side_effect=review) as agent:
            evidence = pipeline.verify_shortlist(['RY', 'TD', 'BMO', 'SU', 'CNQ', 'MFC'])
        return evidence, urls, agent

    def test_source_budget_reserves_full_review_and_mx_precedes_secondary(self):
        evidence, urls, agent = self.collect_fixture(30)
        self.assertEqual(len(urls), 6)
        self.assertTrue(all('m-x.ca' in url for url in urls))
        self.assertEqual(agent.call_args.args[2], 90)
        self.assertTrue(all(item['cleanup']['complete'] for item in evidence.values()))

    def test_lock_wait_does_not_consume_public_collection_or_review_budget(self):
        evidence, urls, agent = self.collect_fixture(30, lock_wait=180)
        self.assertEqual(len(urls), 6)
        self.assertTrue(all('m-x.ca' in url for url in urls))
        self.assertEqual(agent.call_args.args[2], 90)
        self.assertTrue(all(item['status'] == 'observed' for item in evidence.values()))

    def test_secondary_pages_come_after_all_mx_pages(self):
        _, urls, _ = self.collect_fixture(1)
        self.assertTrue(all('m-x.ca' in url for url in urls[:6]))
        self.assertTrue(all('barchart.com' in url for url in urls[6:]))

    def test_disabled_review_does_not_disable_collection(self):
        evidence, urls, agent = self.collect_fixture(1, OPENCLAW_AI_REVIEW='false')
        self.assertTrue(urls)
        agent.assert_not_called()
        self.assertEqual(evidence['RY']['ai_review_meta']['reason'], 'disabled')

    def test_invalid_effort_still_preserves_public_collection(self):
        evidence, urls, agent = self.collect_fixture(1, OPENCLAW_REVIEW_THINKING='ultra')
        self.assertTrue(urls)
        agent.assert_not_called()
        self.assertEqual(evidence['RY']['ai_review_meta']['reason'], 'invalid_review_configuration')

    def test_review_status_is_visible_in_evidence_note(self):
        note = pipeline.evidence_note({'status': 'observed', 'contracts': [],
                                      'ai_review_meta': {'status': 'failed', 'reason': 'request_timeout'}})
        self.assertIn('not completed (request_timeout)', note)

    def test_option_replacements_use_same_lock_and_remain_bounded(self):
        names = ['A', 'X', 'B', 'Y', 'C', 'Z', 'D', 'E']
        directions = {s: ('CALL' if s in 'ABC' else 'PUT') for s in names}
        urls = []
        clock = [0.]
        class Browser:
            last_cleanup = {'complete': True}
            def run_research(self, collect):
                return collect(self)
            def visible_page(self, url):
                urls.append(url)
                clock[0] += 10
                return {'title': 'fixture', 'text': ''}
        def contracts(page, symbol, url, **kwargs):
            return [{'right': directions[symbol]}] if symbol in 'ABCDE' else []
        with patch.object(pipeline, 'CanadianResearchBrowser', Browser), \
                patch.object(pipeline, 'parse_mx_contracts', side_effect=contracts), \
                patch.object(pipeline.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.dict('os.environ', {'OPENCLAW_AI_REVIEW': 'false'}):
            evidence = pipeline.verify_shortlist(names, option_directions=directions)
        self.assertEqual(len([u for u in urls if 'm-x.ca' in u]), 8)
        self.assertEqual(evidence['D']['contracts'][0]['right'], 'PUT')
        self.assertTrue(evidence['E']['cleanup']['complete'])

    def test_replacements_do_not_run_without_option_directions(self):
        evidence, urls, _ = self.collect_fixture(1)
        self.assertEqual(len([u for u in urls if 'm-x.ca' in u]), 6)
        self.assertEqual(len(evidence), 6)

    def test_option_replacement_attempts_never_exceed_twice_primary_limit(self):
        names = [f'A{i}' for i in range(30)]
        urls = []
        class Browser:
            last_cleanup = {'complete': True}
            def run_research(self, collect):
                return collect(self)
            def visible_page(self, url):
                urls.append(url)
                return {'title': 'Unidentified quote page', 'text': '', 'tables': []}
        with patch.object(pipeline, 'CanadianResearchBrowser', Browser), \
                patch.dict('os.environ', {'OPENCLAW_AI_REVIEW': 'false'}):
            evidence = pipeline.verify_shortlist(names, option_directions={s: 'PUT' for s in names})
        self.assertEqual(len(evidence), 12)
        self.assertEqual(len([url for url in urls if 'm-x.ca' in url]), 12)
        self.assertTrue(all(item.get('collection_reason') == 'mx_symbol_identity_unverified'
                            for item in evidence.values()))

    def test_contract_rejection_identifies_market_liquidity_not_account_cash(self):
        now = datetime.now(timezone.utc)
        row = {'symbol': 'NXE', 'currency': 'CAD', 'exchange': 'CDE', 'right': 'PUT',
               'source': 'MX visible table', 'source_url': 'https://www.m-x.ca/en/trading/data/quotes?symbol=NXE',
               'expiry': (now.date() + timedelta(days=30)).isoformat(), 'quote_time': now.isoformat(),
               'strike': 11, 'bid': .5, 'ask': .55, 'open_interest': 14, 'volume': 0}
        diagnostics = {}
        self.assertIsNone(pipeline.validated_contract(row, 'NXE', now, diagnostics=diagnostics))
        self.assertEqual(diagnostics['rejected'], {'contract_oi_below_100_and_volume_below_10': 1})
        self.assertIsNotNone(pipeline.validated_contract({**row, 'volume': 10}, 'NXE', now))


class TriageCoverageTests(unittest.TestCase):
    def test_exhausted_dispatch_budget_stops_requests_and_records_unknown(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(deep_scan, 'LOGS_DIR', temp), \
                patch.object(llm_researcher, 'is_enabled', return_value=True), \
                patch.object(deep_scan.time, 'monotonic', side_effect=[0, 400, 400]), \
                patch.object(llm_researcher, 'triage_watchlist') as call:
            result = deep_scan.run_triage([{'symbol': 'A'}], {}, 'fixture')
            audit = json.loads(next(Path(temp).glob('deep_scan_*.json')).read_text())
        call.assert_not_called()
        self.assertEqual(result, {})
        self.assertEqual(audit['missing_symbols'], ['A'])

    def test_momentum_labels_use_explicit_lookbacks_not_two_year_baseline(self):
        bars = pd.DataFrame({'close': [10.] * 196 + [100.] * 64,
                             'high': [1000.] * 200 + [110.] * 60})
        sig = SimpleNamespace(indicators={'close': 100}, sendable=False)
        with patch.object(deep_scan, 'fundamentals_lite', return_value=None), \
                patch.object(deep_scan, 'pc_sentiment', return_value=None):
            facts = deep_scan.build_factsheet('A', sig, bars, [])
        self.assertEqual(facts['ret_3m_pct'], 0.)
        self.assertEqual(facts['dist_60d_high_pct'], -9.1)
        self.assertNotIn('ret_full_pct', facts)

    def test_missing_only_retry_does_not_overwrite_valid_or_accept_extra_symbols(self):
        facts = [{'symbol': s} for s in ['A', 'B', 'C']]
        first = {'symbol': 'A', 'direction': 'LONG', 'conviction': 4}
        second = [{'symbol': 'B', 'direction': 'SHORT', 'conviction': 4},
                  {'symbol': 'C', 'direction': 'NONE', 'conviction': 1},
                  {'symbol': 'A', 'direction': 'SHORT', 'conviction': 5},
                  {'symbol': 'FAKE', 'direction': 'SHORT', 'conviction': 5}]
        with tempfile.TemporaryDirectory() as temp, patch.object(deep_scan, 'LOGS_DIR', temp), \
                patch.object(llm_researcher, 'is_enabled', return_value=True), \
                patch.object(llm_researcher, 'triage_watchlist', side_effect=[[first], second]) as call:
            result = deep_scan.run_triage(facts, {}, 'fixture')
            audit = json.loads(next(Path(temp).glob('deep_scan_*.json')).read_text())
        self.assertEqual([f['symbol'] for f in call.call_args_list[1].args[0]], ['B', 'C'])
        self.assertEqual(result['A']['direction'], 'LONG')
        self.assertNotIn('FAKE', result)
        self.assertTrue(audit['coverage_complete'])

    def test_invalid_verdicts_remain_unknown_after_one_retry(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(deep_scan, 'LOGS_DIR', temp), \
                patch.object(llm_researcher, 'is_enabled', return_value=True), \
                patch.object(llm_researcher, 'triage_watchlist', return_value=[
                    {'symbol': 'A', 'direction': 'invented', 'conviction': 5}]) as call:
            result = deep_scan.run_triage([{'symbol': 'A'}], {}, 'fixture')
            audit = json.loads(next(Path(temp).glob('deep_scan_*.json')).read_text())
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result, {})
        self.assertEqual(audit['missing_symbols'], ['A'])

    def test_full_watchlist_uses_small_batches_and_bounded_timeouts(self):
        def triage(batch, context, **kwargs):
            self.assertLessEqual(len(batch), 10)
            self.assertLessEqual(kwargs['timeout'], 60)
            return [{'symbol': fs['symbol'], 'direction': 'NONE', 'conviction': 1} for fs in batch]
        with tempfile.TemporaryDirectory() as temp, patch.object(deep_scan, 'LOGS_DIR', temp), \
                patch.object(llm_researcher, 'is_enabled', return_value=True), \
                patch.object(llm_researcher, 'triage_watchlist', side_effect=triage) as call:
            result = deep_scan.run_triage([{'symbol': f'S{i}'} for i in range(75)], {}, 'fixture')
        self.assertEqual(len(result), 75)
        self.assertEqual(call.call_count, 8)


class OptionCoverageTests(unittest.TestCase):
    def signal(self, symbol, side):
        sig = options_signals.OptionsSignal(symbol, side, 100,
              (datetime.now(timezone.utc).date() + timedelta(days=28)).isoformat(),
              28, 100, None, None, None, None, 2.1, symbol)
        sig.confidence = 4
        sig.sendable = True
        return sig

    def test_calls_do_not_fill_unused_put_slots(self):
        sigs = [self.signal(str(i), 'CALL') for i in range(4)]
        self.assertEqual(len(runner.select_option_cards(sigs, [])), 2)

    def test_puts_do_not_fill_unused_call_slots(self):
        sigs = [self.signal(str(i), 'PUT') for i in range(4)]
        self.assertEqual(len(runner.select_option_cards(sigs, [])), 2)

    def test_balanced_selection_respects_daily_prior_counts(self):
        sigs = [self.signal('A', 'CALL'), self.signal('B', 'PUT'), self.signal('C', 'PUT')]
        prior = [{'option_type': 'CALL'}, {'option_type': 'CALL'}, {'option_type': 'PUT'}]
        chosen = runner.select_option_cards(sigs, prior)
        self.assertEqual([(s.symbol, s.option_type) for s in chosen], [('B', 'PUT')])

    def test_historical_extra_call_is_not_erased_or_given_more_room(self):
        prior = [{'option_type': 'CALL'}] * 3
        chosen = runner.select_option_cards([self.signal('A', 'CALL'), self.signal('B', 'PUT'),
                                              self.signal('C', 'PUT')], prior)
        self.assertEqual([s.option_type for s in chosen], ['PUT'])
        self.assertEqual(len(prior), 3)

    def test_interleaved_page_budget_includes_bearish_names(self):
        names = runner.interleave_names(['A', 'B', 'C', 'D'], ['X', 'Y', 'Z'])[:6]
        self.assertEqual(names, ['A', 'X', 'B', 'Y', 'C', 'Z'])

    def test_coverage_notice_and_audit_report_missing_side(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, 'LOGS_DIR', Path(temp)), \
                patch.object(runner, 'send_status', return_value=True) as send:
            runner.options_coverage([], [self.signal('A', 'CALL')], [], [], {}, 'session2', 75)
            audit = json.loads(next(Path(temp).glob('options_coverage_*.json')).read_text())
        self.assertIn('PUT 0/2', send.call_args.args[1])
        self.assertIn('Unused slots are not filled', send.call_args.args[1])
        self.assertEqual(audit['daily_delivered'], {'CALL': 1, 'PUT': 0})

    def test_coverage_audit_retains_ibkr_rejections_after_mx_fallback(self):
        diagnostics = {'PUT': {'rejected': {'contract_liquidity_unknown': 8}}}
        evidence = {'BCE': {'status': 'observed', 'source_kind': 'ibkr+mx',
                            'collection_reason': 'source_unavailable', 'contract_validation': {},
                            'ibkr_evidence': {'collection_reason': 'ibkr_no_qualified_contracts',
                                              'contract_validation': diagnostics}}}
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, 'LOGS_DIR', Path(temp)), \
                patch.object(runner, 'send_status', return_value=True):
            runner.options_coverage([], [], [], [self.signal('BCE', 'PUT')], evidence, 'session2', 75)
            audit = json.loads(next(Path(temp).glob('options_coverage_*.json')).read_text())
        self.assertEqual(audit['ibkr_checked_symbols'], ['BCE'])
        self.assertEqual(audit['candidate_rejections']['BCE']['ibkr_contract_validation'], diagnostics)
        self.assertEqual(audit['candidate_rejections']['BCE']['collection_reason'], 'source_unavailable')

    def test_options_prompt_distinguishes_previous_close_and_premium_units(self):
        prompt = llm_researcher._build_options_prompt([{
            'symbol': 'BCE', 'option_type': 'PUT', 'spot': 28.58, 'strike': 28.5,
            'spot_reference': 'previous_completed_close', 'est_premium': 0.98}], {})
        self.assertIn('Previous completed close (not current spot): $28.58', prompt)
        self.assertIn('current moneyness is unverified', prompt)
        self.assertIn('$0.98/share', prompt)
        self.assertNotIn('Spot: $28.58', prompt)

    def test_sent_options_audit_keeps_spot_reference(self):
        sig = self.signal('BCE', 'PUT')
        sig.spot_reference = 'previous_completed_close'
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, 'LOGS_DIR', Path(temp)):
            runner.append_options_signal_log(sig, 'session2')
            audit = json.loads(next(Path(temp).glob('options_signals_sent_*.jsonl')).read_text())
        self.assertEqual(audit['spot_reference'], 'previous_completed_close')

    def test_unpriced_notice_does_not_hide_puts_behind_higher_ranked_calls(self):
        sigs = [self.signal(f'C{i}', 'CALL') for i in range(4)] + [self.signal('P0', 'PUT')]
        for sig in sigs:
            sig.sendable = False
        bars = pd.DataFrame({'close': [100.] * 260, 'volume': [100000.] * 260})
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, 'LOGS_DIR', Path(temp)), \
                patch.object(runner, 'load_watchlist', return_value=[s.symbol for s in sigs]), \
                patch.object(runner, 'options_signals_sent_today', return_value=[]), \
                patch.object(options_signals, 'score_options_signal', side_effect=sigs), \
                patch.object(runner, 'send_status', return_value=True) as send:
            runner.run_options_scan(None, '', '', {}, [], 'fixture', browser_evidence={},
                                    bars_by_symbol={s.symbol: bars for s in sigs}, xic_bars=bars)
        body = next(call.args[1] for call in send.call_args_list if call.args[0] == 'Options watchlist')
        self.assertIn('P0: PUT underlying bias', body)
        self.assertIn('C0: CALL underlying bias', body)
        self.assertNotIn('C2:', body)

    def test_native_canadian_symbol_without_us_dual_is_eligible(self):
        bars = pd.DataFrame({'open': [100.] * 260, 'high': [101.] * 260,
                             'low': [99.] * 260, 'close': [100.] * 260,
                             'volume': [100000.] * 260, 'ema_50': [90.] * 260})
        snapshot = {'close': 100, 'trend': 'bullish', 'ema_50': 90, 'ema_200': 80,
                    'macd': 2, 'macd_signal': 1, 'rsi_14': 55}
        now = datetime.now(timezone.utc)
        row = {'symbol': 'AC', 'currency': 'CAD', 'exchange': 'CDE', 'right': 'CALL',
               'source': 'MX visible table', 'source_url': 'https://www.m-x.ca/en/trading/data/quotes?symbol=AC',
               'expiry': (now.date() + timedelta(days=28)).isoformat(), 'strike': 100,
               'bid': 2, 'ask': 2.2, 'open_interest': 150, 'volume': 0,
               'quote_time': now.isoformat(), 'data_mode': 'delayed'}
        self.assertNotIn('AC', options_signals.DUAL_LIST_MAP)
        with patch('indicators.latest_snapshot', return_value=snapshot), \
                patch('filters.has_earnings_within', return_value=(False, None)), \
                patch('options_analyzer.analyze_options', side_effect=AssertionError('US lookup')):
            sig = options_signals.score_options_signal('AC', bars,
                    browser_evidence={'status': 'observed', 'contracts': [row]})
        self.assertTrue(sig.sendable)
        self.assertEqual(sig.currency, 'CAD')
        self.assertEqual(sig.spot_reference, 'previous_completed_close')
        self.assertNotIn('Spot:', options_signals.format_options_card(sig))

    def test_preview_never_approves_a_contract_or_calls_earnings(self):
        bars = pd.DataFrame({'close': [90.] * 260, 'volume': [100000.] * 260, 'ema_50': [110.] * 260})
        snap = {'close': 90, 'trend': 'bearish', 'ema_50': 110, 'ema_200': 120,
                'macd': 0, 'macd_signal': 1, 'rsi_14': 35}
        with patch('indicators.latest_snapshot', return_value=snap), \
                patch('filters.has_earnings_within', side_effect=AssertionError('external earnings')):
            sig = options_signals.score_options_signal('AC', bars, skip_external_io=True, allow_unpriced=True)
        self.assertEqual(sig.option_type, 'PUT')
        self.assertFalse(sig.sendable)

    def test_option_card_shows_public_browser_reasoning(self):
        sig = self.signal('RY', 'PUT')
        sig.browser_note = 'MX checked; OpenClaw review: openai/gpt-6.1-sol, medium effort.'
        sig.browser_ai_review = {'summary': '<date conflict>', 'concerns': 'Missing liquidity'}
        card = options_signals.format_options_card(sig)
        self.assertIn('Browser evidence', card)
        self.assertIn('medium effort', card)
        self.assertIn('&lt;date conflict&gt;', card)

    def test_scan_counts_actual_sends_and_never_sends_third_call(self):
        sigs = [self.signal(s, 'CALL') for s in ['AC', 'CNQ', 'SU']]
        bars = pd.DataFrame({'close': [100.] * 260, 'volume': [100000.] * 260})
        evidence = {sig.symbol: {'status': 'observed', 'contracts': [{
                     'right': 'CALL', 'expiry': sig.expiry, 'strike': sig.strike}]} for sig in sigs}
        with ExitStack() as stack:
            temp = stack.enter_context(tempfile.TemporaryDirectory())
            stack.enter_context(patch.object(runner, 'LOGS_DIR', Path(temp)))
            stack.enter_context(patch.object(runner, 'load_watchlist', return_value=[sig.symbol for sig in sigs]))
            stack.enter_context(patch.object(runner, 'options_signals_sent_today', return_value=[]))
            stack.enter_context(patch.object(runner, 'fetch_bars', side_effect=AssertionError('cached history ignored')))
            stack.enter_context(patch.object(options_signals, 'score_options_signal', side_effect=sigs))
            stack.enter_context(patch('fundamentals.fetch_fundamentals', return_value={}))
            stack.enter_context(patch.object(llm_researcher, 'is_enabled', return_value=False))
            stack.enter_context(patch.object(runner.filters, 'in_signal_window', return_value=(True, None)))
            stack.enter_context(patch.object(pipeline, 'validated_contract', side_effect=lambda row, symbol: row))
            sender = stack.enter_context(patch('telegram_notify.send_options_signal', side_effect=[True, False]))
            audit = stack.enter_context(patch.object(runner, 'append_options_signal_log'))
            notices = stack.enter_context(patch.object(runner, 'send_status', return_value=True))
            stack.enter_context(redirect_stdout(io.StringIO()))
            sent = runner.run_options_scan(None, 'CANADA NEUTRAL', 'GOLD-UNKNOWN', {}, [], 'session2',
                      browser_evidence=evidence, bars_by_symbol={sig.symbol: bars for sig in sigs}, xic_bars=bars)
            self.assertEqual(sent, 1)
            self.assertEqual(sender.call_count, 2)
            self.assertEqual(audit.call_count, 1)
            coverage = json.loads(next(Path(temp).glob('options_coverage_*.json')).read_text())
            self.assertEqual(coverage['daily_delivered'], {'CALL': 1, 'PUT': 0})
            self.assertIn('PUT 0/2', notices.call_args.args[1])

    def test_final_earnings_guard_is_not_skipped_for_native_names(self):
        bars = pd.DataFrame({'close': [100.] * 260, 'volume': [100000.] * 260})
        with patch('filters.has_earnings_within', return_value=(True, '2026-10-14')) as check:
            self.assertIsNone(options_signals.score_options_signal('AC', bars, allow_unpriced=True))
        check.assert_called_once()


class FinalAnswerTests(unittest.TestCase):
    def test_reasoning_text_is_not_substituted_for_missing_final_json(self):
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason='length',
                   message=SimpleNamespace(content=None, reasoning_content='{"verdicts":[{"symbol":"FAKE"}]}'))])
        client = Mock()
        client.chat.completions.create.return_value = response
        with patch('openai.OpenAI', return_value=client), patch.object(llm_researcher, 'API_KEY', 'fixture-key'), \
                redirect_stdout(io.StringIO()):
            self.assertIsNone(llm_researcher._call_openai_compat('Public fixture'))

    def test_leaked_reasoning_draft_is_ignored_for_final_json(self):
        # Shape observed 2026-10-15: a draft with a duplicate placeholder, prose,
        # DeepSeek's end-of-thinking marker, then the real answer.
        draft = '{"verdicts": [{"symbol": "SU", "direction": "NONE"}, {"symbol": "SU", "reason": "duplicate placeholder"}]}'
        final = '{"verdicts": [{"symbol": "SU", "direction": "LONG", "conviction": 4}], "summary": "final"}'
        raw = draft + '\n\nWait - need all symbols once. Redo.<｜end▁of▁thinking｜>' + final
        with redirect_stdout(io.StringIO()), patch.object(llm_researcher, '_save_debug') as debug:
            result = llm_researcher._robust_parse(raw, label='triage')
        self.assertEqual(result['summary'], 'final')
        self.assertEqual(result['verdicts'], [{'symbol': 'SU', 'direction': 'LONG', 'conviction': 4}])
        debug.assert_not_called()

    def test_reasoning_with_no_answer_after_marker_is_not_parsed(self):
        raw = '{"verdicts": [{"symbol": "FAKE", "direction": "LONG"}]} thinking...</think>  '
        with redirect_stdout(io.StringIO()), patch.object(llm_researcher, '_save_debug') as debug:
            self.assertIsNone(llm_researcher._robust_parse(raw, label='triage'))
        debug.assert_called_once()

    def test_two_answers_without_marker_still_fail_rather_than_guess(self):
        raw = '{"summary": "draft"}\nprose\n{"summary": "other"}'
        with redirect_stdout(io.StringIO()), patch.object(llm_researcher, '_save_debug'):
            self.assertIsNone(llm_researcher._robust_parse(raw, label='triage'))


class StrikeLabelTests(unittest.TestCase):
    def card(self, side, strike, spot):
        sig = options_signals.OptionsSignal('CNQ', side, strike, '2026-11-06', 23, spot,
                                            None, None, None, None, 3.7, 'CNQ')
        sig.confidence = 5
        return options_signals.format_options_card(sig)

    def test_october_14_contracts_are_labelled_in_the_money(self):
        self.assertIn('(3.1% ITM)', self.card('CALL', 72.0, 74.27))
        self.assertIn('(3.7% ITM)', self.card('CALL', 104.0, 107.99))
        self.assertNotIn('(ATM)', self.card('CALL', 72.0, 74.27))

    def test_put_and_otm_and_atm_labels(self):
        self.assertEqual(options_signals._moneyness_label('PUT', 105, 100), '5.0% ITM')
        self.assertEqual(options_signals._moneyness_label('PUT', 95, 100), '5.0% OTM')
        self.assertEqual(options_signals._moneyness_label('CALL', 104, 100), '4.0% OTM')
        self.assertEqual(options_signals._moneyness_label('CALL', 100.5, 100), 'ATM')
        self.assertEqual(options_signals._moneyness_label('CALL', 0, 100), 'moneyness unavailable')


class SymbolIdentityTests(unittest.TestCase):
    def test_resolver_never_offers_bare_us_symbol(self):
        from yf_symbols import yf_ticker_candidates
        self.assertEqual(yf_ticker_candidates('T'), ['T.TO', 'T.V'])
        self.assertEqual(yf_ticker_candidates('TECK.B'), ['TECK-B.TO', 'TECK-B.V'])
        self.assertEqual(yf_ticker_candidates('RCK'), ['RCK.V', 'RCK.TO'])

    def test_insider_lookup_uses_only_canadian_listing_and_curated_dual(self):
        import insider_transactions
        tried = []

        def ticker(symbol):
            tried.append(symbol)
            return SimpleNamespace(insider_transactions=None)

        with patch('yfinance.Ticker', side_effect=ticker):
            for symbol in ('T', 'CCO', 'MX', 'WELL', 'AC'):
                self.assertEqual(insider_transactions.get_insider_summary(symbol), {})
        # TELUS -> TU (never AT&T "T"), Cameco -> CCJ (never Clear Channel "CCO");
        # MX/WELL/AC have no curated US listing, so Magnachip/Welltower/Associated
        # Capital are never queried and data stays unknown.
        self.assertEqual(tried, ['T.TO', 'TU', 'CCO.TO', 'CCJ', 'MX.TO', 'WELL.TO', 'AC.TO'])


if __name__ == '__main__':
    unittest.main()
