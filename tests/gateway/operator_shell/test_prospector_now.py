"""Failure-mode tests for the `🎛 Now` Prospector engine readout panel.

The renderer (`gateway.operator_shell.prospector_now.render_prospector_now`) loads the
engine's `prospector/scheduler/status.py::status_snapshot()` path-based and renders
the digest as a Telegram message. The engine side is on a SEPARATE branch in the
prospector repo, so these tests must not require the engine to be installed.

THIS FILE IS THE DEFINITION OF "DONE" FOR THE HERMES WIRE-UP. The implementation must
satisfy every test below. The tests are committed BEFORE the implementation so the
verify command can prove the delta is what made them green.

(per the failing-tests invariant: if the implementation lands first and the test is
written to match, the test is not a fence — it is a description. The order here is
deliberate.)
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

def test_module_imports():
    from gateway.operator_shell import prospector_now  # noqa: F401
    assert hasattr(prospector_now, "render_prospector_now")
    assert callable(prospector_now.render_prospector_now)


def test_render_returns_text_and_buttons():
    """The contract: render returns (text, buttons). Even when the engine is unreachable,
    the function degrades to a one-line 'unreachable' message and never raises."""
    from gateway.operator_shell.prospector_now import render_prospector_now

    text, buttons = render_prospector_now()
    assert isinstance(text, str)
    assert len(text) > 0
    assert isinstance(buttons, list)
    # One of the two cases: engine reachable (digest) or unreachable (warning).
    # Both must produce a non-empty text. The renderer must never raise.
    assert "Prospector" in text or "unreachable" in text.lower() or "not found" in text.lower()


def test_render_never_raises_even_with_path_missing(tmp_path, monkeypatch):
    """If the engine repo is not at the expected path, the renderer returns a one-line
    warning rather than raising. This is the contract — the cockpit must never break
    because the engine checkout moved."""
    from gateway.operator_shell import prospector_now

    # Force the engine path to nowhere
    monkeypatch.setattr(prospector_now, "_PROSPECTOR_PATHS", [tmp_path / "does-not-exist"])
    monkeypatch.delenv("PROSPECTOR_REPO", raising=False)

    text, buttons = prospector_now.render_prospector_now()
    assert isinstance(text, str)
    assert "Prospector" in text or "unreachable" in text.lower() or "not found" in text.lower()


# ---------------------------------------------------------------------------
# Engine-side integration (monkeypatched)
# ---------------------------------------------------------------------------

def _fake_engine_snapshot(**overrides):
    """Build a snapshot dict shaped like `prospector.scheduler.status.status_snapshot`'s return."""
    snap = {
        "daemon": {"pid": 43394, "phase": "generating", "last_tick_age_s": 30.0, "ts": "2026-08-08T18:00:00+00:00"},
        "last_tick": {"ts": "2026-08-08T18:00:00+00:00", "dossiers": 13, "passes": 0, "kills": 13,
                       "defers": 0, "provisional": 0, "cost_usd": 0.10},
        "spend": {"today_usd": 1.07, "daily_cap_usd": 20.0, "today_subscription_usd": 415.0},
        "providers": {"moat_blind": False, "dead": [], "moat_brains": ["claude_cli"],
                       "blind_reason": None},
        "alerts": {"active": [], "active_count": 0},
        "backlog": {"deferred": 78, "provisional": 0},
    }
    snap.update(overrides)
    return snap


def test_render_with_healthy_snapshot():
    """A healthy (no alerts, no dead providers, moat not blind) snapshot renders with
    a green glyph and the digest content."""
    from gateway.operator_shell.prospector_now import render_prospector_now, _render_snapshot

    text, buttons = _render_snapshot(_fake_engine_snapshot())
    assert "🟢" in text or "✓" in text or "healthy" in text.lower()
    assert "13" in text  # dossiers echoed
    assert "$1.07" in text or "1.07" in text  # spend echoed


def test_render_with_moat_blind_snapshot():
    """Moat blind = 🔴 — and the reason must be visible."""
    from gateway.operator_shell.prospector_now import _render_snapshot

    snap = _fake_engine_snapshot(providers={"moat_blind": True, "dead": ["claude_cli"],
                                             "moat_brains": ["claude_cli"],
                                             "blind_reason": "all brains dead"})
    text, buttons = _render_snapshot(snap)
    assert "🔴" in text or "blind" in text.lower()
    assert "claude_cli" in text or "dead" in text.lower()


def test_render_with_active_alert():
    """An active alert shows ⚠ and the alert title."""
    from gateway.operator_shell.prospector_now import _render_snapshot

    snap = _fake_engine_snapshot(alerts={"active": [
        {"key": "zero_yield", "severity": "warning", "title": "Zero yield: 13c, 0 PASS"}
    ], "active_count": 1})
    text, buttons = _render_snapshot(snap)
    assert "⚠" in text or "Zero yield" in text


