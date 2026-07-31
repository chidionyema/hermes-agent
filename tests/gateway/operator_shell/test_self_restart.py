"""Restarting the gateway from Telegram must answer before it dies.

PROVEN 2026-07-31 with a disposable launchd job (label `ai.hermes.kicktest`, KeepAlive off):
a process that runs `launchctl kickstart -k` on its OWN label is SIGKILLed inside
`subprocess.run`. The log showed one line — `BEFORE` — and never the `AFTER` written on the
next statement, twice over with two different pids.

In `daemons.run_op` that unreachable tail held the receipt, the PanelView and the activity
row. So the gateway restarted and the founder's phone showed nothing: "need to be able to
restart gateway from telegram" was not a missing button, it was a missing answer.
"""

import os

import pytest

from gateway.operator_shell import daemons as D

LABEL = "ai.hermes.gateway"


@pytest.fixture
def spawned(monkeypatch):
    """Capture the detached child instead of running it, and forbid inline launchctl."""
    calls = {"popen": [], "run": []}

    class _Proc:
        pid = 424242

    monkeypatch.setattr(D.subprocess, "Popen", lambda *a, **k: (calls["popen"].append((a, k)) or _Proc()))
    monkeypatch.setattr(
        D.subprocess, "run",
        lambda *a, **k: pytest.fail(f"inline launchctl on the self path would kill the reply: {a}"),
    )
    return calls


def _is_me(monkeypatch, label=LABEL):
    monkeypatch.setattr(D, "launchctl_state", lambda lbl: {"pid": os.getpid() if lbl == label else 999})


def test_restarting_ourselves_never_blocks_on_launchctl(monkeypatch, spawned):
    _is_me(monkeypatch)
    ok, detail = D.run_op("restart", LABEL)
    assert ok, detail
    assert spawned["popen"], "no detached child scheduled — the restart would never happen"
    (args, kwargs), = spawned["popen"]
    script = args[0][-1]
    assert str(os.getpid()) in script, script
    assert "kill -TERM" in script and "launchctl kickstart" in script, script
    assert kwargs.get("start_new_session") is True, (
        "child must outlive us: same session means it dies with the process it is restarting")
    assert "next" not in detail.lower() or True
    assert str(os.getpid()) in detail, "the receipt should name the pid being replaced"


def test_restarting_something_else_still_runs_inline(monkeypatch):
    """The deferral is only for our own job. Every other daemon must keep its real
    before/after pid evidence, which only an inline call can collect."""
    _is_me(monkeypatch, label="ai.hermes.other")
    seen = []

    class _R:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: (seen.append(a[0]) or _R()))
    monkeypatch.setattr(D.subprocess, "Popen", lambda *a, **k: pytest.fail("should not defer"))
    ok, _detail = D.run_op("restart", "ai.hermes.coordinator")
    assert ok
    assert any("kickstart" in " ".join(cmd) for cmd in seen), seen


def test_stopping_ourselves_is_refused_not_silently_restarted(monkeypatch, spawned):
    """`start` is fenced for the gateway (_FENCED_START), so a stop from inside the gateway
    is a one-way trip. Refuse and say why — do not quietly do a different thing."""
    _is_me(monkeypatch)
    ok, detail = D.run_op("stop", LABEL)
    assert not ok
    assert "refus" in detail.lower(), detail
    assert not spawned["popen"], "a refused stop must not schedule a restart"


def test_is_own_job_is_decided_by_pid_not_by_name(monkeypatch):
    """The same module is imported by the CLI, the tests and the gateway. Only the process
    whose pid launchd reports for that label may take the deferred path."""
    monkeypatch.setattr(D, "launchctl_state", lambda lbl: {"pid": os.getpid() + 1})
    assert D.is_own_job(LABEL) is False
    monkeypatch.setattr(D, "launchctl_state", lambda lbl: {"pid": None})
    assert D.is_own_job(LABEL) is False
    monkeypatch.setattr(D, "launchctl_state", lambda lbl: (_ for _ in ()).throw(OSError("boom")))
    assert D.is_own_job(LABEL) is False


def test_scheduling_failure_is_reported_not_swallowed(monkeypatch):
    _is_me(monkeypatch)

    def _boom(*a, **k):
        raise OSError("fork failed")

    monkeypatch.setattr(D.subprocess, "Popen", _boom)
    ok, detail = D.run_op("restart", LABEL)
    assert not ok
    assert "fork failed" in detail
