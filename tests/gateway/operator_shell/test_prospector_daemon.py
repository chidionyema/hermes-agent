"""Prospector daemon panel tests.

This module shipped untested while holding two dangerous powers: it rewrites
~/Library/LaunchAgents plists in place, and it runs `launchctl bootout`. The tests
below pin the guards around both — allowlists, "not installed" refusals, and the
kind-aware handling that stops a 15-minute oneshot from being reported as a crash.

Nothing here touches the real ~/Documents/code/prospector or the real launchd domain.
"""

from __future__ import annotations

import plistlib

import pytest

from gateway.operator_shell import prospector_daemon as PD


# Shaped like the REAL config.yaml, prose and all. The previous sample declared each knob
# exactly once and nowhere else, so `text.count("batch_size:") == 1` passed and the suite
# stayed green while the shipped setter was rewriting a COMMENT in production: the real
# config.yaml documents its own knobs in assignment form (`# \x60batch_size: 15\x60 mints up to
# 15 rows per tick`, line 1296) hundreds of lines above the `schedule:` mapping on line 1350,
# so a whole-file regex with count=1 never reached the assignment. A fixture that cannot
# contain the defect cannot witness the fix.
CONFIG_SAMPLE = """\
# `batch_size: 15` mints up to 15 rows per tick; prose that quotes the knob.
# `backlog_cap: 100` — the stock-based brake, superseded and documented here.
# `gate_generation_on_grounding: true` — the replacement, a rate not a stock.
schedule: { cadence: daily, batch_size: 5, backlog_cap: 0, max_resume_attempts: 5,
            gate_generation_on_grounding: true }

spend:
  # meter flat before and after, none of it visible to spend.daily_cap_usd.
  daily_cap_usd: 20.0
  alarm: true
"""


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    plist_dir = tmp_path / "LaunchAgents"
    plist_dir.mkdir()
    store = tmp_path / "store" / "scheduler"
    store.mkdir(parents=True)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_SAMPLE, encoding="utf-8")

    sched_plist = plist_dir / "com.prospector.scheduler.plist"
    with sched_plist.open("wb") as f:
        plistlib.dump(
            {
                "Label": "com.prospector.scheduler",
                "ProgramArguments": ["/usr/bin/python3", "-m", "gen", "--daemon", "--interval", "7200"],
                "EnvironmentVariables": {"PROSPECTOR_CLAUDE_CONCURRENCY": "4"},
                "KeepAlive": True,
            },
            f,
            sort_keys=False,
        )

    monkeypatch.setattr(PD, "REPO", tmp_path)
    monkeypatch.setattr(PD, "PLIST_DIR", plist_dir)
    monkeypatch.setattr(PD, "STORE", store)
    monkeypatch.setattr(PD, "_SCHED_PLIST", sched_plist)
    monkeypatch.setattr(PD, "_CONFIG", cfg)
    monkeypatch.setattr(PD, "_PAUSE", store / "PAUSE")
    return tmp_path


# ── unit resolution ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "arg,expected",
    [
        ("", "com.prospector.scheduler"),
        ("scheduler", "com.prospector.scheduler"),
        ("sched", "com.prospector.scheduler"),
        ("daemon", "com.prospector.scheduler"),
        ("gen", "com.prospector.scheduler"),
        ("com.prospector.scheduler", "com.prospector.scheduler"),
        ("watchdog", "com.prospector.watchdog"),
        ("watch", "com.prospector.watchdog"),
        ("ui", "com.prospector.control-center"),
        ("cc", "com.prospector.control-center"),
    ],
)
def test_resolve_unit_aliases(arg, expected):
    assert PD.resolve_unit(arg) == expected


def test_resolve_unit_returns_none_for_unknown():
    """An unknown alias must not silently fall back to the generation daemon."""
    assert PD.resolve_unit("postgres") is None
    assert PD.resolve_unit("../../etc/passwd") is None


# ── launchd state ───────────────────────────────────────────────────────────


def test_launchctl_state_not_installed_never_shells_out(repo, monkeypatch):
    calls = []
    monkeypatch.setattr(PD.subprocess, "run", lambda *a, **k: calls.append(a))
    st = PD.launchctl_state("com.prospector.watchdog")
    assert st["state"] == "not_installed"
    assert st["installed"] is False
    assert calls == []


