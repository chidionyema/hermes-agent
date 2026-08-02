"""The estate callback must never leave the operator with a card they cannot use.

Measured on the live gateway, 2026-07-31: "most of the panel features dont work, just says
loading and nothing happens" (founder). The mechanism, from the log and the code:

1. The loading edit passed `reply_markup=None`. The estate card is a single pinned window that
   every panel edits in place, so stripping its keyboard removes the whole cockpit — there is
   no second message to fall back to and no button left to tap.
2. When the action then failed, the `except` handler called `query.answer()` a second time.
   Telegram rejects that with "Query is too old and response timeout expired or query id is
   invalid" — so the recovery path raised, and the card stayed on "⏳ Loading…" forever.
   36 such failures were logged on 2026-07-31, spread across the whole day.

These tests drive the real `_handle_callback_query` with a mocked Telegram surface, so they
assert what the operator's screen ends up showing, not what the code intends.
"""

from __future__ import annotations

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
from gateway.platforms.telegram import TelegramAdapter


class _Btn:
    """Stand-in for InlineKeyboardButton that actually keeps its data.

    `tests/gateway/conftest.py:227` installs a MagicMock `telegram` package before any test
    module imports, so the adapter's real `InlineKeyboardMarkup` is a MagicMock whose
    `.inline_keyboard` is another MagicMock. A keyboard assertion against that passes no
    matter what the handler built — the exact "test that passes because nothing rendered"
    this suite exists to avoid. These two hold the structure so the assertion has teeth.
    """

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


OLD_KEYBOARD = _Markup([[_Btn("🏠 Home", callback_data="estate:refresh")]])


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
    query.message.reply_markup = OLD_KEYBOARD
    query.from_user = MagicMock()
    query.from_user.id = "777"
    query.from_user.first_name = "Founder"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return query


async def _dispatch(adapter, query):
    update = MagicMock()
    update.callback_query = query
    with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
        await adapter._handle_callback_query(update, MagicMock())


def _edits(query):
    """(text, reply_markup) for every card edit, in order."""
    return [(c.kwargs.get("text", ""), c.kwargs.get("reply_markup"))
            for c in query.edit_message_text.call_args_list]


def _buttons(markup):
    return [b for row in (getattr(markup, "inline_keyboard", None) or []) for b in row]


@pytest.mark.asyncio
async def test_slow_action_from_mission_card_does_not_overwrite_with_loading():
    """Mission card stays put; Store arrives as a separate message."""
    adapter = _adapter()
    query = _query("st_status")
    view = SimpleNamespace(
        text="Store: ok",
        buttons=[[("🏠 Home", "estate:refresh")]],
        pin_edit=False,
    )
    mission = SimpleNamespace(
        text="Mission home",
        buttons=[[("🛒 Store", "estate:st_status")]],
        pin_edit=True,
    )
    adapter._send_message_with_thread_fallback = AsyncMock()

    with patch(
        "gateway.operator_shell.proof.load_mission_card",
        return_value={"chat_id": "12345", "message_id": "8781"},
    ), patch(
        "gateway.operator_shell.estate.handle_estate_action", return_value=view
    ), patch(
        "gateway.operator_shell.estate.render_panel_view", return_value=mission
    ):
        await _dispatch(adapter, query)

    # No Loading overwrite of the pinned card
    for text, _ in _edits(query):
        assert "Probing" not in text and "Loading" not in text, text
    adapter._send_message_with_thread_fallback.assert_awaited()
    sent_text = adapter._send_message_with_thread_fallback.await_args.kwargs.get("text", "")
    assert "Store: ok" in sent_text


@pytest.mark.asyncio
async def test_the_loading_edit_keeps_the_keyboard():
    """The brick. `reply_markup=None` here is what emptied the pinned card."""
    adapter = _adapter()
    query = _query()
    view = SimpleNamespace(text="Store: ok", buttons=[[("🏠 Home", "estate:refresh")]])

    # Not the mission card → loading edit still applies on ephemeral panels.
    with patch(
        "gateway.operator_shell.proof.load_mission_card", return_value={}
    ), patch(
        "gateway.operator_shell.estate.handle_estate_action", return_value=view
    ):
        await _dispatch(adapter, query)

    loading_text, loading_markup = _edits(query)[0]
    assert "Probing store" in loading_text, _edits(query)
    assert loading_markup is OLD_KEYBOARD, "the loading edit dropped the operator's only buttons"


