"""Atlas Rooms — empty Map is a map; Code is an SDLC pipeline; rooms are Look-only."""

from __future__ import annotations

from gateway.operator_shell.atlas import render_atlas, render_code_prompt, render_room
from gateway.operator_shell.find import render_find
from gateway.operator_shell.fleet import render_fleet
from gateway.operator_shell.panel_chrome import nav


def test_empty_find_is_map_atlas():
    text, rows = render_find("")
    assert "Map" in text
    flat = {cb for row in rows for _l, cb in row}
    assert "estate:room:money" in flat
    assert "estate:room:code" in flat
    assert "estate:room:machine" in flat
    assert "estate:room:brain" in flat
    assert "estate:brief" in flat


def test_spine_map_glyph():
    labels = [l for l, _cb in nav()]
    assert any("🗺" in l for l in labels)
    assert all("🔎" not in l for l in labels)


def test_typed_find_still_searches():
    text, _rows = render_find("restart")
    assert "restart" in text.lower() or "Restart" in text or "match" in text.lower()


def test_code_room_exposes_sdlc_stages_and_assign():
    text, rows = render_room("code", probes=False)
    assert "SDLC" in text
    assert "Assign" in text and "Ship" in text
    flat = {cb for row in rows for _l, cb in row}
    assert "estate:code_prompt" in flat
    for need in (
        "estate:missions",
        "estate:fleet",
        "estate:diff",
        "estate:builds",
        "estate:st_status",
        "estate:rsi",
    ):
        assert need in flat, f"Code room missing {need}"


def test_rooms_are_look_only_no_act_verbs():
    for rid in ("money", "machine", "brain"):
        _text, rows = render_room(rid, probes=False)
        flat = {cb for row in rows for _l, cb in row}
        for verb in (
            "estate:pause",
            "estate:resume",
            "estate:se_start_now",
            "estate:daemon_restart_now:gateway",
            "estate:arm_learning",
            "estate:disarm_learning",
        ):
            assert verb not in flat, f"{rid} room still has Act verb {verb}"


def test_money_room_exposes_store_deep_doors():
    _text, rows = render_room("money", probes=False)
    flat = {cb for row in rows for _l, cb in row}
    assert "estate:st_status" in flat
    assert "estate:st_health" in flat
    assert "estate:st_reconcile" in flat


def test_fleet_is_repos_not_daemon_mall():
    _text, rows = render_fleet()
    flat = {cb for row in rows for _l, cb in row}
    for mall in (
        "estate:signal_engine",
        "estate:se_params",
        "estate:prospector_daemon",
        "estate:daemons",
        "estate:st_status",
        "estate:run_prospector",
    ):
        assert mall not in flat, f"Fleet mall remnant {mall}"
    assert "estate:builds" in flat or "estate:room:code" in flat


def test_code_prompt_teaches_cc():
    text, rows = render_code_prompt()
    assert "cc" in text
    flat = {cb for row in rows for _l, cb in row}
    assert "estate:room:code" in flat


def test_unknown_room_falls_back_to_atlas():
    text, rows = render_room("nope")
    assert "Map" in text
    assert any("estate:room:code" in cb for row in rows for _l, cb in row)


def test_atlas_render_matches_empty_find():
    a, ar = render_atlas()
    b, br = render_find(None)
    assert a == b
    assert ar == br
