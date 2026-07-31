"""Cockpit invariants: no screen offers one action twice, and every tap is recorded.

Both were real defects. The home card shipped `estate:resume` under two labels; Run, Tune, RSI
and Store each shipped a 🔄 whose callback was identical to a button beside it. And the operator
log did not exist at all — `Proof` receipts were rendered to the screen and discarded, so
"which button fails most" had no answer.

Scope note: these assert the *button builders*, not `handle_estate_action`. `conftest.py`
redirects HERMES_HOME to a per-test tempdir (correctly — tests must not read the live estate),
so a full dispatch here returns the "Mission card unavailable" fallback, whose single Retry
button trivially satisfies any duplicate check. A test that passes because nothing rendered is
worse than no test. The whole-graph sweep across all 26 live panels is a probe, run against the
real estate, not part of this suite.
"""

from __future__ import annotations

import json

import pytest

from gateway.operator_shell import activity
from gateway.operator_shell.cockpit import render_activity
from gateway.operator_shell.mission import mission_buttons
from gateway.operator_shell.panel_chrome import nav

SPINE = ["estate:refresh", "estate:run", "estate:tune"]


def _callbacks(rows):
    return [cb for row in rows for _label, cb in row]


def _dupes(rows):
    cbs = _callbacks(rows)
    return sorted({cb for cb in cbs if cbs.count(cb) > 1})


@pytest.mark.parametrize("self_action", ["refresh", "run", "tune", "estate:run"])
def test_nav_omits_the_refresh_glyph_on_a_spine_panel(self_action):
    """On Run, the spine's own 🎛 Run already re-renders Run. A 🔄 beside it is the same
    callback twice — the caller must not have to know that, so nav decides."""
    row = nav(self_action)
    cbs = [cb for _label, cb in row]
    assert cbs[:3] == SPINE
    assert len(cbs) == 3, f"nav({self_action!r}) appended a redundant refresh: {cbs}"


@pytest.mark.parametrize("self_action", ["se_params", "activity:7", "st_status"])
def test_nav_keeps_the_refresh_glyph_off_spine(self_action):
    row = nav(self_action)
    cbs = [cb for _label, cb in row]
    assert cbs[:3] == SPINE
    assert cbs[3] == f"estate:{self_action}", (
        # removeprefix, not lstrip: lstrip takes a character SET, so "se_params" would come
        # back as "_params" (both 's' and 'e' are in "estate:").
        f"nav({self_action!r}) mangled the self-action: {cbs}"
    )


@pytest.mark.parametrize(
    "paused,primary,concerns",
    [
        (False, ("🚀 Fleet", "estate:fleet"), []),
        # The exact shipped defect: the top concern IS resume, and the card also carried a
        # standing Pause/Resume row.
        (True, ("▶️ Resume spend", "estate:resume"), [("▶️ Resume spend", "estate:resume")]),
        (False, ("⛽ Fuel", "estate:system_fuel"),
         [("⛽ Fuel", "estate:system_fuel"), ("📥 Inbox", "estate:inbox"),
          ("⚙️ Daemons", "estate:daemons"), ("🏗 CI", "estate:builds")]),
    ],
)
def test_mission_card_never_offers_the_same_action_twice(paused, primary, concerns):
    rows = mission_buttons(paused, primary, concerns)
    assert not _dupes(rows), f"home card duplicates: {_dupes(rows)}"
    assert [cb for _l, cb in rows[-1]][:3] == SPINE


def test_mission_card_caps_concerns_but_says_how_many_were_hidden():
    """Four concerns must not become four rows — the card is read on a phone. The count is
    printed by render_mission_card; here we only assert the button cap holds."""
    concerns = [(f"c{i}", f"estate:fake{i}") for i in range(6)]
    rows = mission_buttons(False, concerns[0], concerns)
    concern_rows = [r for r in rows if len(r) == 1 and r[0][1].startswith("estate:fake")]
    assert len(concern_rows) <= 3


def test_activity_panel_has_no_duplicate_callbacks():
    """The window you are already in is a no-op tap AND collides with nav's 🔄."""
    for days in (1, 7, 30):
        _text, rows = render_activity(days)
        assert not _dupes(rows), f"activity({days}d) duplicates: {_dupes(rows)}"


def test_activity_records_action_outcome_and_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "_dir", lambda: tmp_path)

    class View:
        proof_receipt = "✅ *DONE* — Mission card refreshed"
        toast = "Refreshed"
        ok = True
        paused = False

    activity.record("refresh", "rid-1", view=View(), ms=42.0)
    activity.record("tune:sizing", "rid-2", view=View(), ms=8.0)
    activity.record("se_restart", "rid-3", status="error", error="RuntimeError('boom')", ms=5.0)

    rows = [json.loads(ln) for ln in
            sorted(tmp_path.glob("*.jsonl"))[0].read_text().splitlines() if ln.strip()]
    by = {(r["action"], r.get("arg", "")): r for r in rows}

    assert by[("refresh", "")]["status"] == "ok"
    assert by[("refresh", "")]["outcome"] == "done"
    assert by[("refresh", "")]["ms"] == 42.0
    # The arg is split out so `tune:sizing` and `tune:spend` are distinguishable in the rollup.
    assert ("tune", "sizing") in by
    assert by[("se_restart", "")]["status"] == "error"
    assert "RuntimeError" in by[("se_restart", "")]["error"]

    roll = activity.rollup(1)
    assert roll["total"] == 3
    assert roll["failure_total"] == 1
    assert ("se_restart", 1) in roll["failures"]
    assert ("refresh", 1) in roll["top"]


def test_record_never_raises_even_when_the_disk_is_gone(tmp_path, monkeypatch):
    """An audit trail that can take the cockpit down is a liability, not an asset."""

    def explode():
        raise OSError("disk full")

    monkeypatch.setattr(activity, "_dir", explode)
    activity.record("refresh", "rid-x", ms=1.0)  # must not raise
    assert activity.rollup(1)["total"] == 0


def test_error_text_is_bounded(tmp_path, monkeypatch):
    """A traceback repr can be kilobytes; this file is read on a phone."""
    monkeypatch.setattr(activity, "_dir", lambda: tmp_path)
    activity.record("boom", "rid-y", status="error", error="x" * 5000)
    row = json.loads(sorted(tmp_path.glob("*.jsonl"))[0].read_text().splitlines()[0])
    assert len(row["error"]) <= 300
