"""When the platform names a wait, the gateway must wait that long.

Measured on 2026-07-31: 56 `Flood control exceeded` rejections in one day, with stated
waits of 13, 28, 32, 61, 66, 69, 86, 91, 96, 98, 109, 114, 140 (×6), 151, 156, 166, 170,
171, 175, 178, 187, 202, 225, 231 (×3), 232 (×4), 235 (×6) and 270 seconds. Against those,
the code waited:

  * stream_consumer edit path      — a local interval doubled to a 10s ceiling
  * stream_consumer fallback send  — a hardcoded 3.0s
  * telegram `_edit_overflow_split` — nothing at all; the number was never read

Every retry issued inside a penalty window is itself a rejected request, so short lockouts
compound into long ones. During a lockout Telegram refuses everything in that chat, which
is what the operator experiences as "the /commands not working, broken intermittently
after prolonged use" (founder, 2026-07-31) — the flood is caused by the streamer but the
casualty is the whole chat.

`retry_after` is threaded SendResult → consumer so the number survives the trip.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)

from gateway.platforms.base import SendResult
from gateway.stream_consumer import GatewayStreamConsumer

_stated = GatewayStreamConsumer._stated_retry_after


# --- reading the number ---------------------------------------------------------------


def test_the_structured_field_is_used_when_present():
    assert _stated(SendResult(success=False, error="flood", retry_after=140.0)) == 140.0


@pytest.mark.parametrize(
    "err,expected",
    [
        ("flood_control:140.0", 140.0),
        ("Flood control exceeded. Retry in 270 seconds", 270.0),
        ("Flood control exceeded. Retry in 13 seconds", 13.0),
        ("RetryAfter: retry after 86", 86.0),
    ],
)
def test_the_number_is_recovered_from_the_wording_telegram_actually_uses(err, expected):
    """Both shapes are in the logs verbatim, and the adapter encodes long waits as
    `flood_control:N` — so parsing only one of them loses the number half the time."""
    assert _stated(SendResult(success=False, error=err)) == expected


@pytest.mark.parametrize("err", ["flood control exceeded", "rate limited", "", "boom"])
def test_no_number_means_none_never_zero(err):
    """None must not read as "retry immediately" — that would turn a missing number into
    the most aggressive possible retry, the exact opposite of the intent."""
    assert _stated(SendResult(success=False, error=err)) is None


def test_a_nonsense_retry_after_does_not_crash_the_stream():
    assert _stated(SendResult(success=False, error="flood", retry_after="soon")) is None
    assert _stated(SimpleNamespace()) is None


# --- obeying it -----------------------------------------------------------------------


def _consumer():
    return GatewayStreamConsumer(SimpleNamespace(name="telegram"), "8868748055")


def _backoff_for(stated_error, *, retry_after=None, start=1.0):
    """The interval the real code adopts for this flood result.

    Calls `_flood_backoff` itself rather than re-deriving it here: both retry paths route
    through that one method precisely so a test cannot pass against a copy of the rule
    while the shipped rule says something else.
    """
    c = _consumer()
    c._current_edit_interval = start
    return c._flood_backoff(
        SendResult(success=False, error=stated_error, retry_after=retry_after)
    )


def test_a_stated_wait_beats_the_ten_second_ceiling():
    """The defect in one line: told 140s, the old code waited 10s and retried 14 times."""
    assert _backoff_for("flood_control:140.0") > 10.0


def test_the_wait_is_capped_so_a_stream_cannot_look_hung():
    """270s of silence is indistinguishable from a crash; the fallback final send exists
    for this case, so we wait the ceiling and let the strike counter promote us."""
    c = _consumer()
    assert _backoff_for("Flood control exceeded. Retry in 270 seconds") == c._MAX_FLOOD_BACKOFF


def test_a_short_stated_wait_never_shrinks_the_local_backoff():
    """Telegram's number is a floor, not a licence to retry sooner than we already meant
    to — a 1s reply to repeated rejection is how the penalty grew in the first place."""
    assert _backoff_for("flood_control:1.0", start=8.0) == 10.0


def test_with_no_number_the_local_doubling_still_governs():
    """Platforms that state nothing (and every non-Telegram adapter) must be unaffected."""
    assert _backoff_for("rate limited", start=2.0) == 4.0


def test_the_fallback_send_wait_tracks_the_same_number():
    """The fallback send slept a hardcoded 3s — the one path guaranteed to run *after*
    the stream already tripped the limit, so it was the most certain to be rejected."""
    c = _consumer()
    wait = c._flood_backoff(SendResult(success=False, error="flood_control:86.0"))
    assert wait > 3.0 and wait == c._MAX_FLOOD_BACKOFF


# --- the adapter must hand the number up ----------------------------------------------


def test_the_telegram_adapter_extracts_both_shapes():
    from gateway.platforms.telegram import _retry_after_seconds

    class _RetryAfter(Exception):
        retry_after = 140

    assert _retry_after_seconds(_RetryAfter()) == 140.0
    assert _retry_after_seconds(Exception("Flood control exceeded. Retry in 270 seconds")) == 270.0
    assert _retry_after_seconds(Exception("Bad Request: message is not modified")) is None


def test_send_result_carries_retry_after_by_default_none():
    """Adapters that never set it must not start claiming a zero-second wait."""
    assert SendResult(success=False, error="x").retry_after is None