def _fake_run(monkeypatch, stdout, returncode=0):
    class R:
        pass

    def run(cmd, **kw):
        r = R()
        r.stdout = stdout
        r.stderr = ""
        r.returncode = returncode
        return r

    monkeypatch.setattr(PD.subprocess, "run", run)


def test_interval_oneshot_is_armed_not_dead_between_ticks(repo, monkeypatch):
    """The UX lie this module's docstring calls out: a 15-min oneshot shown as 🔴."""
    (PD.PLIST_DIR / "com.prospector.watchdog.plist").write_text("<plist/>", encoding="utf-8")
    _fake_run(monkeypatch, "\tstate = not running\n\tlast exit code = 0\n\truns = 42\n")
    st = PD.launchctl_state("com.prospector.watchdog")
    assert st["kind"] == "interval"
    assert st["armed"] is True
    assert PD._emoji(st) == "🟢"
    assert "armed" in st["detail"]


def test_keepalive_daemon_down_is_red(repo, monkeypatch):
    _fake_run(monkeypatch, "\tstate = not running\n\tlast exit code = 1\n")
    st = PD.launchctl_state("com.prospector.scheduler")
    assert st["running"] is False
    assert PD._emoji(st) == "🔴"


def test_unloaded_state_is_distinct_from_missing(repo, monkeypatch):
    _fake_run(monkeypatch, "Could not find service", returncode=113)
    st = PD.launchctl_state("com.prospector.scheduler")
    assert st["state"] == "unloaded"
    assert st["installed"] is True
    assert PD._emoji(st) == "⚪"


# ── params ──────────────────────────────────────────────────────────────────


def test_read_params_reads_plist_and_config(repo):
    p = PD.read_params()
    assert p["interval_s"] == 7200
    assert p["concurrency"] == 4
    assert p["batch_size"] == 5
    assert p["daily_cap_usd"] == 20.0
    assert p["backlog_cap"] == 0
    assert p["grounding_gate"] is True
    assert p["paused"] is False


@pytest.mark.parametrize(
    "key,value",
    [
        ("interval", "1"),          # off the allowlist — a 1s tick would hammer the API
        ("concurrency", "64"),
        ("batch_size", "500"),
        ("daily_cap", "9999"),
        ("backlog_cap", "99999"),
        ("grounding_gate", "maybe"),
        ("api_key", "sk-live"),     # not a knob at all
    ],
)
def test_set_param_rejects_anything_off_the_allowlist(repo, key, value):
    before_plist = PD._SCHED_PLIST.read_bytes()
    before_cfg = PD._CONFIG.read_bytes()
    ok, detail, restart = PD.set_param(key, value)
    assert ok is False and restart is False
    assert PD._SCHED_PLIST.read_bytes() == before_plist
    assert PD._CONFIG.read_bytes() == before_cfg


def test_set_interval_rewrites_the_plist_argument_in_place(repo):
    ok, detail, needs_restart = PD.set_param("interval", "3600")
    assert (ok, needs_restart) == (True, True)
    data = plistlib.loads(PD._SCHED_PLIST.read_bytes())
    args = data["ProgramArguments"]
    assert args[args.index("--interval") + 1] == "3600"
    # Everything else in the plist must survive a phone tap.
    assert data["Label"] == "com.prospector.scheduler"
    assert data["KeepAlive"] is True
    assert data["EnvironmentVariables"][PD._CONC_ENV] == "4"


def test_set_concurrency_rewrites_only_the_env_var(repo):
    ok, _detail, needs_restart = PD.set_param("concurrency", "8")
    assert (ok, needs_restart) == (True, True)
    data = plistlib.loads(PD._SCHED_PLIST.read_bytes())
    assert data["EnvironmentVariables"][PD._CONC_ENV] == "8"
    assert "--interval" in data["ProgramArguments"]


def test_set_batch_size_patches_the_assignment_and_not_the_prose(repo):
    """The regression. `batch_size: 15` also appears inside a comment, above the real one.

    The shipped setter used `re.subn(r"(batch_size:\\s*)\\d+", ..., count=1)` over the whole
    file, so it rewrote the COMMENT, returned `True, "batch_size → 10"`, and `read_params()`
    read the comment straight back — the panel showed 10 while the daemon ran 15. Asserting
    on the prose line explicitly is what stops that returning.
    """
    ok, _detail, needs_restart = PD.set_param("batch_size", "10")
    assert ok is True
    assert needs_restart is False  # config.yaml is re-read next tick
    text = PD._CONFIG.read_text()
    assert "batch_size: 10," in text                    # the schedule mapping moved
    assert "# `batch_size: 15` mints up to 15" in text   # the prose did NOT
    assert PD.read_params()["batch_size"] == 10
    assert "cadence: daily" in text  # neighbours untouched


