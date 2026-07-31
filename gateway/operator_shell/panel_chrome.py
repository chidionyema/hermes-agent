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
from typing import Iterable, List, Optional, Sequence, Tuple

ButtonRow = List[Tuple[str, str]]

# A group may span at most this many grid rows before it stops reading as one idea.
#
# Deliberately a PER-GROUP cap, not a per-panel one. Run carries 19 buttons and every one of
# them is a control the operator must keep — capping the panel would mean either deleting a
# verb or pushing it a tap deeper, and both are a loss of control to buy a cosmetic number.
# What actually made Run unreadable was that its 19 buttons arrived as one undifferentiated
# pile: 10 rows of grid against 4 lines of text, spanning 5 subsystems, with nothing saying
# where one subsystem ended and the next began. Grouped and labelled, 19 is navigable; the
# thing worth bounding is how much sits under a single label.
#
# Counted in ROWS, not buttons. Buttons are the wrong unit: the grid wraps by row, so two
# 2-button rows scan as one block while five stacked single-button rows scan as five. The
# first draft of this capped buttons at 4 and immediately mis-flagged the `👁 Look` group —
# 5 buttons but only 3 rows, and visually the tightest group on the panel.
MAX_GROUP_ROWS = 3

_NOW = ("⚡️ Now", "estate:refresh")
_RUN = ("🎛 Run", "estate:run")
_TUNE = ("⚙️ Tune", "estate:tune")
_FIND = ("🔎", "estate:find")


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
    # Three containers hold 131 destinations, so browsing alone stops working — "buttons may
    # exist but the UI is so confusing i dont know where to find anything" (founder,
    # 2026-07-31). Search is the fourth spine position. Bare glyph, so the row still fits a
    # phone. On the Find panel itself it needs no special case: the glyph rule below already
    # refuses to add a 🔄 whose callback is already in the row, so 🔎 *is* the self button.
    row.append(_FIND)
    if self_action:
        # removeprefix, NOT lstrip: lstrip takes a character SET, so "se_params" would come
        # back as "_params" (leading 's' and 'e' are both in "estate:").
        act = self_action.removeprefix("estate:")
        cb = f"estate:{act}"
        # On a spine panel itself, the spine button ALREADY re-renders this screen, so a 🔄
        # beside it would be the same callback twice — the duplicate-button defect the home
        # card was fixed for. Callers must not have to know this; nav decides.
        if cb not in {a for _l, a in row}:
            row.append(("🔄", cb))
    return row


def with_nav(rows: Optional[List[ButtonRow]], self_action: Optional[str] = None) -> List[ButtonRow]:
    """Append the standard nav row to a panel's own action rows.

    Any nav-ish button already present in the panel's rows is left alone — this is additive on
    purpose, so adopting it panel by panel can never strand a destination.
    """
    return list(rows or []) + [nav(self_action)]


class Group:
    """One labelled block of a panel: a legend line in the text, its own rows in the grid.

    Telegram hands you two independent channels — a message body and a button grid — and it
    will not let you put a heading *between* two button rows. So on every dense screen the
    grid arrives with no legend, and the reader has to infer the seams. `Run` was the worst
    case: 19 buttons across `estate spend`, `signal engine`, `prospector`, `daemons` and four
    read-only destinations, under a header that said "the actions", with three status lines
    that named three of the five groups and mapped to nothing.

    A Group ties the two channels together so they cannot drift apart:

        Group("💹 Signal engine", [[("⏹ Stop", ...), ("♻️ Restart", ...)]], status="`running`")

    `compose()` then guarantees the invariant that makes the legend trustworthy — every legend
    line has buttons under it, and every button sits under a legend line. A group whose rows
    are all filtered out (state-dependent verbs frequently are) prints no legend line at all,
    so the text can never promise a control the grid does not offer.
    """

    __slots__ = ("title", "rows", "status", "note")

    def __init__(
        self,
        title: str,
        rows: Optional[Sequence[ButtonRow]] = None,
        status: str = "",
        note: str = "",
    ):
        self.title = title
        # Drop empty rows here rather than at every call site: `render_run` builds rows
        # conditionally on three probes that are each allowed to return None.
        self.rows: List[ButtonRow] = [list(r) for r in (rows or []) if r]
        self.status = status
        self.note = note

    @property
    def n_buttons(self) -> int:
        return sum(len(r) for r in self.rows)

    def legend(self) -> List[str]:
        """The text lines that stand for this group. Empty when the group has no buttons."""
        if not self.rows:
            return []
        head = f"*{self.title}*"
        if self.status:
            head += f" — {self.status}"
        return [head] + ([f"  _{self.note}_"] if self.note else [])


def compose(
    header: Iterable[str],
    groups: Sequence[Group],
    self_action: Optional[str] = None,
    footer: Iterable[str] = (),
    tail: Optional[Sequence[ButtonRow]] = None,
) -> Tuple[str, List[ButtonRow]]:
    """Build (text, rows) so the text is a legend for the grid, in the same order.

    The join key between the two channels is the emoji: a group titled `💹 Signal engine`
    prints that emoji in the body and its buttons follow immediately in the grid, so a thumb
    scanning down the keyboard can find where it is without reading back up. This is why the
    order of `groups` is the order of BOTH outputs and why nothing may be inserted between
    them — `tail` exists for exactly the rows that belong to no group (the spine), and it is
    appended after every group, never interleaved.

    Returns the same `(text, rows)` shape every panel already returns, so adopting it is a
    per-panel change with no dispatcher involvement.
    """
    live = [g for g in groups if g.rows]
    lines = [l for l in header]
    if live:
        lines.append("")
    for g in live:
        lines.extend(g.legend())
    for f in footer:
        lines.append(f)

    rows: List[ButtonRow] = []
    for g in live:
        rows.extend(g.rows)
    rows.extend(list(tail or []))
    rows.append(nav(self_action))
    return "\n".join(lines), rows


def oversized_groups(groups: Sequence[Group]) -> List[Tuple[str, int]]:
    """Groups that exceed `MAX_GROUP_ROWS` — the density check, as data not an assert."""
    return [(g.title, len(g.rows)) for g in groups if len(g.rows) > MAX_GROUP_ROWS]


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
