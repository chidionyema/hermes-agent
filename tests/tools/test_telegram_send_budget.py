"""The Telegram send budget and the callers that wait on it must agree.

On 2026-08-17 three cron jobs were failing for one reason. `ci-watchdog.sh` gave `hermes send`
15 seconds. This module was willing to make 3 attempts with 1s and 2s of backoff on top of
python-telegram-bot's default (effectively unbounded) HTTP timeouts. So the retry that exists to
survive a transient Telegram timeout could not finish before the caller killed it: the watchdog
died with exit 124, and `delivery-canary` recorded four real "Telegram send failed: Timed out"
entries. Nobody had ever compared the two numbers, because they lived in different files.

These tests compare them.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest

import tools.send_message_tool as smt

SCRIPTS_DIR = Path(os.path.expanduser("~/.hermes/scripts"))
_TIMEOUT_SEND = re.compile(r"timeout\s+(\d+)\s+hermes\s+send")


def test_tg_request_sets_every_timeout():
    """A Bot built without this helper inherits library defaults nobody chose."""
    pytest.importorskip("telegram", reason="python-telegram-bot is not in this venv")
    req = smt._tg_request()
    # HTTPXRequest keeps the values on its underlying client; assert via the timeout it applies.
    timeout = req._client.timeout
    for phase in ("connect", "read", "write", "pool"):
        assert getattr(timeout, phase) == smt.TELEGRAM_ATTEMPT_TIMEOUT_S, phase


def test_retry_stops_inside_the_budget():
    """The loop must not start an attempt that cannot finish before the budget runs out."""

    class _AlwaysTimesOut:
        def __init__(self):
            self.calls = 0

        async def send_message(self, **kwargs):
            self.calls += 1
            raise TimeoutError("Timed out")

    bot = _AlwaysTimesOut()
    # A budget smaller than one attempt timeout: the first failure must end it, not the third.
    old_budget = smt.TELEGRAM_SEND_BUDGET_S
    smt.TELEGRAM_SEND_BUDGET_S = 1.0
    try:
        with pytest.raises(TimeoutError):
            asyncio.run(smt._send_telegram_message_with_retry(bot, chat_id=1, text="x"))
    finally:
        smt.TELEGRAM_SEND_BUDGET_S = old_budget
    assert bot.calls == 1, f"budget ignored: made {bot.calls} attempts"


def test_retry_still_retries_when_the_budget_allows_it():
    """The guard must not have quietly disabled retrying altogether."""

    class _FailsOnce:
        def __init__(self):
            self.calls = 0

        async def send_message(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("Timed out")
            return "sent"

    bot = _FailsOnce()
    assert asyncio.run(smt._send_telegram_message_with_retry(bot, chat_id=1, text="x")) == "sent"
    assert bot.calls == 2


@pytest.mark.skipif(not SCRIPTS_DIR.is_dir(), reason="estate scripts not present")
def test_every_caller_waits_longer_than_the_budget():
    """A caller whose timeout is at or under the budget kills the retry it is waiting for."""
    offenders = []
    for path in SCRIPTS_DIR.glob("*.sh"):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for m in _TIMEOUT_SEND.finditer(text):
            if int(m.group(1)) <= smt.TELEGRAM_SEND_BUDGET_S:
                offenders.append(f"{path.name}: timeout {m.group(1)}")
    assert not offenders, (
        f"budget is {smt.TELEGRAM_SEND_BUDGET_S:.0f}s; these callers kill it early: {offenders}"
    )
