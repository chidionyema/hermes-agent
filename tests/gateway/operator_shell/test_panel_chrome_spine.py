"""Regression: every declared spine button must actually be on the spine.

`nav()`'s own docstring has listed Home / Projects / Actions / SDLC / Tune / Browse
since 2026-07-31, and 15+ panels link to `estate:tune` expecting it to be reachable
from the spine on every screen. 2026-08-02 (39402e463f, "unified cockpit") added
`_SDLC` to the `row` list literal and replaced `_TUNE` instead of keeping both — the
constant, the docstring, and every caller still said Tune belonged; only the one
list that actually renders it disagreed. It shipped with "23 end-to-end tests prove
every button works" and none of them asserted this, so `/panel` ran with no way to
reach Tune for 7 days (2026-08-02 -> 2026-08-09) before a founder report caught it.

This test reads the SAME module-level constants `nav()` builds `row` from, so a
future edit that defines a new spine button but forgets to add it to the list fails
here instead of shipping silently again.
"""
from __future__ import annotations

from gateway.operator_shell import panel_chrome


def test_every_declared_spine_button_is_on_the_spine():
    row = panel_chrome.nav()
    declared = [
        panel_chrome._NOW,
        panel_chrome._PROJECTS,
        panel_chrome._RUN,
        panel_chrome._SDLC,
        panel_chrome._TUNE,
        panel_chrome._MAP,
    ]
    missing = [label for label, cb in declared if (label, cb) not in row]
    assert not missing, (
        f"spine constant(s) defined but missing from nav()'s row: {missing} "
        f"— got {row}"
    )


def test_tune_reachable_from_every_panel():
    """`estate:tune` must be one tap away from the bare spine (no self_action)."""
    row = panel_chrome.nav()
    assert ("⚙️ Tune", "estate:tune") in row
