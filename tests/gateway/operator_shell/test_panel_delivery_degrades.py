"""A failed panel delivery must degrade to plain text, never strand the operator.

The estate callback writes "⏳ Loading…" into the card BEFORE running the
action. Every delivery attempt afterwards was MarkdownV2, and the last one
ended in `except Exception: pass` — so a single formatting fault left the
operator staring at the loading placeholder permanently, with nothing logged
and no way to tell whether the action had run.
"""

from __future__ import annotations

import asyncio

import pytest

from gateway.platforms.telegram import TelegramAdapter


class _Adapter(TelegramAdapter):
    """Bare adapter: no network, records what delivery was attempted."""

    def __init__(self, fail_markdown=False, fail_all=False):
        self.attempts = []
        self._fail_markdown = fail_markdown
        self._fail_all = fail_all

    @property
    def name(self) -> str:  # base class defines it read-only
        return "telegram"

    async def _send_message_with_thread_fallback(self, **kwargs):
        self.attempts.append(kwargs)
        if self._fail_all:
            raise RuntimeError("telegram unreachable")
        if self._fail_markdown and kwargs.get("parse_mode") is not None:
            raise RuntimeError("Bad Request: can't parse entities")
        return object()


def _run(coro):
    return asyncio.run(coro)


PANEL = "*Fleet* — 3 up\n_4m ago_"


def test_happy_path_sends_markdownv2_once():
    a = _Adapter()
    assert _run(a.deliver_panel_degrading("42", PANEL)) is True
    assert len(a.attempts) == 1
    assert a.attempts[0]["parse_mode"] is not None
    # markup preserved through the send
    assert "text" in a.attempts[0]


def test_parse_failure_degrades_to_plain_text():
    """The whole point: a formatting fault must still reach the operator."""
    a = _Adapter(fail_markdown=True)
    assert _run(a.deliver_panel_degrading("42", PANEL)) is True

    assert len(a.attempts) == 2, "no plain-text retry was made"
    second = a.attempts[1]
    assert second["parse_mode"] is None
    # markers stripped so the operator does not see raw syntax
    assert "*" not in second["text"]
    assert "Fleet" in second["text"]


def test_total_failure_reports_false_instead_of_passing_silently():
    a = _Adapter(fail_all=True)
    assert _run(a.deliver_panel_degrading("42", PANEL)) is False
    assert len(a.attempts) == 2, "should still have tried both modes"


def test_thread_id_is_forwarded_only_when_present():
    a = _Adapter()
    _run(a.deliver_panel_degrading("42", PANEL, None, 77))
    assert a.attempts[0]["message_thread_id"] == 77

    b = _Adapter()
    _run(b.deliver_panel_degrading("42", PANEL))
    assert "message_thread_id" not in b.attempts[0], (
        "passing a null thread_id would change routing for non-forum chats"
    )


def test_keyboard_survives_the_degraded_send():
    """Degrading must not cost the operator their buttons."""
    a = _Adapter(fail_markdown=True)
    markup = {"inline_keyboard": [[{"text": "Refresh", "callback_data": "estate:refresh"}]]}
    _run(a.deliver_panel_degrading("42", PANEL, markup))
    assert a.attempts[1]["reply_markup"] is markup
