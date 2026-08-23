"""Tests for gateway.shutdown_forensics — fast snapshot + async diag spawn."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from gateway import shutdown_forensics as sf


# ---------------------------------------------------------------------------
# _signal_name
# ---------------------------------------------------------------------------

class TestSignalName:

    def test_unknown_int_returns_signal_num_token(self):
        # Pick an integer extremely unlikely to ever be a real signal alias
        assert sf._signal_name(9999) == "signal#9999"


# ---------------------------------------------------------------------------
# snapshot_shutdown_context
# ---------------------------------------------------------------------------

class TestSnapshotShutdownContext:

    def test_handles_none_signal(self):
        ctx = sf.snapshot_shutdown_context(None)
        assert ctx["signal"] == "UNKNOWN"
        assert ctx["signal_num"] is None

    def test_includes_timestamps(self):
        before = time.time()
        ctx = sf.snapshot_shutdown_context(signal.SIGTERM)
        after = time.time()
        assert before <= ctx["ts"] <= after
        assert isinstance(ctx["ts_monotonic"], float)


    def test_under_systemd_false_without_invocation_id_and_normal_ppid(
        self, monkeypatch
    ):
        monkeypatch.delenv("INVOCATION_ID", raising=False)
        # We can't actually change ppid; skip if we happen to be reaped
        # by init (e.g. running under tini).
        if os.getppid() == 1:
            pytest.skip("test process is reaped by init")
        ctx = sf.snapshot_shutdown_context(signal.SIGTERM)
        assert ctx["under_systemd"] is False


    def test_detects_takeover_marker_for_self(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        marker = tmp_path / ".gateway-takeover.json"
        marker.write_text(
            f'{{"target_pid": {os.getpid()}, "replacer_pid": 99999}}',
            encoding="utf-8",
        )
        ctx = sf.snapshot_shutdown_context(signal.SIGTERM)
        assert "takeover_marker" in ctx
        assert ctx["takeover_marker_for_self"] is True


# ---------------------------------------------------------------------------
# format_context_for_log / context_as_json
# ---------------------------------------------------------------------------

class TestFormatters:


    def test_context_as_json_handles_unserialisable_values(self):
        ctx = {"signal": "SIGTERM", "weird": object()}
        payload = sf.context_as_json(ctx)
        # default=str means objects get repr'd, JSON stays valid
        decoded = json.loads(payload)
        assert decoded["signal"] == "SIGTERM"
        assert "weird" in decoded


# ---------------------------------------------------------------------------
# spawn_async_diagnostic
# ---------------------------------------------------------------------------

class TestSpawnAsyncDiagnostic:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only diagnostic")
    def test_spawns_subprocess_and_writes_output(self, tmp_path):
        log_path = tmp_path / "diag.log"
        pid = sf.spawn_async_diagnostic(log_path, "SIGTERM", timeout_seconds=3.0)
        assert pid is not None and pid > 0

        # Wait briefly for the subprocess to write — bounded by its own timeout.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if log_path.exists() and log_path.stat().st_size > 0:
                # Wait a touch longer for the script to finish writing
                time.sleep(0.2)
                break
            time.sleep(0.1)

        # Reap the subprocess so it doesn't show up as a zombie.
        try:
            os.waitpid(pid, 0)
        except (ChildProcessError, OSError):
            pass

        assert log_path.exists()
        contents = log_path.read_text(encoding="utf-8", errors="replace")
        assert "shutdown diagnostic" in contents
        assert "SIGTERM" in contents


# ---------------------------------------------------------------------------
# _parse_systemd_duration_to_us
# ---------------------------------------------------------------------------

class TestParseSystemdDuration:
    def test_seconds(self):
        assert sf._parse_systemd_duration_to_us("90s") == 90 * 1_000_000

    def test_minutes(self):
        assert sf._parse_systemd_duration_to_us("3min") == 180 * 1_000_000


# ---------------------------------------------------------------------------
# check_systemd_timing_alignment
# ---------------------------------------------------------------------------

class TestCheckSystemdTimingAlignment:

    def test_returns_none_when_unit_undeterminable(self, monkeypatch):
        monkeypatch.setenv("INVOCATION_ID", "abc")
        # /proc/self/cgroup likely doesn't end in .service for the test runner
        result = sf.check_systemd_timing_alignment(180.0)
        # Either None (we couldn't find a unit) or a dict with mismatch info
        # for whatever unit pytest IS in.  Both are valid; we just ensure
        # the function doesn't raise.
        assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# incident 2026-08-23: an empty report read as a clean one
# ---------------------------------------------------------------------------

class TestIncident20260823EmptyDiagnostic:
    """The diagnostic ran Linux-only commands and wrote nothing on macOS.

    Four SIGTERM events on 2026-08-23 (20:53:32, 21:05:47, 21:19:25, 22:14:43)
    each produced a report with every heading present and every section empty,
    because ``ps auxf``, ``pstree``, ``/proc/loadavg`` and ``dmesg`` do not
    exist on a Mac and each was written ``2>/dev/null || true``.

    The test that already covered this function asserted only that the string
    "shutdown diagnostic" appeared in the file, which is the header. It passed
    for all four empty reports. The rule is that a section must have a body.
    """

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only diagnostic")
    def test_every_section_has_a_body_on_this_platform(self, tmp_path):
        import re

        log_path = tmp_path / "diag.log"
        pid = sf.spawn_async_diagnostic(log_path, "SIGTERM", timeout_seconds=5.0)
        assert pid is not None

        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            if log_path.exists() and "=== end ===" in log_path.read_text(
                encoding="utf-8", errors="replace"
            ):
                break
            time.sleep(0.1)
        try:
            os.waitpid(pid, 0)
        except (ChildProcessError, OSError):
            pass

        text = log_path.read_text(encoding="utf-8", errors="replace")
        assert "=== end ===" in text, "diagnostic never finished"

        parts = re.split(r"^--- (.+?) ---$", text, flags=re.M)
        sections = {
            parts[i]: parts[i + 1].replace("=== end ===", "").strip()
            for i in range(1, len(parts), 2)
        }
        assert sections, "no sections at all"
        empty = [name for name, body in sections.items() if not body.strip()]
        assert not empty, f"sections present but empty on {sys.platform}: {empty}"

    def test_script_matches_the_platform_it_will_run_on(self):
        script = sf._diag_script("SIGTERM", 1234)
        if sys.platform == "darwin":
            assert "pstree" not in script
            assert "/proc/loadavg" not in script
            assert "vm.loadavg" in script
        else:
            assert "/proc/loadavg" in script

    def test_missing_timeout_binary_does_not_kill_the_diagnostic(self, monkeypatch):
        monkeypatch.setattr(sf.shutil, "which", lambda _name: None)
        assert sf._timeout_argv(5.0) == []
