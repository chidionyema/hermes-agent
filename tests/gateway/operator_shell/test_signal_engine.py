"""Signal Engine panel tests — money rail, so the fences are what get tested.

Every test redirects the module's path globals at a tmp dir. Nothing here reads or
writes ~/Documents/code/signalengine, and nothing shells out to launchctl.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gateway.operator_shell import signal_engine as SE


CONFIG_SAMPLE = """\
risk:
  sizing: vol_target
  vol_target: 0.10                # C9: target annualised vol
  caps:
    leverage: 2
    per_instrument: 0.1
    portfolio_dd_killswitch: 0.15
    max_positions: 5                # Tier3
    stop_loss_pct: 0.10             # Tier3

llm:
  spend_budget:
    daily_cap_usd: 2
    alarm: true

execution:
  mode: internal_sim              # Literal["internal_sim","testnet","live"]
  order_timeout_sec: 30

ramp:
  stage: paper_forward

live_feed:
  enabled: true                # LIVE — real network calls
"""


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A fake signalengine repo with the module pointed at it."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_SAMPLE, encoding="utf-8")
    store = tmp_path / "data_store"
    store.mkdir()
    ctrl = store / "daemon_control.json"
    ctrl.write_text(
        json.dumps(
            {
                "command": {"action": "none", "id": "", "requested_at": ""},
                "ack": {"id": "", "action": "", "status": "", "at": "", "error": ""},
                "state": {
                    "paused": False,
                    "last_warmup_at": "",
                    "last_applied_id": "",
                    "equity": 9820.41,
                    "running": True,
                },
            }
        ),
        encoding="utf-8",
    )
    err = tmp_path / "daemon.err.log"
    err.write_text("2026-07-31T12:00:00 INFO daemon: Cycle complete.\n", encoding="utf-8")
    out = tmp_path / "daemon.out.log"
    out.write_text("", encoding="utf-8")

    monkeypatch.setattr(SE, "REPO", tmp_path)
    monkeypatch.setattr(SE, "CONFIG", cfg)
    monkeypatch.setattr(SE, "CONTROL", ctrl)
    monkeypatch.setattr(SE, "ERR_LOG", err)
    monkeypatch.setattr(SE, "OUT_LOG", out)
    monkeypatch.setattr(SE, "LOGS", (err, out))
    monkeypatch.setattr(SE, "PLIST", tmp_path / "com.signalengine.daemon.plist")
    return tmp_path


# ── params ──────────────────────────────────────────────────────────────────


def test_read_params_reads_every_allowlisted_knob(repo):
    p = SE.read_params()
    assert p == {
        "exec_mode": "internal_sim",
        "ramp_stage": "paper_forward",
        "vol_target": "0.10",
        "leverage": "2",
        "per_instrument": "0.1",
        "killswitch": "0.15",
        "max_positions": "5",
        "stop_loss": "0.10",
        "llm_cap": "2",
        "live_feed": "true",
    }


def test_read_params_exposes_only_the_allowlist(repo):
    """A knob panel that can surface an unlisted key can surface a secret."""
    assert set(SE.read_params()) == set(SE._READ_PATTERNS)
    assert set(SE._READ_PATTERNS) == set(SE._SAFE_PARAMS)


def test_set_param_rejects_unlisted_key(repo):
    ok, detail, restart = SE.set_param("api_key", "sk-live-1")
    assert (ok, restart) == (False, False)
    assert "not phone-editable" in detail
    assert "sk-live-1" not in SE.CONFIG.read_text()


@pytest.mark.parametrize(
    "key,value",
    [("leverage", "99"), ("exec_mode", "mainnet"), ("ramp_stage", "yolo"), ("live_feed", "maybe")],
)
def test_set_param_rejects_values_off_the_allowlist(repo, key, value):
    before = SE.CONFIG.read_bytes()
    ok, detail, _ = SE.set_param(key, value)
    assert ok is False
    assert "not allowed" in detail
    assert SE.CONFIG.read_bytes() == before


def test_set_param_writes_value_and_keeps_the_trailing_comment(repo):
    ok, detail, needs_restart = SE.set_param("exec_mode", "live")
    assert (ok, needs_restart) == (True, True)
    assert "`internal_sim` → `live`" in detail
    line = next(ln for ln in SE.CONFIG.read_text().splitlines() if ln.strip().startswith("mode:"))
    assert line.strip().startswith("mode: live")
    # The trailing comment documents the allowed literals — losing it on every phone
    # tap would erode config.yaml one knob at a time.
    assert '# Literal["internal_sim","testnet","live"]' in line
    assert SE.read_params()["exec_mode"] == "live"


def test_set_param_round_trip_is_byte_identical(repo):
    before = SE.CONFIG.read_bytes()
    SE.set_param("leverage", "3")
    assert SE.CONFIG.read_bytes() != before
    SE.set_param("leverage", "2")
    assert SE.CONFIG.read_bytes() == before


def test_set_param_no_op_when_value_already_set(repo):
    before = SE.CONFIG.read_bytes()
    ok, detail, needs_restart = SE.set_param("leverage", "2")
    assert ok is True
    assert needs_restart is False  # no restart for a change that did not happen
    assert "already" in detail
    assert SE.CONFIG.read_bytes() == before


def test_set_param_refuses_when_the_key_is_ambiguous(repo):
    """Two `leverage:` lines means we cannot know which one is the risk cap.

    Guessing here would silently edit the wrong block of a money config from a phone.
    """
    SE.CONFIG.write_text(SE.CONFIG.read_text() + "\nother:\n  leverage: 7\n", encoding="utf-8")
    before = SE.CONFIG.read_bytes()
    ok, detail, _ = SE.set_param("leverage", "3")
    assert ok is False
    assert "found 2" in detail
    assert SE.CONFIG.read_bytes() == before


def test_set_param_rolls_back_when_the_write_does_not_verify(repo, monkeypatch):
    before = SE.CONFIG.read_bytes()
    monkeypatch.setattr(SE, "read_params", lambda: {"leverage": "999"})
    ok, detail, _ = SE.set_param("leverage", "3")
    assert ok is False
    assert "rolled back" in detail
    assert SE.CONFIG.read_bytes() == before


def test_set_param_reports_missing_config(repo):
    SE.CONFIG.unlink()
    ok, detail, _ = SE.set_param("leverage", "3")
    assert ok is False
    assert "config.yaml missing" in detail


@pytest.mark.parametrize(
    "mode,stage,armed",
    [
        ("internal_sim", "paper_forward", False),
        ("testnet", "paper_forward", True),
        ("live", "paper_forward", True),
        ("internal_sim", "tiny_real", True),
        ("internal_sim", "scaled", True),
    ],
)
def test_is_armed_covers_both_rail_knobs(repo, mode, stage, armed):
    assert SE.is_armed({"exec_mode": mode, "ramp_stage": stage}) is armed


# ── launchd state classification ────────────────────────────────────────────


def _fake_launchctl(monkeypatch, stdout, returncode=0):
    class R:
        pass

    def fake_run(cmd, **kw):
        r = R()
        r.stdout = stdout
        r.stderr = ""
        r.returncode = returncode
        return r

    monkeypatch.setattr(SE.subprocess, "run", fake_run)


def test_launchctl_state_not_installed_when_plist_missing(repo, monkeypatch):
    called = []
    monkeypatch.setattr(SE.subprocess, "run", lambda *a, **k: called.append(a))
    st = SE.launchctl_state()
    assert st["state"] == "not_installed"
    assert called == []  # never shells out for a unit that cannot exist


def test_launchctl_state_flags_exit_78_as_tcc_denied(repo, monkeypatch):
    SE.PLIST.write_text("<plist/>", encoding="utf-8")
    _fake_launchctl(
        monkeypatch,
        "\tstate = spawn scheduled\n\tlast exit code = 78: EX_CONFIG\n\truns = 12\n",
    )
    st = SE.launchctl_state()
    assert st["state"] == "tcc_denied"
    assert st["running"] is False
    assert "Full Disk Access" in st["detail"]


def test_launchctl_state_other_nonzero_exit_is_crashing_not_tcc(repo, monkeypatch):
    SE.PLIST.write_text("<plist/>", encoding="utf-8")
    _fake_launchctl(
        monkeypatch, "\tstate = spawn scheduled\n\tlast exit code = 1\n\truns = 3\n"
    )
    st = SE.launchctl_state()
    assert st["state"] == "crashing"


def test_launchctl_state_running(repo, monkeypatch):
    SE.PLIST.write_text("<plist/>", encoding="utf-8")
    _fake_launchctl(
        monkeypatch, "\tstate = running\n\tpid = 4242\n\tlast exit code = 0\n"
    )
    st = SE.launchctl_state()
    assert st["running"] is True and st["pid"] == 4242


def test_launchctl_state_unloaded(repo, monkeypatch):
    SE.PLIST.write_text("<plist/>", encoding="utf-8")
    _fake_launchctl(monkeypatch, "Could not find service", returncode=113)
    assert SE.launchctl_state()["state"] == "unloaded"


# ── health verdicts ─────────────────────────────────────────────────────────


def _health_with(monkeypatch, launchd_state, pid, hb_age):
    monkeypatch.setattr(
        SE,
        "launchctl_state",
        lambda: {"state": launchd_state, "detail": launchd_state, "running": launchd_state == "running", "pid": pid},
    )
    monkeypatch.setattr(SE, "daemon_pid", lambda: pid)
    monkeypatch.setattr(SE, "heartbeat_age_s", lambda: hb_age)
    return SE.health()


@pytest.mark.parametrize(
    "launchd_state,pid,hb,expected",
    [
        ("running", 100, 5, "ok"),
        ("running", 100, 9999, "stalled"),
        # The exact shape of the 37-day outage: launchd owns nothing, a hand-started
        # process is alive, and every naive check calls that healthy.
        ("unloaded", 100, 5, "unsupervised"),
        ("not_installed", 100, 5, "unsupervised"),
        ("tcc_denied", None, 99999, "tcc_denied"),
        ("unloaded", None, 99999, "down"),
        ("not_installed", None, None, "not_installed"),
    ],
)
def test_health_verdict_matrix(repo, monkeypatch, launchd_state, pid, hb, expected):
    assert _health_with(monkeypatch, launchd_state, pid, hb)["verdict"] == expected


def test_health_reads_equity_and_paused_from_control_file(repo):
    h = SE.health()
    assert h["equity"] == 9820.41
    assert h["paused"] is False


def test_health_survives_a_corrupt_control_file(repo):
    SE.CONTROL.write_text("{not json", encoding="utf-8")
    h = SE.health()
    assert h["equity"] is None
    assert "_read_error" in h["control"]


# ── control-file protocol ───────────────────────────────────────────────────


def test_send_command_rejects_unknown_action(repo):
    ok, detail = SE.send_command("liquidate")
    assert ok is False and "unknown control action" in detail
    assert (json.loads(SE.CONTROL.read_text())["command"])["action"] == "none"


@pytest.mark.parametrize("action", ["pause", "resume", "restart", "reset"])
def test_send_command_writes_and_verifies(repo, action):
    ok, detail = SE.send_command(action)
    assert ok is True
    cmd = json.loads(SE.CONTROL.read_text())["command"]
    assert cmd["action"] == action
    assert cmd["id"][:8] in detail
    assert cmd["requested_at"].endswith("+00:00")


def test_send_command_preserves_the_daemons_own_state_block(repo):
    SE.send_command("pause")
    data = json.loads(SE.CONTROL.read_text())
    assert data["state"]["equity"] == 9820.41
    assert data["ack"] == {"id": "", "action": "", "status": "", "at": "", "error": ""}


def test_send_command_reports_a_clobbered_write_instead_of_claiming_success(repo, monkeypatch):
    """The daemon rewrites this file every cycle; a lost command must not read as sent."""
    original = SE._atomic_write_control

    def clobber(data):
        data = dict(data)
        data["command"] = {"action": "none", "id": "daemon-won", "requested_at": ""}
        original(data)

    monkeypatch.setattr(SE, "_atomic_write_control", clobber)
    ok, detail = SE.send_command("pause")
    assert ok is False
    assert "command lost" in detail


def test_run_op_refuses_control_commands_when_the_daemon_is_dead(repo, monkeypatch):
    monkeypatch.setattr(SE, "daemon_pid", lambda: None)
    ok, detail = SE.run_op("pause")
    assert ok is False
    assert "would never be read" in detail
    assert json.loads(SE.CONTROL.read_text())["command"]["action"] == "none"


# ── confirm / arm gating ────────────────────────────────────────────────────


def _callbacks(rows):
    return [cb for row in rows for _lbl, cb in row]


@pytest.mark.parametrize("key,value", [("exec_mode", "live"), ("ramp_stage", "tiny_real")])
def test_rail_knobs_route_through_the_arm_screen_never_straight_to_apply(repo, key, value):
    _text, rows = SE.confirm_set_param(key, value)
    cbs = _callbacks(rows)
    assert any(cb.startswith("estate:se_arm:") for cb in cbs)
    assert not any(cb.startswith("estate:se_set_confirm") for cb in cbs)


def test_ops_knob_uses_a_single_confirm(repo):
    _text, rows = SE.confirm_set_param("leverage", "3")
    assert "estate:se_set_confirm:leverage:3" in _callbacks(rows)


def test_confirm_set_param_rejects_bad_input_with_no_way_forward(repo):
    for key, val in (("bogus", "1"), ("leverage", "99")):
        _text, rows = SE.confirm_set_param(key, val)
        cbs = _callbacks(rows)
        assert not any(cb.startswith(("estate:se_set_confirm", "estate:se_arm")) for cb in cbs)


def test_arm_card_shows_live_equity_and_the_killswitch(repo):
    text, rows = SE.arm_card("exec_mode", "live")
    assert "ARM CHECK" in text
    assert "9,820.41" in text
    assert "0.15" in text  # dd killswitch
    assert "estate:se_set_confirm:exec_mode:live" in _callbacks(rows)


def test_arm_card_refuses_a_non_rail_knob(repo):
    text, rows = SE.arm_card("leverage", "3")
    assert "not a rail knob" in text
    assert not any(cb.startswith("estate:se_set_confirm") for cb in _callbacks(rows))


def test_confirm_card_refuses_start_while_tcc_denied(repo, monkeypatch):
    """Offering a Start button that provably cannot work is the 37-day-outage UX."""
    SE.PLIST.write_text("<plist/>", encoding="utf-8")
    monkeypatch.setattr(SE, "daemon_pid", lambda: None)
    monkeypatch.setattr(
        SE, "launchctl_state", lambda: {"state": "tcc_denied", "detail": "x", "running": False, "pid": None}
    )
    text, rows = SE.confirm_card("start")
    assert "will not work yet" in text
    assert "Full Disk Access" in text
    assert not any(cb.startswith("estate:se_start_confirm") for cb in _callbacks(rows))


def test_confirm_card_warns_when_the_rail_is_armed(repo):
    SE.set_param("exec_mode", "live")
    text, _rows = SE.confirm_card("stop")
    assert "ARMED" in text and "unmanaged" in text


def test_confirm_card_refuses_start_without_a_plist(repo):
    text, _rows = SE.confirm_card("start")
    assert "NOT INSTALLED" in text


# ── ops honesty ─────────────────────────────────────────────────────────────


def test_stop_reports_failure_when_a_hand_started_process_survives_bootout(repo, monkeypatch):
    SE.PLIST.write_text("<plist/>", encoding="utf-8")
    monkeypatch.setattr(SE, "_launchctl", lambda cmd: (True, "ok"))
    monkeypatch.setattr(SE, "daemon_pid", lambda: 4242)
    monkeypatch.setattr(
        SE, "launchctl_state", lambda: {"state": "unloaded", "detail": "x", "running": False, "pid": None}
    )
    monkeypatch.setattr(SE, "heartbeat_age_s", lambda: 5)
    ok, detail = SE.run_op("stop")
    assert ok is False
    assert "still alive" in detail


def test_start_kills_an_unsupervised_copy_before_launchd_starts_its_own(repo, monkeypatch):
    """Two daemons on one book is worse than none. The old one goes first."""
    SE.PLIST.write_text("<plist/>", encoding="utf-8")
    killed = []
    monkeypatch.setattr(SE.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(SE, "_launchctl", lambda cmd: (True, "ok"))
    states = iter(["unsupervised", "ok"])
    monkeypatch.setattr(SE, "daemon_pid", lambda: 4242)
    monkeypatch.setattr(SE, "heartbeat_age_s", lambda: 5)
    monkeypatch.setattr(
        SE,
        "launchctl_state",
        lambda: {"state": "unloaded" if next(states, "running") == "unsupervised" else "running",
                 "detail": "x", "running": False, "pid": None},
    )
    ok, detail = SE.run_op("start")
    assert killed and killed[0][1] == 15
    assert "stopped unsupervised pid 4242" in detail


def test_run_op_refuses_launchd_ops_without_a_plist(repo, monkeypatch):
    calls = []
    monkeypatch.setattr(SE, "_launchctl", lambda cmd: calls.append(cmd) or (True, "ok"))
    for op in ("start", "stop", "restart"):
        ok, detail = SE.run_op(op)
        assert ok is False and "NOT INSTALLED" in detail
    assert calls == []


def test_run_op_unknown_op(repo):
    SE.PLIST.write_text("<plist/>", encoding="utf-8")
    ok, detail = SE.run_op("liquidate")
    assert ok is False and "unknown op" in detail


# ── rendering ───────────────────────────────────────────────────────────────


def test_render_names_the_tcc_fix_path_when_blocked(repo, monkeypatch):
    monkeypatch.setattr(SE, "daemon_pid", lambda: None)
    monkeypatch.setattr(SE, "heartbeat_age_s", lambda: 99999)
    monkeypatch.setattr(
        SE, "launchctl_state", lambda: {"state": "tcc_denied", "detail": "x", "running": False, "pid": None}
    )
    text, _rows = SE.render_signal_engine()
    assert "Full Disk Access" in text
    assert SE._TCC_HINT_PATH in text


def test_render_flags_an_unsupervised_process_rather_than_calling_it_healthy(repo, monkeypatch):
    monkeypatch.setattr(SE, "daemon_pid", lambda: 4242)
    monkeypatch.setattr(SE, "heartbeat_age_s", lambda: 5)
    monkeypatch.setattr(
        SE, "launchctl_state", lambda: {"state": "unloaded", "detail": "x", "running": False, "pid": None}
    )
    text, rows = SE.render_signal_engine()
    assert "launchd does NOT own it" in text
    assert "estate:se_start" in _callbacks(rows)


def test_render_params_marks_the_rail_armed(repo):
    SE.set_param("exec_mode", "live")
    text, _rows = SE.render_params()
    assert "ARMED" in text


def test_render_params_says_paper_when_paper(repo):
    text, _rows = SE.render_params()
    assert "Paper" in text and "ARMED" not in text


def test_glance_line_accepts_precomputed_health(repo, monkeypatch):
    monkeypatch.setattr(SE, "health", lambda: pytest.fail("should not re-probe"))
    line = SE.glance_line(
        {"verdict": "ok", "equity": 1234.5, "heartbeat_s": 10, "paused": False, "pid": 1}
    )
    assert "$1,234" in line


def test_render_handles_a_missing_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(SE, "REPO", tmp_path / "nope")
    text, rows = SE.render_signal_engine()
    assert "repo missing" in text
    assert _callbacks(rows)


def test_logs_panel_reports_a_missing_log_instead_of_blank(repo):
    SE.ERR_LOG.unlink()
    text, _rows = SE.render_logs()
    assert "(missing)" in text
