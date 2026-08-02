"""Shared panel chrome — one navigation row, one truncation rule, for every cockpit panel.

Spine (always last, always this order):

    ⚡️ Now   fires — concerns, approve, estate pause
    🎛 Run   verbs — start / stop / restart / run-now
    ⚙️ Tune  knobs — se_set / pd_set / brain / cron
    🗺 Map   orient — Atlas rooms when empty, search when typed

Before this chrome, panels invented their own nav rows (Mission/Inbox/Fleet mixed with live
verbs). The rule: navigation is always the LAST row; actions never sit beside it.
"""

from __future__ import annotations

import re
import time
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

_NOW = ("🏠 Home", "estate:refresh")
_RUN = ("⚡ Actions", "estate:run")
_SDLC = ("💻 SDLC", "estate:sdlc")
_TUNE = ("⚙️ Tune", "estate:tune")
# Callback stays estate:find for compatibility; glyph is Map — empty opens Atlas.
_MAP = ("🗺 Browse", "estate:find")

# Severity legend — one row, glued to the bottom of every panel via compose().
# The "?" prefix marks it as a definition, not a status, so it never collides with a
# real-state glyph anywhere on the card.
LEGEND = "🟢 ok · 🟡 watch · 🔴 act · ⚠️ unproven"

# The four state-glyph slots. Centralised so a panel can never invent its own.
# - OK      : verified clean by the live probe
# - WATCH   : degraded but not yet a halt — operator should glance
# - ACT     : operator action required
# - UNPROVEN: probe could not run, NOT the same as OK. Always distinct from green.
VERDICT_GLYPHS = {
    "ok": "🟢",
    "watch": "🟡",
    "act": "🔴",
    "unproven": "⚠️",
}


def nav(self_action: Optional[str] = None) -> ButtonRow:
    """The one navigation row — the cockpit's spine. Always last, always this order.

        🏠 Home   fires (concerns, approve, estate pause)
        ⚡ Actions   verbs (start, stop, restart, run now)
        💻 SDLC   pipeline (Assign → Board → Fleet → Review → Ship → Learn)
        ⚙️ Tune  knobs (leverage, caps, batch, cadence, brain)
        🗺 Browse   orient (Atlas rooms empty; type a word to search)

    `self_action` re-renders the CURRENT panel as bare 🔄. On Map itself the 🗺 glyph
    already re-opens Atlas, so no duplicate 🔄 is added.
    """
    row: ButtonRow = [_NOW, _RUN, _SDLC, _MAP]
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


def panel_stamp(
    plus_id: Optional[str] = None,
    rendered_at: Optional[float] = None,
) -> str:
    """One-line footer every panel can append: absolute time + relative age + id.

    The mission card invented this format (`2026-07-31 19:15:38 UTC · auto-refresh`).
    Every other panel omitted it (U12) — the operator had no way to tell whether a
    card was 5 seconds or 5 minutes stale. Centralised here so all panels read the
    same way, and the cost is one helper export, not a refactor.

    The `plus_id` is an optional panel/handler identifier (e.g. `fleet`, `daemons`)
    that gets appended for diagnosability — when the operator pastes a screenshot
    into chat, the id tells which panel without a guess.

    ``rendered_at`` is a unix timestamp for when the payload was produced (e.g. cache
    ``ts``). Defaults to now → "just now".
    """
    from datetime import datetime, timezone

    now = time.time()
    ts = float(rendered_at) if rendered_at is not None else now
    iso = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    age_s = max(0, int(now - ts))
    if age_s < 5:
        age = "just now"
    elif age_s < 90:
        age = f"{age_s}s ago"
    elif age_s < 3600:
        age = f"{age_s // 60}m ago"
    else:
        age = f"{age_s // 3600}h ago"
    id_bit = f" · {plus_id}" if plus_id else ""
    return f"_{iso} · {age}{id_bit}_"


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
    with_legend: bool = True,
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

    # Severity legend appears at the bottom of every panel. It is opt-out (panels that
    # already use heavy ⏳/🔴/🟡/⚠️ chrome can disable with with_legend=False) but the
    # default is on — a panel that never explains its own glyphs is a panel the operator
    # has to learn by heart, which is the wrong cost.
    if with_legend:
        lines.extend(["", f"_{LEGEND}_"])

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