def test_set_daily_cap_patches_config(repo):
    ok, _detail, _ = PD.set_param("daily_cap", "40")
    assert ok is True
    assert PD.read_params()["daily_cap_usd"] == 40.0


def test_set_backlog_cap_patches_the_assignment_and_not_the_prose(repo):
    ok, _detail, needs_restart = PD.set_param("backlog_cap", "200")
    assert (ok, needs_restart) == (True, False)
    text = PD._CONFIG.read_text()
    assert "backlog_cap: 200," in text
    assert "# `backlog_cap: 100` — the stock-based brake" in text
    assert PD.read_params()["backlog_cap"] == 200
    assert PD.read_params()["batch_size"] == 5  # the neighbour on the same line survives


def test_grounding_gate_writes_a_yaml_bool_not_the_button_word(repo):
    """The button says on/off; config.yaml must read true/false or the engine ignores it."""
    ok, detail, _ = PD.set_param("grounding_gate", "off")
    assert ok is True
    text = PD._CONFIG.read_text()
    assert "gate_generation_on_grounding: false" in text
    assert "off" not in detail.split("→")[-1]
    assert PD.read_params()["grounding_gate"] is False
    # and the prose that quotes it in assignment form is untouched
    assert "# `gate_generation_on_grounding: true` — the replacement" in text


def test_a_knob_that_is_not_uniquely_locatable_refuses_to_write(repo):
    """0 or 2 assignments means the setter cannot say which line it is about to change.

    Refusing is the whole point: writing 'one of them' is how a knob reports success and
    changes nothing the daemon reads.
    """
    PD._CONFIG.write_text(
        "schedule: { batch_size: 5 }\nother: { batch_size: 9 }\n", encoding="utf-8"
    )
    before = PD._CONFIG.read_bytes()
    ok, detail, _ = PD.set_param("batch_size", "10")
    assert ok is False
    assert "uniquely locate" in detail
    assert PD._CONFIG.read_bytes() == before
    assert PD.read_params()["batch_size"] is None  # ambiguous reads as unknown, not a guess


def test_set_interval_reports_failure_when_the_plist_lacks_the_flag(repo):
    with PD._SCHED_PLIST.open("wb") as f:
        plistlib.dump({"Label": "x", "ProgramArguments": ["/usr/bin/true"]}, f)
    ok, detail, _ = PD.set_param("interval", "3600")
    assert ok is False
    assert "no --interval" in detail


def test_set_param_reports_failure_when_the_plist_is_missing(repo):
    PD._SCHED_PLIST.unlink()
    ok, detail, _ = PD.set_param("interval", "3600")
    assert ok is False
    assert "plist missing" in detail


# ── pause file ──────────────────────────────────────────────────────────────


def test_pause_and_unpause_round_trip(repo):
    assert PD.read_params()["paused"] is False
    ok, detail = PD.set_paused(True)
    assert ok is True and PD._PAUSE.is_file()
    assert PD.read_params()["paused"] is True
    ok, detail = PD.set_paused(False)
    assert ok is True and not PD._PAUSE.exists()
    assert PD.read_params()["paused"] is False


def test_unpause_is_idempotent(repo):
    ok, detail = PD.set_paused(False)
    assert ok is True and "already unpaused" in detail


# ── operations ──────────────────────────────────────────────────────────────


def test_run_op_refuses_every_op_on_an_uninstalled_unit(repo, monkeypatch):
    """`launchctl bootout` against a unit we never installed is not ours to run."""
    calls = []
    monkeypatch.setattr(PD, "_launchctl", lambda cmd: calls.append(cmd) or (True, "ok"))
    for op in ("start", "stop", "restart", "run_now"):
        ok, detail = PD.run_op(op, "watchdog")
        assert ok is False
        assert "NOT INSTALLED" in detail
    assert calls == []


def test_run_op_rejects_an_unknown_unit_without_shelling_out(repo, monkeypatch):
    calls = []
    monkeypatch.setattr(PD, "_launchctl", lambda cmd: calls.append(cmd) or (True, "ok"))
    ok, detail = PD.run_op("stop", "postgres")
    assert ok is False and "unknown unit" in detail
    assert calls == []


