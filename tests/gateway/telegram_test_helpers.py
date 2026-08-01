"""Shared assertions for the Telegram adapter tests.

`telegram` is one of three different things depending on which test module got
imported first, and `ParseMode.MARKDOWN_V2` looks different in each:

1. the real python-telegram-bot package (installed in ``venv/``) — a ``str``
   enum member that reprs as ``<ParseMode.MARKDOWN_V2>``;
2. a module mock whose ``telegram.constants`` is a namespace configured with
   plain strings — ``'MarkdownV2'``;
3. a module mock that registers the SAME MagicMock under ``telegram`` and
   ``telegram.constants`` (what ``tests/gateway/conftest.py`` and the per-file
   ``_ensure_telegram_mock()`` helpers do), so ``from telegram.constants import
   ParseMode`` lands on the auto-created ``mod.ParseMode`` and never sees the
   configured ``mod.constants.ParseMode`` — an unconfigured MagicMock.

That is why ``assert "MARKDOWN_V2" in repr(parse_mode)`` was order-dependent:
it holds in world 1, holds VACUOUSLY in world 3 (the string is just the mock's
auto-generated name), and fails in world 2.

Compare by identity against the constant the adapter module is actually holding
instead.  It is exact in all three worlds and still fails if production sends
``ParseMode.HTML``, ``None``, or a hand-rolled string.
"""


def assert_markdown_v2(parse_mode) -> None:
    """Assert the adapter sent the MARKDOWN_V2 constant it imported."""
    # Imported at call time, not module scope: gateway.platforms.telegram
    # rebinds the module-global ParseMode inside _ensure_imports().
    import gateway.platforms.telegram as tg

    assert parse_mode is tg.ParseMode.MARKDOWN_V2, (
        f"expected ParseMode.MARKDOWN_V2 ({tg.ParseMode.MARKDOWN_V2!r}), "
        f"got {parse_mode!r}"
    )
