"""One destination, one name — measured, ratcheted, and being paid down.

An operator navigates by recognising a word. `estate:otto_health` is currently reachable
as "🧠 Otto health", "🧠 Health", "📊 Details", "📊 Dashboard" and "📊 Self-audit"; a
person who learns "Self-audit" on one screen has to re-learn it on the next four. That is
the difference between a cockpit that feels designed and one that feels assembled.

Measured 2026-08-09 by the scanner below: **39 of 153 estate callbacks carry more than one
label**, after exempting the labels whose whole job is contextual (see `_CONTEXTUAL`).

This file does not demand the cleanup happen at once — renaming 39 destinations across 30
modules in one change is how a navigation regression ships. It ratchets: the count may
fall, never rise. Adding a fortieth conflicting label fails; fixing one lets you lower
`BASELINE`. The named cases below are the ones already unified, pinned so they cannot drift
back.

Why static analysis rather than rendering every panel: a panel needs a live estate to
render, and this question is about the source text, not the runtime. `test_every_button_
dispatches.py` already covers the runtime half — that every callback reaches a handler.
Together: every button goes somewhere (that file) and every somewhere has one name (this
one). Neither side is a hand-maintained table.
"""

from __future__ import annotations

import ast
import collections
import pathlib

import gateway.operator_shell as _pkg


PANEL_DIR = pathlib.Path(_pkg.__file__).parent

# Labels whose meaning is "the panel you came from" / "this panel again". The same callback
# legitimately reads "✗ Cancel" on a confirm screen and "⚙️ Params" on a nav row — that is
# context, not drift, and forcing one word on both would make the confirm screen worse.
_CONTEXTUAL = (
    "✗ ",
    "🔄 Refresh",
    "🔄 Retry",
    "🔄 Re-check",
    "🏠 ",
    "⚙️ Back",
    "💹 Back",
    "🧠 Back",
)

BASELINE = 39


def _labels() -> dict[str, set[str]]:
    """callback -> every label any panel renders it under."""
    out: dict[str, set[str]] = collections.defaultdict(set)
    for path in sorted(PANEL_DIR.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken module fails louder elsewhere
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Tuple) and len(node.elts) == 2):
                continue
            label, cb = node.elts
            if not (isinstance(label, ast.Constant) and isinstance(cb, ast.Constant)):
                continue
            if not (isinstance(label.value, str) and isinstance(cb.value, str)):
                continue
            if not cb.value.startswith("estate:"):
                continue
            if any(label.value.startswith(c) for c in _CONTEXTUAL):
                continue
            out[cb.value].add(label.value)
    return out


def _conflicts() -> dict[str, set[str]]:
    return {k: v for k, v in _labels().items() if len(v) > 1}


def test_the_scanner_sees_the_cockpit_at_all():
    """A scanner that silently matches nothing would let every other test here pass."""
    labels = _labels()
    assert len(labels) >= 120, f"only {len(labels)} callbacks found — scanner is broken"
    assert "estate:tune" in labels


def test_the_number_of_double_named_destinations_never_grows():
    conflicts = _conflicts()
    assert len(conflicts) <= BASELINE, (
        f"{len(conflicts)} destinations now carry more than one label (baseline "
        f"{BASELINE}). New offenders: "
        + ", ".join(f"{k} = {sorted(v)}" for k, v in sorted(conflicts.items()))
    )
    assert len(conflicts) >= BASELINE - 5, (
        f"only {len(conflicts)} conflicts left — lower BASELINE to {len(conflicts)} so the "
        "ratchet keeps biting"
    )


def test_restarting_the_coordinator_has_exactly_one_name():
    """mission.py:501 said '🔄 Restart Coord' beside a '♻️' button for a different daemon,
    while command_palette.py:37 said '♻️ Restart coord' for the same callback."""
    assert _labels()["estate:restart"] == {"♻️ Restart coord"}