def test_stop_is_reported_ok_when_the_unit_was_already_gone(repo, monkeypatch):
    monkeypatch.setattr(PD, "_launchctl", lambda cmd: (False, "No such process"))
    monkeypatch.setattr(
        PD, "launchctl_state", lambda label: {"state": "unloaded", "running": False, "detail": "x"}
    )
    ok, detail = PD.run_op("stop", "scheduler")
    assert ok is True


def test_restart_is_not_ok_when_the_process_does_not_come_back(repo, monkeypatch):
    monkeypatch.setattr(PD, "_launchctl", lambda cmd: (True, "ok"))
    monkeypatch.setattr(
        PD,
        "launchctl_state",
        lambda label: {"state": "not running", "running": False, "pid": None, "detail": "not running"},
    )
    ok, detail = PD.run_op("restart", "scheduler")
    assert ok is False


def test_interval_unit_normalises_start_to_run_now(repo, monkeypatch):
    (PD.PLIST_DIR / "com.prospector.watchdog.plist").write_text("<plist/>", encoding="utf-8")
    cmds = []

    def fake(cmd):
        cmds.append(cmd)
        return True, "ok"

    monkeypatch.setattr(PD, "_launchctl", fake)
    monkeypatch.setattr(
        PD,
        "launchctl_state",
        lambda label: {"state": "not running", "running": False, "kind": "interval", "detail": "armed"},
    )
    ok, detail = PD.run_op("start", "watchdog")
    assert any("kickstart" in " ".join(c) for c in cmds)
    assert "oneshot" in detail


# ── confirm cards ───────────────────────────────────────────────────────────


def _callbacks(rows):
    return [cb for row in rows for _lbl, cb in row]


def test_confirm_set_param_rejects_off_allowlist_values(repo):
    text, rows = PD.confirm_set_param("interval", "1")
    assert "not in allowlist" in text
    assert not any("pd_set_confirm" in cb for cb in _callbacks(rows))


def test_confirm_set_param_offers_confirm_for_allowed_values(repo):
    _text, rows = PD.confirm_set_param("interval", "3600")
    assert "estate:pd_set_confirm:interval:3600" in _callbacks(rows)


def test_confirm_card_on_missing_plist_gives_install_instructions(repo):
    text, rows = PD.confirm_card("start", "watchdog")
    assert "NOT INSTALLED" in text
    assert "launchctl bootstrap" in text
    assert not any("pd_start_confirm" in cb for cb in _callbacks(rows))


def test_confirm_card_warns_that_stopping_the_scheduler_stops_ticks(repo):
    text, _rows = PD.confirm_card("stop", "scheduler")
    assert "pause until scheduler is running again" in text


# ── rendering ───────────────────────────────────────────────────────────────


def test_render_handles_a_missing_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(PD, "REPO", tmp_path / "nope")
    text, rows = PD.render_prospector_daemon()
    assert "repo missing" in text
    assert _callbacks(rows)


def test_params_panel_never_renders_a_secret(repo):
    text, _rows = PD.render_params()
    assert "Secrets never shown" in text
    for banned in ("sk-", "token", "password", "ANTHROPIC"):
        assert banned not in text


# ── the concurrency knob must name a variable the engine reads ───────────────


def test_the_concurrency_knob_names_a_variable_the_engine_still_reads():
    """A knob that writes a variable nothing consumes is a control that lies.

    Until 2026-08-09 this wrote `PROSPECTOR_CURSOR_CONCURRENCY` into the scheduler plist and
    then restarted the daemon to apply it. cursor_cli was deleted from the engine on
    2026-08-06, so no live code had read that name since; the engine repo even carries
    `tests/unit/test_moat_resilience.py:215` asserting it stays gone. The button moved, the
    confirm screen agreed, the daemon restarted, and the engine's CLI ceiling never changed.
    """
    import inspect

    assert PD._CONC_ENV == "PROSPECTOR_CLAUDE_CONCURRENCY"
    # Comments are excluded on purpose: the constant's own comment records the dead name
    # so the next reader learns why it moved. Only executable code may still use it.
    code = "\n".join(l.split("#", 1)[0] for l in inspect.getsource(PD).splitlines())
    assert "PROSPECTOR_CURSOR_CONCURRENCY" not in code


def test_the_concurrency_confirm_screen_names_the_same_variable_it_writes(repo):
    text, _rows = PD.confirm_set_param("concurrency", "4")
    assert PD._CONC_ENV in text