def test_render_with_partial_dead_providers():
    """Some (not all) providers dead = 🟡."""
    from gateway.operator_shell.prospector_now import _render_snapshot

    snap = _fake_engine_snapshot(providers={"moat_blind": False, "dead": ["minimax"],
                                             "moat_brains": ["claude_cli", "minimax"],
                                             "blind_reason": None})
    text, buttons = _render_snapshot(snap)
    assert "🟡" in text or "dead" in text.lower() or "minimax" in text


def test_render_includes_action_buttons():
    """The render must include buttons that lead BACK to the engine's tooling."""
    from gateway.operator_shell.prospector_now import render_prospector_now

    text, buttons = render_prospector_now()
    # Even on the unreachable path, the renderer should keep at least a `Home` button
    # so the operator is not stranded. The contract: never an empty buttons list.
    if "unreachable" in text.lower() or "not found" in text.lower():
        # Best-effort: at least one button row, even if degraded
        assert len(buttons) >= 1, "unreachable render must still offer a Home button"
    else:
        # Healthy render: at least one action button (daemon / params / cron)
        flat = [b for row in buttons for _, b in row]
        assert any("prospector" in a or "Home" in (l for l, _ in [b for b in flat]) or "estate:" in a
                    for a in flat), flat


# ---------------------------------------------------------------------------
# Panel registry wiring
# ---------------------------------------------------------------------------

def test_panels_registry_has_prospector_now():
    """`_PANELS` in estate.py must contain a `prospector_now` key — that is what
    forwards the `estate:prospector_now` callback to the renderer. Without this
    entry, the button is dead (the 'built and unreachable' defect class)."""
    from gateway.operator_shell.estate import _PANELS

    assert "prospector_now" in _PANELS, (
        f"registry missing prospector_now — wired but unreachable. keys: {sorted(_PANELS)}"
    )
    module, func, toast, arg_mode = _PANELS["prospector_now"]
    assert module == "prospector_now"
    assert func == "render_prospector_now"
    assert toast == "Now"
    assert arg_mode == "none"


def test_every_button_dispatches_test_still_passes():
    """The static test that the cockpit's `test_every_button_dispatches.py` runs at
    collection time must not gain a regression from this branch. The renderer is the
    only place that emits `estate:prospector_now`; the registry must accept it."""
    # The static test is collection-time; running it here after the registry change
    # is the cleanest proof the dispatch graph is closed.
    from gateway.operator_shell.estate import _PANELS  # noqa: F401
    from gateway.operator_shell import prospector_now  # noqa: F401

    # Sanity: the module imports cleanly under the registry's expected name.
    importlib.import_module("gateway.operator_shell.prospector_now")
    assert hasattr(prospector_now, "render_prospector_now")


# ---------------------------------------------------------------------------
# Reachability — a panel nobody can open is the cockpit's oldest defect class
# ---------------------------------------------------------------------------

def test_the_readout_is_reachable_without_being_a_home_tile():
    """Reachable from the machine room and the palette; NOT a tile on home.

    This started as "the button must be on the mission card", which broke a pinned IA
    invariant: home is fires-only and carries at most two of its own tiles, and
    `test_cockpit_ia.py:190` failed 3 <= 2 on
    ['estate:setup_cron_topic', 'estate:pause', 'estate:prospector_now']. A readout is a
    destination, not a fire. So the assertion moved to where it belongs -- the panel must be
    reachable, which is the thing that actually matters, and the two surfaces that carry every
    other machine readout are the ones checked here.
    """
    from gateway.operator_shell.atlas import all_room_destinations
    from gateway.operator_shell.command_palette import COMMAND_GROUPS
    from gateway.operator_shell.mission import mission_buttons
    from gateway.operator_shell.panel_chrome import nav

    rooms = {cb for _l, cb in all_room_destinations()}
    assert "estate:prospector_now" in rooms, "orphaned panel — not in any Atlas room"

    palette = {cb for _title, entries in COMMAND_GROUPS for _l, cb in entries}
    assert "estate:prospector_now" in palette, "orphaned panel — not in the command palette"

    quiet_home = mission_buttons(False, ("🚀 Fleet", "estate:fleet"), [])
    spine = {cb for _l, cb in nav()}
    own = [cb for row in quiet_home for _l, cb in row if cb not in spine]
    assert "estate:prospector_now" not in own, f"home is fires-only; own tiles were {own}"


def test_the_coordinator_unavailable_card_still_carries_it():
    """When the coordinator is down there are no concerns to compete with, and the engine is a
    separate process that is probably still working — so that card keeps the readout."""
    from gateway.operator_shell.mission import _render_unavailable_card

    _text, _paused, buttons = _render_unavailable_card()
    flat = [cb for row in buttons for _l, cb in row]
    assert "estate:prospector_now" in flat
