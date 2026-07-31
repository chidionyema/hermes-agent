"""Operator shell unit tests — mission, inbox, natural ops, proof, menu."""

from __future__ import annotations

from gateway.operator_shell.cron_ops import format_cron_command
from gateway.operator_shell.menu import (
    OPERATOR_TELEGRAM_MENU,
    filter_operator_menu,
    resolve_telegram_menu_profile,
)
from gateway.operator_shell.natural_ops import match_natural_op
from gateway.operator_shell.proof import (
    check_idempotent,
    new_request_id,
    push_undo,
    pop_undo,
    store_idempotent,
)
from gateway.operator_shell.voice_brief import wants_executive_brief


def test_operator_menu_is_twelve_or_fewer():
    assert len(OPERATOR_TELEGRAM_MENU) <= 12
    assert "panel" in OPERATOR_TELEGRAM_MENU
    assert "cron" in OPERATOR_TELEGRAM_MENU


def test_filter_operator_menu_uses_tier0_order_not_input_order():
    # filter_operator_menu emits OPERATOR_TELEGRAM_MENU order (menu.py:49),
    # not the caller's order, and drops anything not Tier-0 ("zzz", "new").
    cmds = [("zzz", "Z"), ("panel", "Panel"), ("help", "Help"), ("cron", "Cron"), ("new", "New")]
    assert [n for n, _ in filter_operator_menu(cmds)] == ["panel", "cron", "help"]


def test_filter_operator_menu_drops_non_tier0():
    assert filter_operator_menu([("zzz", "Z"), ("new", "New")]) == []


def test_resolve_menu_profile_operator():
    assert resolve_telegram_menu_profile({"operator_shell": {"menu_profile": "operator"}}) == "operator"
    assert resolve_telegram_menu_profile({}) == "default"


def test_cron_help_mentions_list(monkeypatch):
    monkeypatch.setattr(
        "gateway.operator_shell.cron_ops._cron_api",
        lambda **kwargs: {"success": True, "jobs": []},
    )
    text = format_cron_command("")
    assert "/cron list" in text


def test_cron_pause_formats(monkeypatch):
    def fake_api(**kwargs):
        assert kwargs.get("action") == "pause"
        return {"success": True, "job": {"name": "morning-brief"}}

    monkeypatch.setattr("gateway.operator_shell.cron_ops._cron_api", fake_api)
    assert "Paused" in format_cron_command("pause abc123")


def test_panel_fail_closed_without_coordinator(monkeypatch, tmp_path):
    from gateway.operator_shell import estate as estate_mod

    monkeypatch.setattr(estate_mod, "_hermes_home", lambda: tmp_path)
    estate_mod._COORD_CACHE = None
    estate_mod._COORD_ERROR = None
    view = estate_mod.render_panel_view()
    assert view.ok is False


def test_natural_ops_pause_spend():
    op = match_natural_op("pause spend")
    assert op is not None and op.action == "pause"
    assert match_natural_op("please rewrite the entire prospector pipeline") is None


def test_natural_ops_host_keep_awake():
    for phrase in ("host", "keep awake", "estate online", "Mac awake?"):
        op = match_natural_op(phrase)
        assert op is not None and op.action == "host", phrase
    op = match_natural_op("start keep awake")
    assert op is not None and op.action == "host_keepawake_start"


def test_host_glance_line_shape(monkeypatch):
    from gateway.operator_shell import host as host_mod

    monkeypatch.setattr(
        host_mod,
        "probe_host",
        lambda: {
            "line": "🖥 Host: AWAKE · online",
            "status": "awake",
            "at_risk": False,
        },
    )
    assert "AWAKE" in host_mod.glance_line()


def test_host_wake_grace_suppresses_stale_alarm(monkeypatch):
    from gateway.operator_shell import host as host_mod

    monkeypatch.setattr(
        host_mod,
        "_keepawake_running",
        lambda: {"running": True, "pid": 1, "state": "running", "detail": "pid 1", "installed": True},
    )
    monkeypatch.setattr(host_mod, "_gateway_heartbeat_age", lambda: 2000)
    monkeypatch.setattr(
        host_mod,
        "_watchdog_meta",
        lambda: {"last_run_age": 60, "in_wake_grace": True, "wake_age": 120},
    )
    monkeypatch.setattr(host_mod, "_load_uptime", lambda: {"load": (1.0, 1.0, 1.0), "uptime_s": 1000})
    monkeypatch.setattr(host_mod, "_net_ok", lambda: True)
    monkeypatch.setattr(host_mod, "_pmset_sleep", lambda: "0")
    p = host_mod.probe_host()
    assert p["at_risk"] is False
    assert p["status"] in ("waking", "awake", "degraded")
    assert "grace" in p["line"].lower() or p["status"] == "waking"


def test_voice_brief_triggers():
    assert wants_executive_brief("status", from_voice=False)
    assert wants_executive_brief("how are we doing", from_voice=True)
    assert not wants_executive_brief("implement a new auth system please", from_voice=True)


def test_idempotent_callbacks(tmp_path, monkeypatch):
    from gateway.operator_shell import proof as proof_mod

    monkeypatch.setattr(proof_mod, "_hermes_home", lambda: tmp_path)
    rid = new_request_id()
    assert check_idempotent(rid) is None
    store_idempotent(rid, {"text": "ok", "buttons": []})
    assert check_idempotent(rid)["text"] == "ok"


def test_undo_stack(tmp_path, monkeypatch):
    from gateway.operator_shell import proof as proof_mod

    monkeypatch.setattr(proof_mod, "_hermes_home", lambda: tmp_path)
    token = push_undo("pause", {"set_paused": False}, "paused spend")
    rec = pop_undo(token[:4])
    assert rec is not None and rec["action"] == "pause"
