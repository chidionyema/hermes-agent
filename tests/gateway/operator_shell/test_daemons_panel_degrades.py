"""The recovery screen must never be the thing that is down.

`daemons.py` enforces an 8-button cap. Until 2026-08-09 it enforced it with a bare
`assert` inside the render path, which fails two ways at once:

- Tripping it returns no panel at all. The daemons panel is the only phone-reachable door
  to bounce a crashed KeepAlive job (`estate:daemon_restart:gateway`), so the failure mode
  was "recovery is unreachable because recovery has one button too many".
- `python -O` strips asserts. The guard was a crash in dev and a no-op in prod — the two
  environments disagreeing about the same code is worse than either behaviour alone.

Nothing pinned it: a grep across `tests/` for the cap found only unrelated `<= 8`
assertions. So this file pins the contract the assert was reaching for (never more than 8
buttons) and the behaviour it got wrong (degrade, keep the spine, still render).
"""

from __future__ import annotations

import inspect

from gateway.operator_shell import daemons as D


def test_the_daemons_panel_stays_within_the_eight_button_cap():
    _text, buttons = D.render_daemons()
    total = sum(len(r) for r in buttons)
    assert total <= 8, f"daemons panel rendered {total} buttons"


def test_the_panel_renders_even_when_the_cap_is_exceeded(monkeypatch):
    """Force the overflow the assert used to die on, by fattening the spine the panel
    appends last. A real overflow would come from someone adding an action row; either
    way the operator must still get a screen."""
    real_nav = D.nav
    monkeypatch.setattr(
        D, "nav", lambda *a, **k: list(real_nav(*a, **k)) + [("🧨 x", "estate:home")] * 6
    )

    text, buttons = D.render_daemons()

    total = sum(len(r) for r in buttons)
    assert total <= 8, f"degraded panel still over cap: {total}"
    assert text.strip(), "panel rendered no text"
    # The spine survives — a screen with no way back is not a recovery path.
    assert buttons[-1], "navigation row was dropped"
    assert "hidden to fit" in text


def test_both_recovery_doors_survive_the_trim():
    """The panel is over cap today and will stay there: 3 action buttons + a 6-button spine
    = 9. What degradation removes therefore matters more than that it happens. `🗂 Projects`
    joining the spine on 2026-08-06 ate the third action slot, and the first two drafts of
    the trim each dropped a recovery verb — once the gateway bounce, once both coord verbs.
    These two callbacks are the whole point of the screen and must outlive any trim."""
    _text, buttons = D.render_daemons()
    callbacks = {cb for row in buttons for _label, cb in row}

    assert "estate:daemon_restart:gateway" in callbacks, (
        "the only phone-reachable door to a stopped gateway was trimmed away"
    )
    assert "estate:daemon_restart:coordinator" in callbacks, (
        "the coordinator recovery verb was trimmed away"
    )


def test_the_cap_is_not_enforced_by_an_assert():
    """`python -O` strips asserts, so a guard written as one is absent in production."""
    src = inspect.getsource(D.render_daemons)
    assert "assert " not in src, (
        "render_daemons enforces a limit with `assert`, which -O removes and which "
        "takes the whole panel down when it trips"
    )
