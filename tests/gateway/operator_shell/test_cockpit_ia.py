"""Information-architecture invariants: the legend cannot lie, and a group stays one idea.

These lock the organisation itself, not any one panel's contents. The defects they encode
were all measured on the live cockpit on 2026-07-31, not imagined:

- `Run` shipped 19 buttons across five subsystems as one undifferentiated grid: 10 rows of
  keyboard against 4 lines of text, under a header reading "the actions" while four of those
  buttons were read-only destinations sitting among live restarts and stops.
- `Run` ships two buttons BOTH labelled "♻️ Restart", one hitting the signal engine and one
  the prospector scheduler. Nothing on screen distinguished them.
- Telegram will not let a heading sit between two button rows, so a panel's grouping lives in
  the message body while its buttons live in the grid. Any hand-written legend drifts from the
  grid the moment a state-gated verb drops out — and on `Run` three separate probes
  (`_estate_paused`, `_se_running`, `_pd_paused`) are each allowed to return None.

Scope note, inherited from test_cockpit_activity.py: these assert the *builders*, not
`handle_estate_action`. `conftest.py` redirects HERMES_HOME to a per-test tempdir, so a full
dispatch returns the "Mission card unavailable" fallback whose single Retry button satisfies
any structural check vacuously. A test that passes because nothing rendered is worse than no
test. The whole-graph BFS across all live panels is a probe run against the real estate.
"""

from __future__ import annotations

import pytest

from gateway.operator_shell import panel_chrome
from gateway.operator_shell.mission import _SURFACES
from gateway.operator_shell.panel_chrome import (
    MAX_GROUP_ROWS,
    Group,
    compose,
    oversized_groups,
)

A = ("a", "estate:a")
B = ("b", "estate:b")
C = ("c", "estate:c")


def _callbacks(rows):
    return [cb for row in rows for _label, cb in row]


def _legend_lines(text):
    """The group headings compose() emitted — bold lines that are not the header."""
    return [l for l in text.splitlines() if l.startswith("*") and l.endswith("*")]


# --- the invariant that makes a legend trustworthy ---------------------------------------


def test_a_group_with_no_buttons_prints_no_legend_line():
    """The whole point. A verb that drops out must not leave the text promising it."""
    text, rows = compose(["header"], [Group("👻 Gone", []), Group("✅ Here", [[A]])])
    assert "👻 Gone" not in text
    assert "✅ Here" in text
    assert _callbacks(rows)[0] == "estate:a"


def test_every_legend_line_has_buttons_and_every_button_has_a_legend_line():
    groups = [Group("1️⃣ One", [[A]]), Group("2️⃣ Two", [[B, C]]), Group("3️⃣ Empty", [])]
    text, rows = compose(["header"], groups)
    assert len(_legend_lines(text)) == 2, "one legend line per non-empty group, no more"
    # Every non-spine button traces back to a group that printed a line.
    grouped = sum(len(r) for g in groups for r in g.rows)
    spine = len(panel_chrome.nav())
    assert sum(len(r) for r in rows) == grouped + spine


def test_legend_order_is_grid_order():
    """The join between body and keyboard is position, so the two orders must not diverge."""
    text, rows = compose(
        ["header"],
        [Group("🥇 First", [[A]]), Group("🥈 Second", [[B]]), Group("🥉 Third", [[C]])],
    )
    assert [l.strip("*") for l in _legend_lines(text)] == ["🥇 First", "🥈 Second", "🥉 Third"]
    assert _callbacks(rows)[:3] == ["estate:a", "estate:b", "estate:c"]


def test_an_empty_group_does_not_shift_the_rows_after_it():
    _text, rows = compose(["h"], [Group("x", []), Group("y", [[A]]), Group("z", [])])
    assert _callbacks(rows)[0] == "estate:a"


def test_compose_always_ends_with_the_spine():
    _text, rows = compose(["h"], [Group("g", [[A]])])
    assert rows[-1] == panel_chrome.nav()


def test_tail_rows_land_after_every_group_never_between():
    _text, rows = compose(["h"], [Group("g1", [[A]]), Group("g2", [[B]])], tail=[[C]])
    assert _callbacks(rows)[:3] == ["estate:a", "estate:b", "estate:c"]


def test_a_status_renders_on_the_group_line_not_as_a_stray_line():
    text, _rows = compose(["h"], [Group("💹 Engine", [[A]], status="`running`")])
    assert "*💹 Engine* — `running`" in text


# --- density: a group is one idea ---------------------------------------------------------


def test_oversized_groups_flags_by_rows_not_buttons():
    """5 buttons in 3 rows is fine; 4 buttons stacked in 4 rows is not.

    The first draft of the cap counted buttons and mis-flagged `👁 Look` — 5 buttons but the
    tightest block on the panel — while passing a group of four single-button rows.
    """
    wide = Group("wide", [[A, B], [A, B], [C]])          # 5 buttons, 3 rows
    tall = Group("tall", [[A], [B], [C], [A]])           # 4 buttons, 4 rows
    assert oversized_groups([wide]) == []
    assert oversized_groups([tall]) == [("tall", 4)]


