"""No single screen shows the same word pointing at two different places.

`test_destination_vocabulary.py` reads the SOURCE and asks "does this destination have one
name?". It cannot see a label that does not exist until render time, and that blind spot hid a
real one: `cockpit.render_run` put **two buttons both labelled "♻️ Restart"** on one screen —
one restarting the signal engine, one restarting the Prospector scheduler. Each sat under its
own `Group` heading, so it reads as unambiguous in the source. It is not: group headings live
in the message TEXT, while a Telegram inline keyboard is one flat grid, so on a phone those
were two identical adjacent buttons that restart different daemons. Found 2026-08-09 by
rendering every panel and reading the keyboards — never by grepping.

Three source shapes the static scanner is structurally blind to, all of them found here:

  * labels built at runtime — `f"{dot} {name}"`, a status glyph swapped in per health
  * 3-tuples — `cockpit._TUNE_GROUPS` is `(label, action, description)`, and the scanner only
    walks 2-tuples, so "💵 Spend" vs "💵 Spend cap" was invisible to it
  * rows assembled conditionally, where the collision only exists in one branch's output

So the two files are not redundant: one asks whether a destination has one name, this one asks
whether a name has one destination, and only this one sees what actually reaches the phone.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import gateway.operator_shell as pkg


# Labels whose entire job is "this panel again" / "the panel you came from". Two of these on one
# screen would be a real defect, but they are per-row refresh affordances and never collide.
_CONTEXTUAL = ("🔄", "✗ ", "🏠 ")

# Rendering the whole cockpit is the only way to ask this question, and a harness that silently
# renders nothing answers "no defects" forever. This is the count on 2026-08-09; it is a FLOOR,
# so adding panels never fails and gutting the sweep does.
MIN_PANELS_RENDERED = 40


_CACHE: list[tuple[str, list]] | None = None


def _render_everything() -> list[tuple[str, list]]:
    """(entry point name, buttons) for every panel that renders without arguments.

    Cached: a full sweep shells out to `gh`, `launchctl` and sqlite for ~40s, and both tests
    below want the same snapshot. Rendering twice doubled the suite's runtime for no new
    information.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    out = []
    for _finder, modname, _ispkg in pkgutil.iter_modules(pkg.__path__):
        try:
            mod = importlib.import_module(f"gateway.operator_shell.{modname}")
        except Exception:  # pragma: no cover - an unimportable module fails louder elsewhere
            continue
        for fname, fn in vars(mod).items():
            if not (fname.startswith("render") and inspect.isfunction(fn)):
                continue
            if fn.__module__ != mod.__name__:
                continue
            params = inspect.signature(fn).parameters.values()
            if any(
                p.default is inspect.Parameter.empty
                and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                for p in params
            ):
                continue
            try:
                result = fn()
            except Exception:
                # A panel that cannot render on this box (no live estate, no gh, no launchd) is
                # not this test's subject. `test_mdv2_panel_rendering` owns "does it render".
                continue
            if not (isinstance(result, tuple) and len(result) == 2):
                continue
            _text, buttons = result
            if not isinstance(buttons, list):
                continue
            out.append((f"{modname}.{fname}", buttons))
    _CACHE = out
    return out


def test_the_sweep_actually_renders_the_cockpit():
    """The guard the first version of this sweep did not have.

    Written without it, the harness called 17 panels, every call raised, and it reported "0
    conflicts" — a green light produced entirely by rendering nothing.
    """
    rendered = _render_everything()
    assert len(rendered) >= MIN_PANELS_RENDERED, (
        f"only {len(rendered)} panels rendered (floor {MIN_PANELS_RENDERED}) — this sweep is "
        "vacuous and its green result means nothing. Fix the harness, do not lower the floor."
    )


def test_no_screen_shows_the_same_label_twice_pointing_at_two_places():
    collisions = []
    for name, buttons in _render_everything():
        seen: dict[str, str] = {}
        for row in buttons:
            for entry in row or []:
                if not (isinstance(entry, tuple) and len(entry) == 2):
                    continue
                label, cb = entry
                if not (isinstance(label, str) and isinstance(cb, str)):
                    continue
                if not cb.startswith("estate:"):
                    continue
                if any(label.startswith(c) for c in _CONTEXTUAL):
                    continue
                if label in seen and seen[label] != cb:
                    collisions.append(f"{name}: {label!r} -> {seen[label]} AND {cb}")
                seen[label] = cb
    assert not collisions, (
        "the same word appears twice on one screen, pointing at two different destinations. "
        "On a phone the keyboard is one flat grid — a Group heading does NOT disambiguate "
        "them. Offenders: " + "; ".join(collisions)
    )
