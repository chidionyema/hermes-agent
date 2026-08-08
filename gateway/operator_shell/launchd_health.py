"""Judge a LaunchAgent by what its plist ASKS launchd to do, not by `state = running`.

The bug this exists to kill, measured on the live estate 2026-07-31:

    status_summary.py:55   if "state = running" in (r.stdout or ""): running += 1
    -> card renders        🟡 Daemons: 2/5 running

Three of those five "down" daemons were perfectly healthy:

    $ launchctl print gui/501/ai.hermes.watchdog
        state = not running
        runs = 262
        last exit code = 0
    $ grep -A1 StartInterval ~/Library/LaunchAgents/ai.hermes.watchdog.plist
        <key>StartInterval</key>
        <integer>300</integer>

`ai.hermes.watchdog` is a *periodic* job: launchd runs it every 300s and it
exits. 262 runs, every one exit 0. Between runs — which is 99.9% of wall clock —
its state is `not running`, and that is the healthy steady state. Same for
`ai.hermes.progress` (StartInterval 3600) and `ai.hermes.rsi`
(StartCalendarInterval). Only `gateway` and `coordinator` are resident
(`KeepAlive`/`RunAtLoad`), so the old probe could never score higher than 2/5.

So the daemon line read 🟡 *permanently*, on a healthy estate, and no action
could ever clear it. That is worse than showing nothing: a warning that is
always on is a warning the operator learns to scroll past, and the one time
`coordinator` actually dies it looks exactly like Tuesday.

The rule here: read the plist to learn the CONTRACT, then check the contract.

    resident  (KeepAlive, or RunAtLoad with no interval)  -> must be running
    periodic  (StartInterval / StartCalendarInterval)     -> must be loaded,
                                                             last exit 0
    disabled  (Disabled=true)                             -> muted, not a fault

That last line is deliberate: `verify_estate.sh` scores `ai.hermes.ngrok
Disabled=true` as a PASSING fence. A daemon that is off on purpose must not
burn the same colour as a daemon that fell over.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"

# The five daemons the estate cockpit speaks for. Order is display order.
ESTATE_DAEMONS = [
    "ai.hermes.gateway",
    "ai.hermes.coordinator",
    "ai.hermes.watchdog",
    "ai.hermes.progress",
    "ai.hermes.rsi",
]

_RE_STATE = re.compile(r"^\s*state\s*=\s*(.+)$", re.M)
_RE_PID = re.compile(r"^\s*pid\s*=\s*(\d+)\s*$", re.M)
_RE_EXIT = re.compile(r"^\s*last exit code\s*=\s*(-?\d+)\s*$", re.M)
_RE_RUNS = re.compile(r"^\s*runs\s*=\s*(\d+)\s*$", re.M)


@dataclass
class Health:
    """One daemon's verdict. `ok` is the only field a summary count should read."""

    label: str
    kind: str      # resident | periodic | disabled | unknown
    state: str     # running | scheduled | down | failing | disabled | unloaded | missing
    ok: bool
    detail: str    # one phone-width line, already human-readable
    pid: Optional[int] = None
    last_exit: Optional[int] = None
    runs: int = 0

    @property
    def short(self) -> str:
        """`ai.hermes.watchdog` -> `watchdog` — the only part that fits a phone row."""
        return self.label.rsplit(".", 1)[-1]

    @property
    def glyph(self) -> str:
        if self.state == "disabled":
            return "⚪"
        return "🟢" if self.ok else "🔴"


def _plist_contract(label: str) -> tuple[str, bool]:
    """(kind, disabled) read from the plist on disk. ('unknown', False) if unreadable."""
    path = LAUNCH_AGENTS / f"{label}.plist"
    if not path.is_file():
        return "missing", False
    try:
        with path.open("rb") as fh:
            data = plistlib.load(fh)
    except Exception:
        return "unknown", False
    if data.get("Disabled") is True:
        return "disabled", True
    # StartInterval / StartCalendarInterval mean "run me, then let me exit".
    if "StartInterval" in data or "StartCalendarInterval" in data:
        return "periodic", False
    # KeepAlive (bool or dict) or a bare RunAtLoad means "stay up".
    if data.get("KeepAlive") or data.get("RunAtLoad"):
        return "resident", False
    return "unknown", False


def probe(label: str, timeout: float = 3.0) -> Health:
    """Classify one LaunchAgent. Never raises — a probe that throws is a probe that lies."""
    kind, disabled = _plist_contract(label)
    if kind == "missing":
        return Health(label, "unknown", "missing", False, "no plist installed")

    try:
        r = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],  # windows-footgun: ok — launchctl is macOS-only
            capture_output=True, text=True, timeout=timeout,
        )
        out = r.stdout or ""
        loaded = r.returncode == 0
    except Exception as exc:
        return Health(label, kind, "unloaded", False, f"probe failed: {exc}")

    m = _RE_STATE.search(out)
    running = bool(m) and m.group(1).strip() == "running"
    pid = int(_RE_PID.search(out).group(1)) if _RE_PID.search(out) else None
    last_exit = int(_RE_EXIT.search(out).group(1)) if _RE_EXIT.search(out) else None
    runs = int(_RE_RUNS.search(out).group(1)) if _RE_RUNS.search(out) else 0

    if disabled:
        # Off on purpose. Report it, do not fault it — see the ngrok fence in the module docstring.
        return Health(label, "disabled", "disabled", True, "disabled in plist (deliberate)",
                      pid, last_exit, runs)

    if not loaded:
        return Health(label, kind, "unloaded", False, "plist installed but not loaded into launchd",
                      pid, last_exit, runs)

    if kind == "periodic":
        # The contract is "run on schedule and exit 0". `not running` is the healthy
        # steady state; a nonzero last exit is the actual failure signal.
        if last_exit not in (0, None):
            return Health(label, kind, "failing", False,
                          f"periodic · last exit {last_exit} after {runs} runs",
                          pid, last_exit, runs)
        return Health(label, kind, "scheduled", True,
                      f"periodic · {runs} runs, last exit 0",
                      pid, last_exit, runs)

    # resident / unknown: the contract is "be up".
    if running:
        return Health(label, kind, "running", True,
                      f"resident · pid {pid}" if pid else "resident · running",
                      pid, last_exit, runs)
    return Health(label, kind, "down", False,
                  f"resident but not running (last exit {last_exit})"
                  if last_exit is not None else "resident but not running",
                  pid, last_exit, runs)


def probe_estate(labels: Optional[List[str]] = None) -> List[Health]:
    """Every estate daemon, display order preserved."""
    return [probe(l) for l in (labels or ESTATE_DAEMONS)]


def summarize(healths: List[Health]) -> tuple[int, int, List[Health]]:
    """(ok_count, total, faults). `total` excludes deliberately-disabled agents.

    Excluding disabled from the denominator is what makes `5/5` reachable. If a
    fence like `ai.hermes.ngrok` counted, the estate could never read all-clear
    and the count would be un-actionable by construction.
    """
    counted = [h for h in healths if h.state != "disabled"]
    ok = sum(1 for h in counted if h.ok)
    return ok, len(counted), [h for h in healths if not h.ok]


if __name__ == "__main__":  # probe from the shell: python3 -m gateway.operator_shell.launchd_health
    hs = probe_estate()
    ok, total, faults = summarize(hs)
    for h in hs:
        print(f"{h.glyph} {h.short:14} {h.state:10} {h.detail}")
    print(f"\n{ok}/{total} healthy · {len(faults)} fault(s)")
