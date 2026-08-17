"""The adoption meter — the spine is measured as USED, not merely as existing.

Every other test in this directory is a ratchet on VIOLATIONS, pinned at zero:
`test_every_button_dispatches.py:104 _UNBUILT = {}`, `test_destination_vocabulary.py:75
BASELINE = 0`. Ratchets stop decay. They cannot create coherence, and on 2026-08-14 the
founder's verdict was that the cockpit is "cryptic and confusing" while five tests aimed at
exactly that symptom — `test_cockpit_ia`, `test_no_screen_says_one_word_twice`,
`test_destination_vocabulary`, `test_panel_chrome_spine`, `test_action_outcome_is_visible` —
were all green.

The measurement that explained it — this file's own census, run 2026-08-14 over the 61 panel
modules in `gateway/operator_shell/` (63 files less `panel_chrome`, `nav_stack`, `__init__`):

    nav() / with_nav()   39 modules   ← the cosmetic part: adopted
    compose()             4 modules   ← the part that makes the body a legend for the grid
    Group()               3 modules
    VERDICT_GLYPHS        1 module    ← the one state vocabulary
    raw 🟢/🔴/🟡         33 modules   ← every panel inventing that vocabulary itself

The spine was never missing. It was OPTIONAL, and the three pieces that actually produce
one coherent product had 6%, 5% and 2% uptake. This file is the instrument that makes them
mandatory *gradually*: floors that only ever rise, and a ceiling on raw glyphs that only
ever falls. It is deliberately the inverse of a violation ratchet — it fails when the
migration STOPS, not only when someone regresses.

Failure output is the migration queue: the assertion names the modules still outside.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Set

PANEL_DIR = Path(__file__).resolve().parents[3] / "gateway" / "operator_shell"

# `panel_chrome` DEFINES the spine, so it always matches every pattern and would inflate
# every count by one. `nav_stack` is history, not a panel.
_NOT_PANELS = {"panel_chrome.py", "nav_stack.py", "__init__.py"}

# ---------------------------------------------------------------------------
# THE FLOORS. Each is the measured count on the day it was last raised.
#
# Raise them as panels migrate — that is the entire point, and the number in the commit
# message is the progress report. NEVER lower one to make a build green: a drop means a
# panel stopped using the spine, which is the regression this file exists to catch.
# ---------------------------------------------------------------------------
FLOOR_COMPOSE = 4
FLOOR_GROUP = 3
FLOOR_VERDICT_GLYPHS = 1
FLOOR_NAV = 39

# THE CEILING — modules that hardcode a state glyph instead of reading VERDICT_GLYPHS.
# Only ever lower this. Each panel migrated to VERDICT_GLYPHS drops it by one.
CEILING_RAW_GLYPHS = 33

_RAW_GLYPH = re.compile(r"[🟢🔴🟡]")


def _panels() -> List[Path]:
    return sorted(p for p in PANEL_DIR.glob("*.py") if p.name not in _NOT_PANELS)


def _census() -> Dict[str, Set[str]]:
    """Which modules use which piece of the spine. Source text, deliberately.

    Not an import-and-introspect: several panels do real work at import (coordinator
    bridges, launchd probes), and a census that can hang is a census nobody runs. The cost
    is that a dynamically-built call is invisible here — acceptable for a COVERAGE meter,
    where the question is "did this module adopt the helper", not "what did it render".
    That distinction matters: judging RENDERED text this way would repeat memory
    `a-source-scanner-cannot-see-a-label-built-at-render-time`, which is why the crawler
    (U1) captures output at render time and this file does not try to.
    """
    seen: Dict[str, Set[str]] = {k: set() for k in
                                 ("compose", "group", "glyphs", "nav", "stamp", "raw_glyph")}
    for path in _panels():
        src = path.read_text(errors="replace")
        name = path.name
        if re.search(r"\bcompose\s*\(", src):
            seen["compose"].add(name)
        if re.search(r"\bGroup\s*\(", src):
            seen["group"].add(name)
        if "VERDICT_GLYPHS" in src:
            seen["glyphs"].add(name)
        if re.search(r"\bwith_nav\s*\(|(?<![\w.])nav\s*\(", src):
            seen["nav"].add(name)
        if "panel_stamp" in src:
            seen["stamp"].add(name)
        if _RAW_GLYPH.search(src) and "VERDICT_GLYPHS" not in src:
            seen["raw_glyph"].add(name)
    return seen


def _report(seen: Dict[str, Set[str]]) -> str:
    total = len(_panels())
    lines = [f"spine adoption across {total} panel modules:"]
    for key, floor in (("compose", FLOOR_COMPOSE), ("group", FLOOR_GROUP),
                       ("glyphs", FLOOR_VERDICT_GLYPHS), ("nav", FLOOR_NAV)):
        lines.append(f"  {key:<8} {len(seen[key]):>3}/{total}  (floor {floor})")
    lines.append(f"  raw glyph {len(seen['raw_glyph']):>3}/{total}  (ceiling {CEILING_RAW_GLYPHS})")
    return "\n".join(lines)


def test_compose_adoption_only_rises():
    """`compose()` is the one helper that ties the message body to the button grid.

    It guarantees the invariant that makes a panel readable: every legend line has buttons
    under it and every button sits under a legend line. A panel that builds `(text, rows)`
    by hand can promise a control the grid does not offer — and 59 of 63 do exactly that.
    """
    seen = _census()
    got = len(seen["compose"])
    missing = sorted(set(p.name for p in _panels()) - seen["compose"])
    assert got >= FLOOR_COMPOSE, (
        f"compose() adoption fell to {got}, floor is {FLOOR_COMPOSE}.\n{_report(seen)}\n"
        f"still hand-building (text, rows): {', '.join(missing[:15])}…"
    )


def test_group_adoption_only_rises():
    seen = _census()
    assert len(seen["group"]) >= FLOOR_GROUP, (
        f"Group() adoption fell to {len(seen['group'])}, floor is {FLOOR_GROUP}.\n"
        f"{_report(seen)}"
    )


def test_verdict_glyph_adoption_only_rises():
    seen = _census()
    assert len(seen["glyphs"]) >= FLOOR_VERDICT_GLYPHS, (
        f"VERDICT_GLYPHS adoption fell to {len(seen['glyphs'])}, floor is "
        f"{FLOOR_VERDICT_GLYPHS}.\n{_report(seen)}"
    )


def test_nav_adoption_only_rises():
    seen = _census()
    assert len(seen["nav"]) >= FLOOR_NAV, (
        f"nav() adoption fell to {len(seen['nav'])}, floor is {FLOOR_NAV}.\n{_report(seen)}"
    )


def test_hand_rolled_state_glyphs_only_fall():
    """The state vocabulary must converge on one table, not 35 private copies.

    🟢/🟡/🔴 mean four specific things (`panel_chrome.VERDICT_GLYPHS`), and the fourth —
    ⚠️ unproven — is the one a hand-rolled panel always omits, so "the probe could not run"
    renders as green. That is the `estate-probe-green-fence-line-is-not-evidence` failure
    reproduced once per panel.
    """
    seen = _census()
    offenders = sorted(seen["raw_glyph"])
    assert len(offenders) <= CEILING_RAW_GLYPHS, (
        f"{len(offenders)} modules hardcode a state glyph, ceiling is {CEILING_RAW_GLYPHS} "
        f"and may only fall.\n{_report(seen)}\noffenders: {', '.join(offenders)}"
    )


def test_the_meter_reports_the_migration_queue(capsys):
    """Not a gate — the census printed, so `-s` on this file is the progress report."""
    seen = _census()
    print("\n" + _report(seen))
    remaining = sorted(set(p.name for p in _panels()) - seen["compose"])
    print(f"\nnext for compose() migration ({len(remaining)} left):")
    for name in remaining[:10]:
        print(f"  - {name}")
    assert _panels(), "no panel modules found — the meter is measuring nothing"
