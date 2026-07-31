"""Pin the periodic-job verdict, because the old probe got it wrong forever.

The regression these guard: `status_summary` counted `"state = running"` and so
scored `ai.hermes.watchdog` — a StartInterval=300 job with 262 runs and every
one exit 0 — as down. The card read 🟡 2/5 on a healthy estate, permanently,
with no action available to clear it.
"""

from __future__ import annotations

import plistlib
import subprocess

import pytest

from gateway.operator_shell import launchd_health as LH


# Real `launchctl print` output, trimmed to the lines the parser reads.
PERIODIC_IDLE = """gui/501/ai.hermes.watchdog = {
\tstate = not running
\truns = 262
\tlast exit code = 0
}
"""

PERIODIC_FAILING = """gui/501/ai.hermes.watchdog = {
\tstate = not running
\truns = 262
\tlast exit code = 78
}
"""

RESIDENT_UP = """gui/501/ai.hermes.coordinator = {
\tstate = running
\tpid = 56290
\truns = 4
}
"""

RESIDENT_DOWN = """gui/501/ai.hermes.coordinator = {
\tstate = not running
\truns = 4
\tlast exit code = 1
}
"""


def _install(tmp_path, monkeypatch, label, contract, stdout, rc=0):
    """Write a plist expressing `contract` and stub launchctl to return `stdout`."""
    (tmp_path / f"{label}.plist").write_bytes(plistlib.dumps(contract))
    monkeypatch.setattr(LH, "LAUNCH_AGENTS", tmp_path)
    monkeypatch.setattr(
        LH.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], rc, stdout, ""),
    )


def test_idle_periodic_job_is_healthy(tmp_path, monkeypatch):
    """`state = not running` on a StartInterval job is the healthy steady state."""
    _install(tmp_path, monkeypatch, "ai.hermes.watchdog",
             {"StartInterval": 300, "RunAtLoad": True}, PERIODIC_IDLE)

    h = LH.probe("ai.hermes.watchdog")

    assert h.kind == "periodic"
    assert h.state == "scheduled"
    assert h.ok is True          # the whole point: NOT counted as down
    assert h.glyph == "🟢"
    assert "262 runs" in h.detail


def test_periodic_job_faults_on_nonzero_exit(tmp_path, monkeypatch):
    """A periodic job is judged by its exit code, which is the only failure signal it has."""
    _install(tmp_path, monkeypatch, "ai.hermes.watchdog",
             {"StartInterval": 300}, PERIODIC_FAILING)

    h = LH.probe("ai.hermes.watchdog")

    assert h.state == "failing"
    assert h.ok is False
    assert "78" in h.detail


def test_resident_daemon_must_actually_be_running(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, "ai.hermes.coordinator",
             {"KeepAlive": True, "RunAtLoad": True}, RESIDENT_UP)
    assert LH.probe("ai.hermes.coordinator").ok is True

    _install(tmp_path, monkeypatch, "ai.hermes.coordinator",
             {"KeepAlive": True, "RunAtLoad": True}, RESIDENT_DOWN)
    h = LH.probe("ai.hermes.coordinator")
    assert h.state == "down"
    assert h.ok is False


def test_deliberately_disabled_agent_is_muted_not_faulted(tmp_path, monkeypatch):
    """`verify_estate.sh` scores `ai.hermes.ngrok Disabled=true` as a PASSING fence."""
    _install(tmp_path, monkeypatch, "ai.hermes.ngrok",
             {"Disabled": True, "RunAtLoad": True}, "", rc=113)

    h = LH.probe("ai.hermes.ngrok")

    assert h.state == "disabled"
    assert h.ok is True
    assert h.glyph == "⚪"


def test_missing_plist_is_a_fault_not_a_silent_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(LH, "LAUNCH_AGENTS", tmp_path)
    h = LH.probe("ai.hermes.nonexistent")
    assert h.state == "missing"
    assert h.ok is False


def test_disabled_agents_leave_the_denominator(tmp_path, monkeypatch):
    """Otherwise a deliberate fence makes `all clear` unreachable by construction."""
    healths = [
        LH.Health("a", "resident", "running", True, ""),
        LH.Health("b", "periodic", "scheduled", True, ""),
        LH.Health("c", "disabled", "disabled", True, ""),
    ]
    ok, total, faults = LH.summarize(healths)
    assert (ok, total) == (2, 2)
    assert faults == []


def test_summarize_returns_the_faults_so_the_card_can_name_them(tmp_path, monkeypatch):
    healths = [
        LH.Health("ai.hermes.gateway", "resident", "running", True, ""),
        LH.Health("ai.hermes.rsi", "periodic", "failing", False, "last exit 1"),
    ]
    ok, total, faults = LH.summarize(healths)
    assert (ok, total) == (1, 2)
    assert [f.short for f in faults] == ["rsi"]