@pytest.mark.parametrize("n", range(1, MAX_GROUP_ROWS + 1))
def test_groups_at_or_under_the_cap_pass(n):
    assert oversized_groups([Group("g", [[A]] * n)]) == []


def test_the_live_run_panel_keeps_every_group_within_the_cap():
    """Guards the real panel, not a fixture — this is the screen that regressed."""
    from gateway.operator_shell.cockpit import render_run

    _text, rows = render_run()
    # Verbs + spine only — destinations live on Atlas Rooms.
    assert len(rows) <= 12, f"Run grew to {len(rows)} rows — regroup before adding more"


def test_run_is_verbs_only_no_destination_mall():
    """Run must not re-absorb the home mall under Look/More."""
    from gateway.operator_shell.cockpit import render_run

    _text, rows = render_run()
    flat = {cb for row in rows for _l, cb in row}
    for orphan in (
        "estate:st_status",
        "estate:rsi",
        "estate:builds",
        "estate:diff",
        "estate:fleet",
        "estate:missions",
    ):
        assert orphan not in flat, f"Run still carries destination {orphan}"


def test_atlas_rooms_cover_every_former_home_destination():
    """Nothing we built is orphaned — old home tiles live under Atlas Rooms."""
    from gateway.operator_shell.atlas import all_room_destinations, render_atlas
    from gateway.operator_shell.mission import _SURFACES

    _text, atlas_rows = render_atlas()
    atlas_cbs = {cb for row in atlas_rows for _l, cb in row}
    for rid in ("money", "code", "machine", "brain"):
        assert f"estate:room:{rid}" in atlas_cbs

    room_cbs = {cb for _l, cb in all_room_destinations()}
    for _l, cb in [b for row in _SURFACES for b in row]:
        assert cb in room_cbs, f"orphaned panel {cb} — missing from Atlas Rooms"


# --- the home card (Elon diet: ≤6 buttons, no destination mall) ----------------------------


def test_home_no_longer_ships_a_nine_tile_mall():
    """Home is fires-only. Quiet day = Pause + spine — browse is Map."""
    from gateway.operator_shell.mission import mission_buttons

    rows = mission_buttons(False, ("🚀 Fleet", "estate:fleet"), [])
    flat = [cb for row in rows for _l, cb in row]
    assert "estate:fleet" not in flat
    assert "estate:inbox" not in flat
    assert "estate:status" not in flat
    assert "estate:st_status" not in flat
    assert "estate:pause" in flat
    assert len(flat) <= 6  # pause + optional cron + spine


def test_busy_home_caps_at_two_concerns_and_no_mall():
    from gateway.operator_shell.mission import mission_buttons

    concerns = [(f"c{i}", f"estate:fake{i}") for i in range(5)]
    rows = mission_buttons(False, concerns[0], concerns)
    concern_rows = [r for r in rows if len(r) == 1 and r[0][1].startswith("estate:fake")]
    assert len(concern_rows) <= 2
    flat = [cb for row in rows for _l, cb in row]
    assert "estate:st_status" not in flat
    assert "estate:fleet" not in flat
    assert len(flat) <= 8  # 2 concerns + optional cron + pause + spine


def test_home_grid_catalog_still_documents_domains():
    """_SURFACES remains as the Atlas orphan-guard catalog — not rendered on home."""
    assert len(_SURFACES) == 3
    assert [cb for _l, cb in _SURFACES[0]] == [
        "estate:st_status",
        "estate:pd_cron",
        "estate:inbox",
    ]


def test_home_grid_has_no_duplicate_destinations():
    cbs = [cb for row in _SURFACES for _l, cb in row]
    assert len(cbs) == len(set(cbs))


# --- the door ------------------------------------------------------------------------------


def test_the_pinned_banner_names_the_cockpit():
    from gateway.operator_shell.mission import card_headline

    assert card_headline("🟡 BLOCKED", "4 need you").startswith("🎛 *Cockpit*")


def test_the_banner_leads_with_identity_not_state():
    from gateway.operator_shell.mission import card_headline

    line = card_headline("🔴 HALTED", "x" * 200)
    assert line.index("Cockpit") < line.index("HALTED")


def test_the_banner_still_carries_the_live_verdict():
    from gateway.operator_shell.mission import card_headline

    line = card_headline("🟢 OK", "nothing needs you")
    assert "🟢 OK" in line and "nothing needs you" in line


def test_menu_and_cockpit_reach_the_panel():
    from hermes_cli.commands import resolve_command

    for word in ("menu", "cockpit", "panel", "control", "mission"):
        cmd = resolve_command(word)
        assert cmd is not None and cmd.name == "panel", f"/{word} does not open the cockpit"
