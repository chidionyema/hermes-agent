"""The `deployed` panel must keep telling the truth after people edit it.

This panel exists because a status someone WROTE DOWN went stale and asserted `NOT STARTED`
about work that had shipped. Its whole value is that every row is computed. These tests guard
the three ways that value has already been lost once each during its first hour of life:

1. **A probe that calls the function differently from the process under test.**
   `code_fingerprint()` argless hashes the package; the daemon passes a config path
   (`run_scheduled.py:1416`) so config.yaml is inside the hash it logs. Calling it argless
   produced `033b7d4b1855` against a logged `776a692b1a3e` and painted a healthy engine
   🔴 STALE CODE. Static test, so it holds without a daemon.

2. **A code root that is not the code the daemon loads.** Pointing all four local daemons at
   the hermes-agent repo made an edit to `estate.py` report `coordinator` — which runs
   `~/.hermes/scripts/coordinator.py` — as stale. Re-derived from launchctl here.

3. **A renderer that is not reachable.** `estate.py`'s registry is the door; a panel written
   and not registered is the "built and unreachable" defect this estate has hit repeatedly.

What these do NOT prove, stated so a green run is not over-read: they do not prove any
individual probe's verdict is correct on this machine at this moment. That is what the panel
itself is for.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gateway.operator_shell import deployed

# No `pytest.mark.timeout` here: pytest-timeout is not installed in this venv, so the mark is
# silently ignored and would read as a bound that does not exist. The real bound is the panel's
# own `_DEADLINE_S` plus each probe's subprocess timeout, which is the thing under test anyway.


def _launchctl_arguments(job: str) -> str:
    """The argv launchd will actually exec for `job`, as one string, or '' if unavailable."""
    if not shutil.which("launchctl"):
        return ""
    try:
        out = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{job}"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""
    block = re.search(r"arguments = \{(.*?)\n\t\}", out, re.S)
    return " ".join(block.group(1).split()) if block else ""


# ── 1. the probe must call the engine exactly as the engine calls itself ────────────────────
def test_engine_fingerprint_is_not_recomputed_argless():
    for label, _repo, _marker, cmd in deployed._ENGINES:
        src = " ".join(cmd)
        assert "code_fingerprint(" in src, f"{label}: probe no longer recomputes a fingerprint"
        assert "code_fingerprint()" not in src, (
            f"{label}: recomputing argless drops config.yaml from the hash, so the panel "
            f"compares a different quantity to the one the daemon logs and reports a healthy "
            f"engine as STALE CODE. Pass the same config path the daemon passes."
        )


def test_engine_probe_fits_inside_the_panel_deadline():
    """A probe allowed to run longer than the panel waits can only ever render as a timeout."""
    for label, _repo, _marker, _cmd in deployed._ENGINES:
        assert deployed._FP_TTL_S > deployed._DEADLINE_S, (
            "the fingerprint cache must outlive a render, or every render pays the cold cost"
        )


# ── 2. the declared code root must be the code the daemon really loads ──────────────────────
@pytest.mark.parametrize("label,job,code_root", deployed._LOCAL)
def test_declared_code_root_matches_launchctl(label: str, job: str, code_root: Path):
    argv = _launchctl_arguments(job)
    if not argv:
        pytest.skip(f"{job} not loaded on this machine — nothing to cross-check against")

    # Two shapes, both handled by searching TEXT rather than by resolving scraped paths.
    # (Resolving them was the first attempt and it raised `ValueError: embedded null byte` —
    # a regex over a shell script's bytes happily yields things that are not paths.)
    #   direct   — launchd execs the code itself:            argv contains the root
    #   wrapper  — launchd execs a .sh that hands off:       the script's text contains it
    haystack = argv
    for path in re.findall(r"/[\w./@+-]+\.sh\b", argv):
        try:
            haystack += "\n" + Path(path).read_text(errors="ignore")
        except OSError:
            pass
    haystack = haystack.replace("$HOME", str(Path.home()))

    assert str(code_root) in haystack, (
        f"{label}: declared code root {code_root} appears nowhere in what launchd runs.\n"
        f"argv: {argv}\n"
        f"A wrong root makes this panel report drift for a daemon that never loads that code — "
        f"the amber that teaches an operator to stop reading the panel."
    )


# ── 3. the panel must be reachable, and must render ─────────────────────────────────────────
def test_panel_is_registered_in_the_dispatcher():
    from gateway.operator_shell.estate import _PANELS

    assert "deployed" in _PANELS, "renderer written but not wired — it cannot be reached"
    module, func, _toast, arg = _PANELS["deployed"]
    assert (module, func) == ("deployed", "render_deployed")
    assert arg == "none", "render_deployed takes no argument; any other mode TypeErrors on tap"


def test_render_returns_text_and_buttons_and_never_raises():
    text, buttons = deployed.render_deployed()
    assert isinstance(text, str) and text.strip()
    assert isinstance(buttons, list) and buttons
    assert all(isinstance(row, list) for row in buttons)
    # Telegram rejects a message over 4096 characters — a panel that grows past it fails closed
    # in production and nowhere else.
    assert len(text) < 4000, f"panel is {len(text)} chars; Telegram's limit is 4096"


def test_every_registered_component_gets_a_row():
    """The registry is the point: a component added to it must appear, or the panel silently
    under-reports the estate — the exact failure it was built to end."""
    text, _ = deployed.render_deployed()
    expected = (
        [label for label, _, _ in deployed._LOCAL]
        + [label for label, _, _, _ in deployed._ENGINES]
        + [label for label, _, _, _ in deployed._REMOTE]
        + [label for label, _, _ in deployed._REPOS]
    )
    missing = [label for label in expected if label not in text]
    assert not missing, f"registered but absent from the rendered panel: {missing}"


def test_a_probe_that_raises_is_never_green():
    """Fail-amber, not fail-green. A probe that blows up must not read as health."""
    original = deployed._probe_repo

    def boom(*_args, **_kwargs):
        raise RuntimeError("probe exploded")

    deployed._probe_repo = boom
    try:
        text, _ = deployed.render_deployed()
    finally:
        deployed._probe_repo = original

    assert "probe error" in text, "a raising probe must be reported, not swallowed"
    for line in text.splitlines():
        if "probe error" in line:
            assert line.startswith(deployed._AMBER), f"raising probe rendered non-amber: {line}"
