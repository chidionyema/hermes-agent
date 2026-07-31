"""Shared panel chrome — one navigation row, one truncation rule, for every cockpit panel.

The friction this fixes is measurable, not aesthetic. Before this module, 13 panel modules
built **26 distinct navigation rows and no two were identical**:

    builds.py             ['🚀 Fleet', '🎛 Mission', '📥 Inbox']
    fleet.py              ['📥 Inbox', '🎛 Mission']
    inbox.py              ['🎛 Mission', '🧠 RSI', '🚀 Fleet']
    daemons.py            ['🔄 Refresh', '🎛 Mission']
    prospector_daemon.py  ['▶️ Run watch', '🔄 Refresh', '🚀 Fleet']
    signal_engine.py      ['📡 feed on', '📴 feed off', '🔄 Refresh']

Two consequences on a phone:

1. **"Mission" moves.** It is at index 0, 1 or 2 depending on which panel you are looking at,
   and four rows omit it entirely. Every screen has to be re-read before you can leave it.
2. **Actions are welded into navigation rows.** `▶️ Run watch`, `📡 feed on` and
   `▶️ Start keep-awake` each sit in the same row as a navigation button. A thumb aimed at
   "go back" lands on a live action.

The rule here: navigation is always the LAST row, always the same three buttons, always in the
same order — Mission · Inbox · Refresh-this-panel. Everything else a panel offers stays exactly
where it was, one row higher. No feature is removed; 109 callbacks remain reachable. The only
thing taken away is the need to re-learn the keyboard on every screen.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

ButtonRow = List[Tuple[str, str]]

_NOW = ("⚡️ Now", "estate:refresh")
_RUN = ("🎛 Run", "estate:run")
_TUNE = ("⚙️ Tune", "estate:tune")


def nav(self_action: Optional[str] = None) -> ButtonRow:
    """The one navigation row — the cockpit's spine. Always last, always this order.

    Three positions, and the split between them is the whole information architecture:

        ⚡️ Now   what is true and what needs me   (read + the fix for what is broken)
        🎛 Run   the ~10 verbs I actually perform  (start, stop, restart, run now, bounce)
        ⚙️ Tune  the 29 knobs that configure it    (leverage, caps, batch size, cadence)

    Before this split the three were interleaved on every screen. `se_params` is the proof:
    28 buttons, mixing `🔴 LIVE` (arms real capital) with `📜 Logs` (a read) — and it was
    *still* incomplete, with 6 of the 29 allowlisted values having no button at all. Density
    and coverage were failing at the same time, which is what a wrong container looks like.

    `self_action` re-renders the CURRENT panel. It is the bare glyph, not "🔄 Refresh", so
    four buttons fit one phone row without wrapping.
    """
    row: ButtonRow = [_NOW, _RUN, _TUNE]
    if self_action:
        # removeprefix, NOT lstrip: lstrip takes a character SET, so "se_params" would come
        # back as "_params" (leading 's' and 'e' are both in "estate:").
        act = self_action.removeprefix("estate:")
        row.append(("🔄", f"estate:{act}"))
    return row


def with_nav(rows: Optional[List[ButtonRow]], self_action: Optional[str] = None) -> List[ButtonRow]:
    """Append the standard nav row to a panel's own action rows.

    Any nav-ish button already present in the panel's rows is left alone — this is additive on
    purpose, so adopting it panel by panel can never strand a destination.
    """
    return list(rows or []) + [nav(self_action)]


_SENT_END = re.compile(r"[.!?;:](?:\s|$)")


def clip(text: str, limit: int = 60) -> str:
    """Truncate on a word boundary and mark it, so a cut is never mistaken for the end.

    The panels used to do `text[:60]`, which produced lines like

        🧱 APPROVE [MONEY] `4eb8ae72` failure: prospector guard probe
        🚀 `Prospector ship` BLOCKED · M4: Land the acceptance test as

    Both are truncated mid-sentence with nothing to say so. A reader cannot tell a clipped
    blocker from a complete one, which is the difference between "I know what is wrong" and
    "I must open another screen to find out".
    """
    s = " ".join((text or "").split())
    if len(s) <= limit:
        return s
    cut = s[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:  # only back up to a word boundary if it doesn't gut the line
        cut = cut[:space]
    return cut.rstrip(" ,;:·-") + "…"


_NOISE_LINE = re.compile(
    r"^\s*(?:#{1,6}\s|-{3,}\s*$|={3,}\s*$|\|)"          # headings, rules, table rows
    r"|^\s*[-*]\s*\*\*[^*]+\*\*\s*:?\s*(?:\[|`|$)"      # "- **Key**: [link" doc metadata
)


def first_meaningful_line(text: str, limit: int = 60) -> str:
    """First line that is actually a statement, not document furniture.

    Fleet used to print whatever line came first, which is why real panels showed

        next/blocker: ---
        next/blocker: - **Analysis Source File**: [graphify-out/.graphify_ana
        next/blocker: - **Graph built at commit:** `fddef58` (`graph.json` →

    None of those is a blocker. They are the top of a markdown file. A status line that prints
    `---` teaches the reader to stop reading status lines.
    """
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or _NOISE_LINE.match(stripped):
            continue
        return clip(stripped.lstrip("-*• ").strip(), limit)
    return ""