@pytest.mark.asyncio
async def test_slow_action_loading_names_the_probe_and_keeps_buttons():
    """Opaque Loading on a 60s Store probe reads as hung — name the work + ETA."""
    adapter = _adapter()
    query = _query("st_health")
    view = SimpleNamespace(text="Health: ok", buttons=[[("🏠 Home", "estate:refresh")]])

    with patch("gateway.operator_shell.estate.handle_estate_action", return_value=view):
        await _dispatch(adapter, query)

    loading_text, loading_markup = _edits(query)[0]
    assert "Probing store health" in loading_text
    assert "1–2 min" in loading_text or "1-2 min" in loading_text
    assert loading_markup is OLD_KEYBOARD
    assert "Health: ok" in _edits(query)[-1][0]


@pytest.mark.asyncio
async def test_fast_action_keeps_short_loading_copy():
    adapter = _adapter()
    query = _query("refresh")
    view = SimpleNamespace(text="Mission", buttons=[[("🏠 Home", "estate:refresh")]])

    with patch("gateway.operator_shell.estate.handle_estate_action", return_value=view):
        await _dispatch(adapter, query)

    loading_text, _ = _edits(query)[0]
    assert "Loading" in loading_text
    assert "Probing" not in loading_text


@pytest.mark.asyncio
async def test_a_failing_action_leaves_a_card_with_working_buttons():
    """Not "an error was reported" — the screen must still be a cockpit afterwards."""
    adapter = _adapter()
    query = _query("st_status")

    with patch("gateway.operator_shell.estate.handle_estate_action",
               side_effect=RuntimeError("stripe probe timed out")):
        await _dispatch(adapter, query)

    final_text, final_markup = _edits(query)[-1]
    assert "Loading" not in final_text, "the card was left spinning on a dead action"
    assert "Action failed" in final_text and "st_status" in final_text
    assert "stripe probe timed out" in final_text, "the operator cannot see why it failed"
    cbs = [b.callback_data for b in _buttons(final_markup)]
    assert cbs, "the failure card shipped no buttons — that is the bricked cockpit"
    assert any(c.startswith("estate:") for c in cbs), cbs


@pytest.mark.asyncio
async def test_a_failing_action_does_not_answer_the_query_twice():
    """The second answer() raises "Query is too old", *inside* the handler meant to contain
    failures — which is why the recovery edit never ran."""
    adapter = _adapter()
    query = _query()

    with patch("gateway.operator_shell.estate.handle_estate_action",
               side_effect=RuntimeError("boom")):
        await _dispatch(adapter, query)

    assert query.answer.await_count == 1, query.answer.await_args_list


@pytest.mark.asyncio
async def test_a_query_that_expired_before_the_handler_ran_still_does_the_work():
    """answer() throws when the loop was busy >15s. That is a congested moment, not a reason
    to abandon the tap — the card edit does not need the query id."""
    adapter = _adapter()
    query = _query()
    query.answer = AsyncMock(side_effect=RuntimeError("Query is too old"))
    view = SimpleNamespace(text="Store: ok", buttons=[[("🏠 Home", "estate:refresh")]])

    with patch("gateway.operator_shell.estate.handle_estate_action", return_value=view) as h:
        await _dispatch(adapter, query)

    h.assert_called_once()
    assert "Store: ok" in _edits(query)[-1][0]


@pytest.mark.asyncio
async def test_an_unanswerable_expired_query_that_then_fails_still_restores_the_card():
    """Both failures at once — the state the founder actually hit. The toast is lost either
    way; the card is the only recovery that reaches the screen."""
    adapter = _adapter()
    query = _query("pd_cron")
    query.answer = AsyncMock(side_effect=RuntimeError("Query is too old"))

    with patch("gateway.operator_shell.estate.handle_estate_action",
               side_effect=RuntimeError("boom")):
        await _dispatch(adapter, query)

    assert query.answer.await_count == 2, "the retry ack was skipped even though none landed"
    final_text, final_markup = _edits(query)[-1]
    assert "Action failed" in final_text
    assert _buttons(final_markup), "no way out of the failure card"
