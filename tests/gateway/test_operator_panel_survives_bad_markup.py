"""A panel with unbalanced markup must still reach the operator.

Measured live 2026-08-14 23:30:12 in ~/.hermes/logs/gateway.log::

    ERROR gateway.run: operator view send failed: Can't parse entities:
    can't find end of bold entity at byte offset 1736
      File ".../gateway/platforms/telegram.py", line 6806, in send_operator_panel

The operator had waited 277 seconds for that turn and received NOTHING.
Telegram refuses the WHOLE message when markup is unbalanced — there is no
partial render — and `send_operator_panel` had no retry, so the exception
propagated and the panel was dropped. From the chat that is indistinguishable
from the agent ignoring you, which is exactly the complaint that started this
work ("Otto is totally unresponsive to my chats").

The draft path (`:3298`) and the streaming send loop already do the
MarkdownV2 → plain-text retry. The panel path was the outlier.
"""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


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

from gateway.config import PlatformConfig  # noqa: E402
from gateway.platforms.telegram import TelegramAdapter  # noqa: E402


class BadRequest(Exception):
    """Stands in for telegram.error.BadRequest.

    `_is_bad_request_error` matches on the class NAME as well as isinstance,
    precisely so a stub like this is recognised.
    """


PANEL_TEXT = "*Cockpit* · *BLOCKED*\nBlocker: an unclosed *bold span"


def _adapter(monkeypatch):
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="***"))
    monkeypatch.setattr(
        "gateway.operator_shell.proof.load_mission_card", lambda: {}
    )
    saved = {}
    monkeypatch.setattr(
        "gateway.operator_shell.proof.save_mission_card",
        lambda chat_id, mid, thread=None: saved.update(
            chat_id=chat_id, message_id=mid
        ),
    )
    adapter._bot = SimpleNamespace(pin_chat_message=AsyncMock())
    return adapter, saved


def _event():
    return SimpleNamespace(
        source=SimpleNamespace(chat_id="8868748055", thread_id=None)
    )


@pytest.mark.asyncio
async def test_entity_parse_failure_is_resent_unformatted(monkeypatch, caplog):
    """THE REGRESSION. If the retry goes, the operator silently gets nothing."""
    adapter, saved = _adapter(monkeypatch)
    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        if kwargs.get("parse_mode"):
            raise BadRequest(
                "Can't parse entities: can't find end of bold entity "
                "at byte offset 1736"
            )
        return SimpleNamespace(message_id=4242)

    monkeypatch.setattr(
        adapter, "_send_message_with_thread_fallback", fake_send
    )

    await adapter.send_operator_panel(
        _event(), SimpleNamespace(text=PANEL_TEXT, pin_edit=False)
    )

    assert len(calls) == 2, "the panel must be retried, not dropped"
    assert calls[1].get("parse_mode") is None
    assert calls[1]["text"] == PANEL_TEXT, "the retry carries the same words"
    assert saved["message_id"] == "4242", "the retried panel is still the card"


@pytest.mark.asyncio
async def test_the_buttons_survive_the_downgrade(monkeypatch):
    """reply_markup is independent of parse_mode — a plain panel still works."""
    adapter, _ = _adapter(monkeypatch)
    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        if kwargs.get("parse_mode"):
            raise BadRequest("Can't parse entities: unexpected end of string")
        return SimpleNamespace(message_id=1)

    monkeypatch.setattr(
        adapter, "_send_message_with_thread_fallback", fake_send
    )
    monkeypatch.setattr(
        adapter, "_panel_keyboard_from_view", lambda view: "KEYBOARD"
    )

    await adapter.send_operator_panel(
        _event(), SimpleNamespace(text=PANEL_TEXT, pin_edit=False)
    )

    assert calls[1]["reply_markup"] == "KEYBOARD"


@pytest.mark.asyncio
async def test_unrelated_failures_still_propagate(monkeypatch):
    """The retry is for markup only. A dead chat must not be masked as sent."""
    adapter, _ = _adapter(monkeypatch)
    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        raise BadRequest("Chat not found")

    monkeypatch.setattr(
        adapter, "_send_message_with_thread_fallback", fake_send
    )

    with pytest.raises(BadRequest):
        await adapter.send_operator_panel(
            _event(), SimpleNamespace(text=PANEL_TEXT, pin_edit=False)
        )
    assert len(calls) == 1, "a non-markup failure must not be retried"


@pytest.mark.asyncio
async def test_the_healthy_path_sends_once_with_markdown(monkeypatch):
    adapter, _ = _adapter(monkeypatch)
    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(message_id=7)

    monkeypatch.setattr(
        adapter, "_send_message_with_thread_fallback", fake_send
    )

    await adapter.send_operator_panel(
        _event(), SimpleNamespace(text=PANEL_TEXT, pin_edit=False)
    )

    assert len(calls) == 1
    # Compared against the module's own constant: under the stub above
    # `telegram.constants` resolves to the stub module, so a hardcoded
    # "MarkdownV2" would be asserting against the mock, not the behaviour.
    from gateway.platforms.telegram import ParseMode

    assert calls[0]["parse_mode"] == ParseMode.MARKDOWN_V2
    assert calls[0]["parse_mode"] is not None


def test_the_log_line_names_the_offending_markup():
    """The offset Telegram reports is useless without the text it indexes."""
    err = BadRequest(
        "Can't parse entities: can't find end of bold entity at byte offset 20"
    )
    text = "0123456789" * 3 + "*UNCLOSED-BOLD-HERE"

    out = TelegramAdapter._entity_error_context(err, text)

    assert "offset=20" in out
    assert "UNCLOSED-BOLD-HERE" in out


def test_context_never_throws_on_a_message_without_an_offset():
    """Diagnostics run inside an except block; they may not raise."""
    assert (
        TelegramAdapter._entity_error_context(BadRequest("Chat not found"), "x")
        == "<no offset in error>"
    )


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Can't parse entities: unexpected end of string", True),
        ("can't parse entities at byte offset 4", True),
        ("Chat not found", False),
        ("Message thread not found", False),
        ("", False),
    ],
)
def test_entity_error_classifier(message, expected):
    assert TelegramAdapter._is_entity_parse_error(BadRequest(message)) is expected
