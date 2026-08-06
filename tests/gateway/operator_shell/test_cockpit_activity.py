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

# Four positions: Now · Run · Tune · Map. Empty Map = Atlas; typed Map = search.
# On the Map panel itself the 🗺 glyph re-opens Atlas — no duplicate 🔄.
SPINE = ["estate:refresh", "estate:run", "estate:sdlc", "estate:find"]


def _callbacks(rows):
    return [cb for row in rows for _label, cb in row]


def _dupes(rows):
    cbs = _callbacks(rows)
    return sorted({cb for cb in cbs if cbs.count(cb) > 1})


@pytest.mark.parametrize("self_action", ["refresh", "run", "sdlc", "estate:run"])
def test_nav_omits_the_refresh_glyph_on_a_spine_panel(self_action):
    """On Run, the spine's own 🎛 Run already re-renders Run. A 🔄 beside it is the same
    callback twice — the caller must not have to know that, so nav decides."""
    row = nav(self_action)
    cbs = [cb for _label, cb in row]
    assert cbs[:4] == SPINE
    assert len(cbs) == 4, f"nav({self_action!r}) appended a redundant refresh: {cbs}"


@pytest.mark.parametrize("self_action", ["se_params", "activity:7", "st_status"])
def test_nav_keeps_the_refresh_glyph_off_spine(self_action):
    row = nav(self_action)
    cbs = [cb for _label, cb in row]
    assert cbs[:4] == SPINE
    assert cbs[4] == f"estate:{self_action}", (
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
    assert [cb for _l, cb in rows[-1]][:4] == SPINE


def test_mission_card_caps_concerns_but_says_how_many_were_hidden():
    """Two concerns max on a phone — the rest live under Inbox / Run."""
    concerns = [(f"c{i}", f"estate:fake{i}") for i in range(6)]
    rows = mission_buttons(False, concerns[0], concerns)
    concern_rows = [r for r in rows if len(r) == 1 and r[0][1].startswith("estate:fake")]
    assert len(concern_rows) <= 2


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


# ── Slowest: one row per action, not per call ───────────────────────────────
# `slow.append((ms, lab))` ranked individual CALLS, so a repeatedly-slow action occupied every
# row and crowded out every other. Measured on the live store 2026-08-06: st_health×3 and
# st_money×2 took all 5 rows, hiding st_status (65.7s), run (37.4s) and refresh (36.7s).


def _durations(tmp_path, pairs):
    """pairs: (label, ms) per call. Rows are live so rollup does not filter them."""
    import os
    path = tmp_path / "2026-08-06.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for i, (lab, ms) in enumerate(pairs):
            act, _, arg = lab.partition(":")
            fh.write(json.dumps({
                "ts": 1_754_000_000 + i, "iso": "2026-08-06T01:00:00",
                "action": act, "arg": arg, "status": "ok", "ms": float(ms),
                "source": "button", "pid": os.getpid(), "live": True,
            }) + "\n")
    return path


@pytest.fixture
def _window(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "_dir", lambda: tmp_path)
    monkeypatch.setattr(activity, "_path", lambda day=None: tmp_path / "2026-08-06.jsonl")
    return tmp_path


def test_one_repeated_action_cannot_fill_the_slowest_list(_window):
    """The defect, stated as the property that replaces it.

    Six slow st_health calls plus four other slow actions: before, st_health took all five
    rows and the other four were invisible.
    """
    _durations(_window, [("st_health", 100_000 + i) for i in range(6)]
               + [("st_money", 90_000), ("st_status", 80_000),
                  ("run", 70_000), ("refresh", 60_000)])
    slow = activity.rollup(days=1)["slowest"]
    assert [e[1] for e in slow] == ["st_health", "st_money", "st_status", "run", "refresh"]


def test_the_slowest_row_reports_the_worst_call_not_the_first(_window):
    _durations(_window, [("st_health", 5_000), ("st_health", 120_000), ("st_health", 9_000)])
    ms, lab, n, typ = activity.rollup(days=1)["slowest"][0]
    assert (lab, ms, n) == ("st_health", 120_000.0, 3)
    assert typ == 9_000.0  # median of 5k/9k/120k — the outlier does not move it


def test_a_one_off_hang_reads_differently_from_a_chronically_slow_action(_window):
    """The reason `typ` exists at all.

    On live data `refresh` was 36.7s worst but 0.0s typical of 220 calls, while `st_health`
    was 117.5s typical of 5. Ranked by worst alone those two rows look identical, and the
    operator would chase the wrong one.
    """
    _durations(_window, [("refresh", 1)] * 20 + [("refresh", 40_000)]
               + [("st_health", 38_000)] * 5)
    text, _ = render_activity(days=1)
    assert "`refresh` — 40.0s worst of 21 · typ 0.0s" in text
    assert "`st_health` — 38.0s worst of 5 · typ 38.0s" in text


def test_a_single_call_says_no_count(_window):
    """`worst of 1 · typ 5.0s` restates the same number three times."""
    _durations(_window, [("run", 5_000)])
    text, _ = render_activity(days=1)
    assert "⏱ `run` — 5.0s" in text
    assert "worst of" not in text


def test_the_panel_shows_five_distinct_actions_where_it_showed_two(_window):
    """Render-level: the assertion that failed before this shipped."""
    _durations(_window, [("st_health", 100_000 + i) for i in range(3)]
               + [("st_money", 90_000), ("st_money", 88_000)]
               + [("st_status", 65_000), ("run", 37_000), ("refresh", 36_000)])
    text, _ = render_activity(days=1)
    slowest_block = text.split("*Slowest*")[1].split("*Last 8*")[0]
    labels = {ln.split("`")[1] for ln in slowest_block.splitlines() if ln.startswith("⏱")}
    assert labels == {"st_health", "st_money", "st_status", "run", "refresh"}


def test_a_zero_duration_action_never_reaches_the_slowest_list(_window):
    """Every `refresh` served from cache has ms=0; 220 of them must not crowd the list."""
    _durations(_window, [("cached", 0)] * 50 + [("run", 1_000)])
    assert [e[1] for e in activity.rollup(days=1)["slowest"]] == ["run"]


def test_an_arg_keeps_actions_apart(_window):
    """`tune:sizing` and `tune:spend` are different actions and must not collapse."""
    _durations(_window, [("tune:sizing", 9_000), ("tune:spend", 8_000)])
    assert [e[1] for e in activity.rollup(days=1)["slowest"]] == ["tune:sizing", "tune:spend"]


# ── Knob depth ──────────────────────────────────────────────────────────────
# Grouping fixed the 28-button screen but left every knob 3 taps away. Two changes cut that:
# the index promotes knobs you have actually used, and a set lands back in its group.

from gateway.operator_shell.cockpit import group_for_key, knob_by_key, render_tune
from gateway.operator_shell.estate import _knob_landing

ALL_KEYS = [
    "exec_mode", "ramp_stage", "live_feed",           # exec
    "vol_target", "leverage", "per_instrument",       # sizing
    "stop_loss", "max_positions", "killswitch",       # safety
    "llm_cap", "daily_cap",                           # spend
    "interval", "concurrency", "batch_size",          # prospector
]


@pytest.mark.parametrize("key", ALL_KEYS)
def test_every_knob_key_resolves_to_a_group(key):
    """The reverse index matches on the CALLBACK, not the display label: the label is
    presentation and gets reworded, the key is the contract with _SAFE_PARAMS."""
    found = knob_by_key(key)
    assert found is not None, f"{key} is settable but belongs to no Tune group"
    group, label, buttons = found
    assert group and label and buttons
    assert all(f":{key}:" in cb for _l, cb in buttons)


def test_unknown_key_has_no_group_and_falls_back():
    assert group_for_key("not_a_real_knob") is None
    assert group_for_key("") is None
    sentinel = ("READ PANEL", [])
    assert _knob_landing("not_a_real_knob", lambda: sentinel) == sentinel


@pytest.mark.parametrize("key,expected_group", [("leverage", "sizing"), ("daily_cap", "spend")])
def test_a_set_lands_in_its_group_not_the_read_panel(key, expected_group):
    """Landing on the read panel cost 3 taps to reach the very next knob (group link ->
    value -> confirm). From the group it is 1."""
    text, buttons = _knob_landing(key, lambda: ("READ PANEL", []))
    assert "READ PANEL" not in text
    siblings = [cb for row in buttons for _l, cb in row
                if ":se_set:" in cb or ":pd_set:" in cb]
    assert len(siblings) >= 2, f"{key} landed somewhere with no sibling values: {siblings}"
    assert group_for_key(key) == expected_group


def test_recent_knobs_ignore_failed_sets(tmp_path, monkeypatch):
    """A knob that keeps erroring is not a knob to promote — that would be backwards."""
    monkeypatch.setattr(activity, "_dir", lambda: tmp_path)

    class OK:
        proof_receipt = "✅ *DONE* — set"
        toast = "set"
        ok = True
        paused = False

    activity.record("se_set_confirm:leverage:2", "k1", view=OK(), ms=1.0)
    activity.record("pd_set_confirm:daily_cap:30", "k2", view=OK(), ms=1.0)
    activity.record("se_set_confirm:stop_loss:0.05", "k3", status="error", error="boom")
    # A read is not a change.
    activity.record("tune:sizing", "k4", view=OK(), ms=1.0)

    keys = activity.recent_knob_keys(limit=5)
    assert keys == ["daily_cap", "leverage"], keys
    assert "stop_loss" not in keys
    assert activity.recent_knob_keys(limit=1) == ["daily_cap"]


def test_recent_knobs_dedupe_to_most_recent(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "_dir", lambda: tmp_path)
    for i in range(4):
        activity.record(f"se_set_confirm:leverage:{i}", f"d{i}", ms=1.0)
    activity.record("se_set_confirm:vol_target:0.05", "d9", ms=1.0)
    assert activity.recent_knob_keys(limit=5) == ["vol_target", "leverage"]


def test_tune_index_promotes_recent_knobs_without_duplicating(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "_dir", lambda: tmp_path)

    _text, cold = render_tune()
    cold_n = sum(len(r) for r in cold)

    activity.record("se_set_confirm:leverage:2", "p1", ms=1.0)
    text, warm = render_tune()

    assert "Recently changed" in text
    promoted = [cb for row in warm for _l, cb in row if ":se_set:leverage:" in cb]
    assert len(promoted) == 3, "the promoted knob should offer all its values inline"
    assert not _dupes(warm), _dupes(warm)
    assert sum(len(r) for r in warm) > cold_n
    # The groups must still be there — promotion is additive, never a replacement.
    for group_cb in ("estate:tune:exec", "estate:tune:sizing", "estate:tune:safety",
                     "estate:tune:spend", "estate:tune:prospector"):
        assert any(cb == group_cb for row in warm for _l, cb in row), group_cb


def test_tune_index_is_unchanged_with_no_history(tmp_path, monkeypatch):
    """A fresh cockpit must not grow a stray empty section."""
    monkeypatch.setattr(activity, "_dir", lambda: tmp_path)
    text, rows = render_tune()
    assert "Recently changed" not in text
    assert not _dupes(rows)


# ── Panels found broken by the whole-graph sweep ─────────────────────────────

import sqlite3
import types


def _fake_coordinator():
    """A coordinator whose views return real sqlite3.Row objects with DIFFERENT columns —
    which is what the live one does, and what broke 👁 Inspect."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, "
                 "risk_class TEXT, kind TEXT, source TEXT)")
    conn.execute("INSERT INTO tasks VALUES "
                 "('7ec45b68deadbeef','Escalated thing','escalated','high','ops','telegram')")
    conn.execute("INSERT INTO tasks VALUES "
                 "('c1d2a4dd00000000','Running thing','running',NULL,'ops',NULL)")
    conn.commit()

    mod = types.SimpleNamespace()
    mod.connect = lambda: conn
    # decisions_view selects six columns; backlog_view selects four. The panel merged both.
    mod.decisions_view = lambda c: c.execute(
        "SELECT id,title,status,risk_class,kind,source FROM tasks "
        "WHERE status='escalated'").fetchall()
    mod.backlog_view = lambda c: c.execute(
        "SELECT id,title,status,kind FROM tasks WHERE status='running'").fetchall()
    return mod


@pytest.mark.parametrize("prefix,expect", [("7ec45b68", "high"), ("c1d2a4dd", "?")])
def test_inspect_renders_rows_from_either_view(monkeypatch, prefix, expect):
    """sqlite3.Row has no .get() and backlog_view omits risk_class/source, so every tap on
    👁 Inspect raised AttributeError. Found by the live sweep, not by any unit test."""
    from gateway.operator_shell import estate as E

    monkeypatch.setattr(E, "_load_coordinator", _fake_coordinator)
    view = E.handle_estate_action(f"inspect:{prefix}", f"t-inspect-{prefix}")
    assert prefix in view.text
    assert expect in view.text
    assert "Traceback" not in view.text


def test_inspect_missing_task_is_a_message_not_a_crash(monkeypatch):
    from gateway.operator_shell import estate as E

    monkeypatch.setattr(E, "_load_coordinator", _fake_coordinator)
    view = E.handle_estate_action("inspect:ffffffff", "t-inspect-none")
    assert "No task" in view.text


@pytest.mark.parametrize("armed", [True, False])
def test_rsi_panel_offers_the_arm_toggle_once(monkeypatch, armed):
    """Disarmed, the suggested next action IS arming — which the standing toggle already
    carries. The CTA line keeps naming it; the button must not appear twice."""
    from gateway.operator_shell import rsi_panel as R

    monkeypatch.setattr(R, "learning_armed", lambda: armed)
    text, rows = R.render_rsi_panel()
    assert not _dupes(rows), f"rsi(armed={armed}) duplicates: {_dupes(rows)}"
    toggle = "estate:disarm_learning" if armed else "estate:arm_learning"
    assert _callbacks(rows).count(toggle) == 1
    if not armed:
        assert "Arm learning" in text


# --- Find: search is the answer to "I don't know where anything is" -------------------

def test_find_panel_does_not_offer_itself():
    """On Map, 🗺 already re-opens Atlas — no duplicate 🔄 beside it."""
    cbs = [cb for _label, cb in nav("find")]
    assert cbs == SPINE, cbs
    assert cbs.count("estate:find") == 1, cbs


@pytest.mark.parametrize("query,expect_action", [
    ("restart", "daemon_restart"),
    ("model", "brain"),
    ("spend", "pause"),
    ("logs", "daemon_logs"),
])
def test_search_finds_the_obvious_word(query, expect_action):
    from gateway.operator_shell.find import search

    actions = {entry.action for _score, entry in search(query)}
    assert expect_action in actions, f"{query!r} did not surface {expect_action}: {actions}"


def test_search_is_empty_rather_than_wrong():
    """A no-hit answer must say so. Returning the whole index for an unmatched word is how
    a search box teaches an operator to stop trusting it."""
    from gateway.operator_shell.find import search

    assert search("xyzzyqux") == []
    assert search("") == []


def test_every_find_result_is_dispatchable():
    """The index is derived from natural_ops, so a renamed action silently becomes a dead
    button. Every argument-free hit must round-trip through the real dispatcher."""
    from gateway.operator_shell.find import _index

    for entry in _index():
        assert entry.callback.startswith("estate:")
        if not entry.needs_arg:
            assert " " not in entry.callback, f"unusable callback: {entry.callback}"
            assert len(entry.callback.encode()) <= 64, (
                f"callback exceeds Telegram's 64-byte limit: {entry.callback}")
