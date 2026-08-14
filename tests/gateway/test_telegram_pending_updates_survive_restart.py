"""Messages sent while the gateway is down must survive the restart.

2026-08-14: the founder reported "Otto is totally unresponsive to my chats".
gateway.log recorded only 8 inbound messages for the whole day. The cause was
``start_polling(drop_pending_updates=True)`` on the cold-start path: that flag
tells Telegram to BIN every update queued while the process was down, so a
message sent mid-restart is not delayed — it is destroyed, and never reaches a
log line at all.

That would be survivable if restarts were rare. They are not: gateway.source_watch
restarts the process on any source edit (210 times by 2026-08-14, three inside one
40-minute window), and at 21:59 the gateway came up with no connected platforms for
~14 minutes.

Both reconnect paths and the delete_webhook call already passed False; cold start
was the lone outlier, and it is the one path that runs on every restart.
"""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return

    telegram_mod = MagicMock()
    telegram_mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    telegram_mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    telegram_mod.constants.ChatType.GROUP = "group"
    telegram_mod.constants.ChatType.SUPERGROUP = "supergroup"
    telegram_mod.constants.ChatType.CHANNEL = "channel"
    telegram_mod.constants.ChatType.PRIVATE = "private"
    telegram_mod.error.NetworkError = type("NetworkError", (OSError,), {})
    telegram_mod.error.TimedOut = type("TimedOut", (OSError,), {})
    telegram_mod.error.BadRequest = type("BadRequest", (Exception,), {})

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, telegram_mod)
    sys.modules.setdefault("telegram.error", telegram_mod.error)


_ensure_telegram_mock()

from gateway.platforms.telegram import TelegramAdapter  # noqa: E402


@pytest.fixture(autouse=True)
def _no_auto_discovery(monkeypatch):
    async def _noop():
        return []

    monkeypatch.setattr("gateway.platforms.telegram.discover_fallback_ips", _noop)
    monkeypatch.setattr(
        "gateway.platforms.telegram.HTTPXRequest", lambda **kwargs: MagicMock()
    )


async def _connect_and_capture(monkeypatch):
    """Run connect() against a mocked PTB stack; return the start_polling kwargs."""
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="***"))

    monkeypatch.setattr(
        "gateway.status.acquire_scoped_lock",
        lambda scope, identity, metadata=None: (True, None),
    )
    monkeypatch.setattr(
        "gateway.status.release_scoped_lock", lambda scope, identity: None
    )

    captured = {}

    async def fake_start_polling(**kwargs):
        captured.update(kwargs)

    updater = SimpleNamespace(
        start_polling=AsyncMock(side_effect=fake_start_polling),
        stop=AsyncMock(),
        running=True,
    )
    bot = SimpleNamespace(set_my_commands=AsyncMock(), delete_webhook=AsyncMock())
    app = SimpleNamespace(
        bot=bot,
        updater=updater,
        add_handler=MagicMock(),
        initialize=AsyncMock(),
        start=AsyncMock(),
    )
    builder = MagicMock()
    builder.token.return_value = builder
    builder.request.return_value = builder
    builder.get_updates_request.return_value = builder
    builder.build.return_value = app
    monkeypatch.setattr(
        "gateway.platforms.telegram.Application",
        SimpleNamespace(builder=MagicMock(return_value=builder)),
    )
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    ok = await adapter.connect()
    assert ok is True, "connect() must succeed for this test to mean anything"
    return captured


@pytest.mark.asyncio
async def test_cold_start_does_not_discard_queued_messages(monkeypatch):
    """THE REGRESSION. If this flips back to True, chats silently vanish again."""
    monkeypatch.delenv("TELEGRAM_DROP_PENDING_UPDATES", raising=False)

    captured = await _connect_and_capture(monkeypatch)

    assert captured["drop_pending_updates"] is False, (
        "start_polling(drop_pending_updates=True) destroys every message sent "
        "while the gateway was down — see this module's docstring."
    )


@pytest.mark.asyncio
async def test_the_escape_hatch_still_works(monkeypatch):
    """A long outage may prefer losing stale commands to replaying them."""
    monkeypatch.setenv("TELEGRAM_DROP_PENDING_UPDATES", "1")

    captured = await _connect_and_capture(monkeypatch)

    assert captured["drop_pending_updates"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "  "])
async def test_unset_or_falsey_env_keeps_messages(monkeypatch, value):
    monkeypatch.setenv("TELEGRAM_DROP_PENDING_UPDATES", value)

    captured = await _connect_and_capture(monkeypatch)

    assert captured["drop_pending_updates"] is False
