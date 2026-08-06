"""The origin scope has to be where the action actually runs, not where the update arrives.

`activity.source_scope` is only worth anything if it is still in force at the moment the row
is written. Two ways to get that wrong, one of which was the first attempt at this fix:

- **Scope the buffering handler.** `_handle_text_message` does not dispatch; it appends to
  `_pending_text_batches` and returns, and the real dispatch happens later in
  `_flush_text_batch` after a quiet period. A `with` block around the handler body would have
  exited before a single action ran, and every typed request would still have logged as
  unattributed — a fix that is green in unit tests and inert in production.
- **Scope a coroutine but lose it across the thread hop.** Estate actions run as
  `await asyncio.to_thread(handle_estate_action, ...)`.

So these tests drive the real handlers with a mocked Telegram surface and assert on
`activity.current_source()` read from *inside* the dispatched call — the same place the row is
written from. Nothing here asserts that the code contains a `with` statement.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)

from gateway.config import PlatformConfig
from gateway.operator_shell import activity
from gateway.platforms.telegram import TelegramAdapter


class _Btn:
    def __init__(self, text, callback_data=None):
        self.text = text
        self.callback_data = callback_data


class _Markup:
    def __init__(self, rows):
        self.inline_keyboard = rows


@pytest.fixture(autouse=True)
def _real_keyboards(monkeypatch):
    from gateway.platforms import telegram as tg

    monkeypatch.setattr(tg, "InlineKeyboardButton", _Btn)
    monkeypatch.setattr(tg, "InlineKeyboardMarkup", _Markup)


def _adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token", extra={}))
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


def _query(action: str = "st_status"):
    query = AsyncMock()
    query.id = "q-1"
    query.data = f"estate:{action}"
    query.message = MagicMock()
    query.message.chat_id = 12345
    query.message.message_id = 8781
    query.message.message_thread_id = None
    query.message.reply_markup = _Markup([[_Btn("🏠 Home", callback_data="estate:refresh")]])
    query.from_user = MagicMock()
    query.from_user.id = "777"
    query.from_user.first_name = "Founder"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return query


def _stub_message_plumbing(adapter, event):
    """Reduce the ingress to the one thing under test: what origin is in force at dispatch."""
    adapter._effective_update_message = MagicMock(return_value=MagicMock(text="/missions"))
    adapter._should_process_message = MagicMock(return_value=True)
    adapter._ensure_forum_commands = AsyncMock()
    adapter._build_message_event = MagicMock(return_value=event)
    adapter._clean_bot_trigger_text = MagicMock(side_effect=lambda t: t)
    adapter._cache_replied_media = AsyncMock()
    adapter._apply_telegram_group_observe_attribution = MagicMock(side_effect=lambda e: e)


# --- button tap -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_button_tap_is_recorded_as_a_button():
    """Read from inside the dispatched call, across the `to_thread` hop the real code uses."""
    adapter = _adapter()
    query = _query("st_status")
    seen = {}

    def _fake_action(action, request_id=""):
        seen["source"] = activity.current_source()
        return SimpleNamespace(text="Store: ok", buttons=[[("🏠 Home", "estate:refresh")]])

    update = MagicMock()
    update.callback_query = query
    with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False), patch(
        "gateway.operator_shell.estate.handle_estate_action", _fake_action
    ), patch(
        "gateway.operator_shell.proof.load_mission_card", return_value=None
    ):
        await adapter._handle_callback_query(update, MagicMock())

    assert seen.get("source") == "button"


@pytest.mark.asyncio
async def test_the_tap_scope_does_not_leak_past_the_update():
    adapter = _adapter()
    query = _query("st_status")
    update = MagicMock()
    update.callback_query = query
    with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False), patch(
        "gateway.operator_shell.estate.handle_estate_action",
        return_value=SimpleNamespace(text="ok", buttons=[]),
    ), patch("gateway.operator_shell.proof.load_mission_card", return_value=None):
        await adapter._handle_callback_query(update, MagicMock())

    assert activity.current_source() == "unknown"


# --- typed slash command --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_typed_slash_command_is_recorded_as_typed():
    """`/panel`, `/missions` and `/revert` all reach `handle_estate_action`.

    Before this, every one of them was logged as a tap.
    """
    adapter = _adapter()
    event = SimpleNamespace(text="/missions", media_urls=[], media_types=[])
    _stub_message_plumbing(adapter, event)
    seen = {}

    async def _capture(ev):
        seen["source"] = activity.current_source()

    adapter.handle_message = _capture

    update = MagicMock()
    update.update_id = 1
    await adapter._handle_command(update, MagicMock())

    assert seen.get("source") == "command"


# --- typed prose ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_typed_prose_is_recorded_as_chat():
    """Dispatch happens in `_flush_text_batch`, which is where the scope must live."""
    adapter = _adapter()
    event = SimpleNamespace(text="restart the gateway", media_urls=[], media_types=[])
    seen = {}

    async def _capture(ev):
        seen["source"] = activity.current_source()

    adapter.handle_message = _capture
    adapter._pending_text_batches["k"] = event
    adapter._text_batch_delay_seconds = 0.0
    adapter._text_batch_split_delay_seconds = 0.0

    await adapter._flush_text_batch("k")

    assert seen.get("source") == "chat"


@pytest.mark.asyncio
async def test_the_buffering_handler_does_not_dispatch():
    """The reason the scope is not on `_handle_text_message`.

    This is the regression guard for the wrong fix: if this handler ever starts dispatching
    directly, the scope has to move with it or typed prose silently reverts to unattributed.
    """
    adapter = _adapter()
    event = SimpleNamespace(text="hello", media_urls=[], media_types=[])
    _stub_message_plumbing(adapter, event)
    adapter._should_observe_unmentioned_group_message = MagicMock(return_value=False)
    adapter.handle_message = AsyncMock()
    # Only the session-key derivation is stubbed; the buffering itself stays real, because
    # "it buffers rather than dispatching" is the whole claim under test.
    adapter._text_batch_key = MagicMock(return_value="k")

    update = MagicMock()
    update.update_id = 1
    await adapter._handle_text_message(update, MagicMock())

    adapter.handle_message.assert_not_awaited()
    # It buffered instead — the dispatch is deferred to _flush_text_batch.
    assert adapter._pending_text_batches
    for task in list(adapter._pending_text_batch_tasks.values()):
        task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await task


# --- the scope helper must never break an update --------------------------------------------


def test_the_scope_helper_degrades_to_a_no_op():
    """Attribution failing must cost a label, never an inbound message."""
    from gateway.platforms import telegram as tg

    with patch.dict(sys.modules, {"gateway.operator_shell.activity": None}):
        with tg._source_scope("button"):
            pass  # must not raise
