"""Static compatibility checks for the founder-facing Telegram Bot API 10.4 widget.

Telegram's server enforces 32 KiB raw / 4 KiB zlib-9 HTML. A browser preview
passing does not mean a widget can be sent, so protect the actual source size.
"""
from html.parser import HTMLParser
from pathlib import Path
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]
WIDGET = ROOT / "plugins/platforms/telegram/widgets/otto_founder_compass.html"


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attributes = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.append((tag, dict(attrs)))


class FounderWidgetUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = WIDGET.read_bytes()
        cls.source = cls.raw.decode("utf-8")
        cls.parsed = Tags()
        cls.parsed.feed(cls.source)

    def test_telegram_size_budget(self):
        self.assertLessEqual(len(self.raw), 32768, "Telegram raw HTML limit")
        self.assertLessEqual(
            len(zlib.compress(self.raw, level=9)), 4096,
            "Telegram zlib-9 size limit",
        )

    def test_single_self_contained_document(self):
        self.assertEqual(self.parsed.tags.count("html"), 1)
        self.assertEqual(self.parsed.tags.count("body"), 1)
        self.assertEqual(self.parsed.tags.count("style"), 1)
        self.assertEqual(self.parsed.tags.count("script"), 1)
        for tag in ("form", "input", "textarea", "iframe", "img", "link"):
            self.assertNotIn(tag, self.parsed.tags)
        self.assertNotRegex(self.source, r"https?://|@import|src\s*=")

    def test_native_widget_contract_and_no_fake_data(self):
        for fragment in (
            "TelegramWidget", "chatData", "chatDataChanged",
            "W.request('brief')", "W.ready()", "textContent",
            "No verified estate briefing", "No connection to the Telegram bot",
        ):
            self.assertIn(fragment, self.source)
        for prohibited in (
            "fetch(", "XMLHttpRequest", "WebSocket", "eval(",
            "innerHTML", "navigator.clipboard", "sendMessage(",
            "localStorage", "sessionStorage",
        ):
            self.assertNotIn(prohibited, self.source)
        self.assertIn("W.chatType==='private'", self.source)
        self.assertIn("!W.isPreview", self.source)
        self.assertIn("!W.isForward", self.source)

    def test_interaction_is_read_only_and_works_without_backend(self):
        ids = {attrs["id"] for _tag, attrs in self.parsed.attributes if "id" in attrs}
        for item in ("refresh", "detailBtn", "details", "notice", "delivered", "active", "issues", "proof", "copyProof"):
            self.assertIn(item, ids)
        self.assertIn("aria-expanded", self.source)
        self.assertIn("W.copyText(", self.source)
        self.assertIn("addEventListener('click'", self.source)
        self.assertIn("Invalid evidence packet", self.source)
        self.assertIn("STALE SNAPSHOT", self.source)
        # No invented metric values: counters initialize to em dashes.
        self.assertRegex(self.source, r'id="delivered">—')
        self.assertRegex(self.source, r'id="active">—')
        self.assertRegex(self.source, r'id="issues">—')


if __name__ == "__main__":
    unittest.main()
