"""One destination, one name — measured, ratcheted, and being paid down.

An operator navigates by recognising a word. `estate:otto_health` is currently reachable
as "🧠 Otto health", "🧠 Health", "📊 Details", "📊 Dashboard" and "📊 Self-audit"; a
person who learns "Self-audit" on one screen has to re-learn it on the next four. That is
the difference between a cockpit that feels designed and one that feels assembled.

Measured 2026-08-09 by the scanner below: **39 of 153 estate callbacks carried more than one
label**, after exempting the labels whose whole job is contextual (see `_CONTEXTUAL`). Paid
down the same day to **0** — 81 label rewrites across 23 modules, derived from one canonical
name per callback rather than hand-typed, so no site could be missed and none renamed twice.

The ratchet is therefore absolute now: `BASELINE = 0`, and any destination that acquires a
second name fails this test. There is no longer a "lower the baseline as you go" step,
because there is nothing left to lower.

Three of the 39 were not drift at all and are declared in `_DESIGNED` rather than renamed —
an exemption you can read and argue with beats a rename that quietly destroys a working
metaphor. `test_the_designed_exemptions_are_all_still_real` stops one outliving its button.

Why static analysis rather than rendering every panel: a panel needs a live estate to
render, and this question is about the source text, not the runtime. `test_every_button_
dispatches.py` already covers the runtime half — that every callback reaches a handler.
Together: every button goes somewhere (that file) and every somewhere has one name (this
one). Neither side is a hand-maintained table.
"""

from __future__ import annotations

import ast
import collections
import functools
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

# (module, callback) pairs where a second name is the DESIGN. Unlike `_CONTEXTUAL`, which is a
# property of the label, these are a property of the PANEL: the screen supplies a vocabulary of
# its own, and the canonical word would be the wrong word there.
_DESIGNED: dict[tuple[str, str], str] = {
    # sdlc.py renders a numbered legend that glosses every stage WITH its destination —
    # "*4. Review* — decisions / inbox", "*5. Ship* — CI / builds / deploys",
    # "*6. Learn* — RSI / self-improvement" (sdlc.py:198-211) — then captions the keyboard
    # "_Tap any stage to go deep._" (:213). So the labels are stage names in a pipeline the
    # panel defines on screen and maps to the destination in the same breath; that is the same
    # reason "Back" is exempt. Renaming them to Missions / Inbox / CI / RSI would leave a
    # numbered legend describing buttons that no longer exist.
    ("sdlc.py", "estate:missions"): "pipeline stage 'Board'",
    ("sdlc.py", "estate:inbox"): "pipeline stage 'Review'",
    ("sdlc.py", "estate:builds"): "pipeline stage 'Ship'",
    ("sdlc.py", "estate:rsi"): "pipeline stage 'Learn'",
    # Client-facing surfaces. A client is not an operator and has never seen an "Inbox"; the
    # word for "reach the people running this" is Contact / Feedback.
    ("commercial_ui.py", "estate:inbox"): "client wording 'Contact Team'",
    ("projects.py", "estate:inbox"): "client wording 'Feedback' (client_mode branch)",
}

BASELINE = 0


@functools.lru_cache(maxsize=None)
def _scan_labels(apply_designed: bool) -> dict[str, set[str]]:
    """The scan itself. Cached — see `_labels`."""
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
            if apply_designed and (path.name, cb.value) in _DESIGNED:
                continue
            out[cb.value].add(label.value)
    return out


def _labels(apply_designed: bool = True) -> dict[str, set[str]]:
    """callback -> every label any panel renders it under.

    Memoised, because the scan AST-parses every module in the cockpit — ~11s — and the five
    tests below all want the same snapshot of the same unchanging source tree. Measured
    2026-08-10: this file cost 54.85s, of which ~44s was re-parsing the identical files four
    more times. Returns a fresh copy each call so a caller that mutates the result cannot
    poison the next test.
    """
    return {k: set(v) for k, v in _scan_labels(apply_designed).items()}


def _conflicts() -> dict[str, set[str]]:
    return {k: v for k, v in _labels().items() if len(v) > 1}


def test_the_scanner_sees_the_cockpit_at_all():
    """A scanner that silently matches nothing would let every other test here pass."""
    labels = _labels()
    assert len(labels) >= 120, f"only {len(labels)} callbacks found — scanner is broken"
    assert "estate:tune" in labels


def test_no_destination_carries_two_names():
    """Was a ratchet from 39 while the backlog was paid down; now an absolute rule.

    If this fails, the fix is normally to use the name the destination already has everywhere
    else — not to add an entry to `_DESIGNED`. That set is for a panel that genuinely speaks
    its own vocabulary on screen, and it is six entries, not an escape hatch.
    """
    conflicts = _conflicts()
    assert len(conflicts) <= BASELINE, (
        f"{len(conflicts)} destinations carry more than one label. Offenders: "
        + ", ".join(f"{k} = {sorted(v)}" for k, v in sorted(conflicts.items()))
    )


def test_the_designed_exemptions_are_all_still_real():
    """An exemption that outlives its button is a silent hole in the rule above.

    Every `_DESIGNED` entry must still be a live second name — if the button was deleted or
    already renamed to the canonical word, the entry is stale and must go.
    """
    raw = _labels(apply_designed=False)
    canonical = _labels()
    stale = []
    for (module, cb), why in _DESIGNED.items():
        others = raw.get(cb, set()) - canonical.get(cb, set())
        if not others:
            stale.append(f"{module} / {cb} ({why})")
    assert not stale, "stale _DESIGNED entries — the exempted label is gone: " + "; ".join(stale)


def _sdlc_rendered_strings() -> list[str]:
    """Literal strings in `render_sdlc`'s `lines = [...]` — i.e. text an operator actually sees.

    Deliberately NOT a substring search of the module: "Assign → Board → Fleet → Review → Ship →
    Learn" appears in sdlc.py's own docstring (`sdlc.py:1`), so a grep for it passes whether or
    not the panel renders anything at all. The docstring is not the product.
    """
    tree = ast.parse((PANEL_DIR / "sdlc.py").read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "render_sdlc"
    )
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "lines" for t in node.targets)
            and isinstance(node.value, ast.List)
        ):
            return [
                e.value for e in node.value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
    raise AssertionError("render_sdlc no longer builds a literal `lines` list")


def test_the_sdlc_pipeline_still_prints_the_legend_its_labels_depend_on():
    """`_DESIGNED` exempts four sdlc.py labels *because the panel names each stage and its
    destination in the same line* — "*4. Review* — decisions / inbox". If those glosses ever go,
    the exemption's justification goes with it and the four become unexplained second names.
    """
    rendered = _sdlc_rendered_strings()
    # stage word (the button label) -> a word from the destination's canonical name.
    for stage, destination in (
        ("Board", "missions"),
        ("Review", "inbox"),
        ("Ship", "builds"),
        ("Learn", "RSI"),
    ):
        assert any(stage in ln and destination in ln for ln in rendered), (
            f"sdlc.py renders no line tying the '{stage}' button to {destination!r}; "
            f"the _DESIGNED exemption for it is now unjustified. Rendered: {rendered}"
        )


def test_restarting_the_coordinator_has_exactly_one_name():
    """mission.py:501 said '🔄 Restart Coord' beside a '♻️' button for a different daemon,
    while command_palette.py:37 said '♻️ Restart coord' for the same callback."""
    assert _labels()["estate:restart"] == {"♻️ Restart coord"}
