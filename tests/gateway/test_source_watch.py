"""The auto-reloader must not make the estate look broken for doing its job.

`source_watch` restarts the gateway by SIGTERMing itself. The shutdown handler classifies an
unmarked SIGTERM as *unexpected* and exits 1; launchd records status 1; `verify_estate.sh`
reads that back as "ai.hermes.gateway last exit=1 — job is failing every run" and the whole
estate verdict flips to DEGRADED. Measured live on 2026-07-31: a green estate went DEGRADED
purely because the watcher had reloaded the gateway after a source edit.

A source-of-truth probe that goes red on correct behaviour is worse than no probe — it trains
the eye to ignore the red that matters. So the reload declares itself planned first.
"""

import os
import signal
from unittest import mock

from gateway import source_watch


def test_planned_restart_marks_before_it_signals():
    """The marker must be written BEFORE the SIGTERM, or the handler misses it.

    The handler consumes the marker while handling the signal, so a marker written after
    would arrive too late and the exit would still be 1.
    """
    order = []
    with mock.patch("gateway.status.write_takeover_marker",
                    side_effect=lambda pid: order.append(("mark", pid))), \
            mock.patch("os.kill", side_effect=lambda pid, sig: order.append(("kill", pid, sig))):
        source_watch.signal_planned_restart()

    assert [step[0] for step in order] == ["mark", "kill"], order
    assert order[0][1] == os.getpid(), "the marker must name this process"
    assert order[1][1] == os.getpid()
    assert order[1][2] == signal.SIGTERM, "graceful shutdown, not SIGKILL — sessions drain"


def test_the_marker_targets_self_so_the_handler_consumes_it():
    """End-to-end on the real marker: write it, then let the consumer match it.

    This is the assertion that actually pins the exit code. `consume_takeover_marker_for_self`
    is what the shutdown handler calls to decide between exit 0 and exit 1.
    """
    from gateway import status

    status.clear_takeover_marker()
    try:
        with mock.patch("os.kill"):
            source_watch.signal_planned_restart()
        assert status.consume_takeover_marker_for_self() is True, (
            "handler would not recognise the reload as planned, so it exits 1 and the "
            "estate probe reports DEGRADED"
        )
    finally:
        status.clear_takeover_marker()


def test_restart_still_signals_when_the_marker_cannot_be_written():
    """A failed marker write costs a false red, not the restart. Never trade one for the other."""
    with mock.patch("gateway.status.write_takeover_marker", side_effect=OSError("read-only")), \
            mock.patch("os.kill") as killed:
        source_watch.signal_planned_restart()
    killed.assert_called_once_with(os.getpid(), signal.SIGTERM)


def test_unrelated_sigterm_is_still_treated_as_unexpected():
    """The fix must not blanket-silence real crashes.

    With no marker written, the consumer must return False so an unexpected SIGTERM still
    exits non-zero and stays visible to the probe.
    """
    from gateway import status

    status.clear_takeover_marker()
    assert status.consume_takeover_marker_for_self() is False
