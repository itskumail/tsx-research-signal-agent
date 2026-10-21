"""Offline notification diagnostics tests; never send a real message."""

import re
import unittest
from unittest.mock import Mock, patch

import enrichment
import ownership_data
import telegram_notify


def assert_telegram_html_safe(case, text):
    """Every '<' must open a supported tag and every '&' must start an entity."""
    for match in re.finditer(r"<", text):
        case.assertRegex(text[match.start():], r"^</?(b|i|a|code)(\s[^<>]*)?>", text)
    for match in re.finditer(r"&", text):
        case.assertRegex(text[match.start():], r"^&(amp|lt|gt|quot|#\d+);", text)


class TelegramDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        token = patch.object(telegram_notify, "TOKEN", "test-secret-token")
        chat = patch.object(telegram_notify, "CHATID", "test-private-chat")
        token.start()
        chat.start()
        self.addCleanup(token.stop)
        self.addCleanup(chat.stop)

    def test_exception_does_not_log_token_or_payload(self):
        with patch.object(telegram_notify.requests, "post", side_effect=RuntimeError(
                "test-secret-token test-private-chat private-message")), patch("builtins.print") as log:
            self.assertFalse(telegram_notify.send("private-message"))
        self.assertEqual(log.call_args.args[0], "Telegram send exception: RuntimeError")

    def test_http_failure_does_not_log_response_body(self):
        with patch.object(telegram_notify.requests, "post", return_value=Mock(
                status_code=403, text="test-secret-token private-message")), patch("builtins.print") as log:
            self.assertFalse(telegram_notify.send("private-message"))
        self.assertEqual(log.call_args.args[0], "Telegram send failed: HTTP 403")

    def test_success_remains_quiet(self):
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True}
        with patch.object(telegram_notify.requests, "post", return_value=response), patch("builtins.print") as log:
            self.assertTrue(telegram_notify.send("test"))
        log.assert_not_called()

    def test_option_formatter_fallback_keeps_reference_label_and_redacts_exception(self):
        sig = Mock(symbol="BCE", option_type="PUT", strike=28.5, expiry="2026-11-20", dte=35,
                   spot=28.58, spot_reference="previous_completed_close")
        with patch("options_signals.format_options_card", side_effect=ValueError("private broker data")), \
                patch.object(telegram_notify, "send", return_value=True) as send:
            self.assertTrue(telegram_notify.send_options_signal(sig))
        card = send.call_args.args[0]
        self.assertIn("Previous completed close: $28.58", card)
        self.assertNotIn("Spot:", card)
        self.assertNotIn("private broker data", card)


class TelegramRejectedHtmlTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("TOKEN", "fixture-token"), ("CHATID", "fixture-chat")):
            patcher = patch.object(telegram_notify, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def response(status, body):
        result = Mock(status_code=status)
        result.json.return_value = body
        return result

    def test_entity_rejection_retries_once_as_plain_text(self):
        rejected = self.response(400, {"ok": False, "description": "Bad Request: can't parse entities: x"})
        accepted = self.response(200, {"ok": True})
        with patch.object(telegram_notify.requests, "post", side_effect=[rejected, accepted]) as post, \
                patch("builtins.print"):
            self.assertTrue(telegram_notify.send("<b>CM</b> RSI < 30 &amp; S&P"))
        self.assertEqual(post.call_count, 2)
        retry = post.call_args_list[1].kwargs["json"]
        self.assertEqual(retry["parse_mode"], "")
        self.assertEqual(retry["text"], "CM RSI < 30 & S&P")

    def test_other_rejections_are_not_retried(self):
        rejected = self.response(400, {"ok": False, "description": "Bad Request: chat not found"})
        with patch.object(telegram_notify.requests, "post", return_value=rejected) as post, \
                patch("builtins.print"):
            self.assertFalse(telegram_notify.send("<b>CM</b>"))
        self.assertEqual(post.call_count, 1)

    def test_plain_text_messages_are_not_retried(self):
        rejected = self.response(400, {"ok": False, "description": "can't parse entities"})
        with patch.object(telegram_notify.requests, "post", return_value=rejected) as post, \
                patch("builtins.print"):
            self.assertFalse(telegram_notify.send("status", parse_mode=""))
        self.assertEqual(post.call_count, 1)


class StockCardEscapingTests(unittest.TestCase):
    def test_reasons_note_and_external_text_are_escaped(self):
        external = {
            "_llm_rationale": "Beats S&P/TSX; RSI < 70",
            "_llm_concerns": "Debt > peers & <b>",
            "_signal_origin": "NEWS-DRIVEN",
            "_catalyst": "M&A talk <rumour>",
            "_data_basis": "Daily bars & delayed",
            "ownership": {"top_holders": [{"name": "Dodge & Cox", "pct": 0.05}]},
        }
        with patch.object(telegram_notify, "send", return_value=True) as send:
            self.assertTrue(telegram_notify.send_signal(
                "BUY", "CM", entry_price=100, qty=10, stop_loss=95, take_profit=110,
                reasons=["Bullish news: S&P/TSX record", "close < EMA-20"],
                note="Q&A <note>", regime="CANADA NEUTRAL", external=external))
        card = send.call_args.args[0]
        assert_telegram_html_safe(self, card)
        self.assertIn("S&amp;P/TSX record", card)
        self.assertIn("close &lt; EMA-20", card)
        self.assertIn("M&amp;A talk &lt;rumour&gt;", card)
        self.assertIn("Dodge &amp; Cox", card)
        self.assertIn("<b>", card)

    def test_footer_keeps_its_own_formatting(self):
        footer = enrichment.format_card_footer({"_llm_rationale": "A & B"})
        self.assertIn("<b>🧠 AI Research:</b>", footer)
        self.assertIn("A &amp; B", footer)

    def test_ownership_holder_names_are_escaped(self):
        lines = ownership_data.format_ownership_lines({"top_holders": [{"name": "A<B> & Co", "pct": None}]})
        self.assertEqual(lines, ["  • Top: A&lt;B&gt; &amp; Co"])


if __name__ == "__main__":
    unittest.main()
