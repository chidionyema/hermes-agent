"""Regression: a `_CONST = ("label", "estate:x")` button must be used, not just declared.

2026-08-02 (39402e463f) defined `_TUNE = ("⚙️ Tune", "estate:tune")` in panel_chrome.py,
kept it in the module docstring and in 15+ other panels' outbound links, but dropped it
from the one list (`nav()`'s `row`) that actually renders the spine. The constant existed;
nothing in the file ever read it again. "23 end-to-end tests prove every button works"
shipped anyway, because none of them checked that a declared constant gets *consumed*
inside its own module — every existing test renders panels and asserts on their output,
which only catches a missing button if some other test happens to assert it's present.

This test skips rendering entirely and reads source text: a module-level
`_NAME = ("label", "estate:...")` assignment must have its identifier `_NAME` referenced
at least once more in the same file (in a list, a call, anywhere) or it is dead on arrival
— defined, documented, linked-to by other files, and reachable from nowhere. Verified
against the actual buggy commit (39402e463f) before this test was written: it flags
`_TUNE` and only `_TUNE` (`_NOW`/`_RUN`/`_SDLC`/`_MAP` each had 2 occurrences, `_TUNE` had 1)
— see `../../../docs/audits/` or the commit that added this file for the raw run.

This is a floor (BASELINE = 0), same ratchet shape as test_destination_vocabulary.py: any
new occurrence fails CI immediately, it does not need a human to notice the panel first.
"""
from __future__ import annotations

import glob
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PANEL_DIR = _REPO_ROOT / "gateway" / "operator_shell"

_CONST_DEF = re.compile(r'^(_[A-Z][A-Z0-9_]*)\s*=\s*\("[^"]+",\s*"estate:[^"]+"\)', re.M)

BASELINE = 0


def _undeclared_dead_constants():
    dead = []
    for path in sorted(glob.glob(str(_PANEL_DIR / "*.py"))):
        text = Path(path).read_text(encoding="utf-8")
        for m in _CONST_DEF.finditer(text):
            name = m.group(1)
            uses = len(re.findall(rf"\b{re.escape(name)}\b", text))
            if uses <= 1:
                dead.append((Path(path).name, name))
    return dead


def test_no_declared_button_constant_is_unreferenced():
    dead = _undeclared_dead_constants()
    assert len(dead) <= BASELINE, (
        f"button constant(s) declared but never referenced again in their own file "
        f"(defined, then unreachable — the Tune-spine defect class): {dead}. "
        f"If this is a new false positive, wire it in; if the pattern itself doesn't "
        f"apply, don't raise BASELINE without a comment justifying why."
    )
