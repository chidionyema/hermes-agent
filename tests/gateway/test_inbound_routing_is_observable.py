"""Routing verdicts must say WHAT was routed, and must log the allow case.

2026-08-14. The operator reported "Otto is totally unresponsive to my chats".
The log recorded 8 `pre_gateway_dispatch skip` lines and nothing else, which left
two unanswerable questions:

  1. WHAT was sent? The skip line carried only a reason, so "a regex is eating my
     real questions" could not be confirmed or refuted from the log. It had to be
     tested by hand, replaying the operator's sentences through the matchers.
     (The answer turned out to be no — every interception was correct for what was
     actually sent: menu taps and short acks.)
  2. Did ANY message reach the agent? There was no log line on the allow path at
     all, so "reached the agent" could only be inferred from the ABSENCE of a
     skip — and absence is indistinguishable from the message never arriving.
     It never had arrived: they were being deleted by cold-start polling
     (see tests/gateway/test_telegram_pending_updates_survive_restart.py).

These tests pin the shape helper only — cheap, no gateway construction.
"""

import pytest

from gateway.run import _MSG_SHAPE_PREFIX, _msg_shape


def test_a_menu_tap_is_distinguishable_from_a_real_question():
    tap = _msg_shape("/panel")
    question = _msg_shape("why is the mission blocked and what do I do about it?")

    assert "len=6" in tap
    assert "/panel" in tap
    assert "why is the mission blocked" in question
    assert tap != question


def test_long_messages_are_truncated_not_transcribed():
    """The log must not become a wholesale transcript of the operator's chat."""
    secret_tail = "SENSITIVE-TAIL-MUST-NOT-APPEAR"
    text = ("a" * _MSG_SHAPE_PREFIX) + " " + secret_tail

    out = _msg_shape(text)

    assert secret_tail not in out
    assert out.endswith("…")
    # The true length is still reported, so truncation is visible, not silent.
    assert f"len={len(text)}" in out


def test_short_messages_are_not_marked_truncated():
    out = _msg_shape("ok")
    assert out.endswith("'ok'"), out
    assert "…" not in out


def test_whitespace_is_flattened_so_one_message_is_one_log_line():
    out = _msg_shape("line one\nline two\r\n\tline three")
    assert "\n" not in out and "\r" not in out and "\t" not in out
    assert "line one line two line three" in out


@pytest.mark.parametrize("value", [None, 42, object(), b"bytes"])
def test_non_text_never_raises(value):
    """This runs on the hot path of every inbound message; it may not throw."""
    assert _msg_shape(value) == "len=0 <non-text>"


@pytest.mark.parametrize("value", ["", "   ", "\n\t "])
def test_empty_is_labelled(value):
    assert _msg_shape(value) == "len=0 <empty>"
