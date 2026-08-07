"""The Otto panel must report the estate, not a belief about it.

`test_every_button_dispatches.py` already proves `estate:otto` reaches this renderer and
that its buttons all resolve. What that cannot see is whether the numbers mean anything —
a panel that renders "0 stranded" because it failed to open the database looks exactly
like a healthy estate. So every test here builds a FAKE estate on disk with known
contents and asserts the panel reports those contents back.

The specific defects being gated, all of them things this file's subject matter has done
before somewhere in this repo:

- Hardcoded telemetry. `rsi_control.py:94-98` records two status lines that were
  f-strings with no placeholders — constants rendered as if live, untrue since typed.
- Copied lifecycle sets. Otto's 243 stranded tasks are stranded because `failed` is in
  neither `coordinator.ACTIVE` nor `coordinator.TERMINAL`. A panel that hardcoded those
  tuples would keep saying "stranded" after someone fixed the daemon, or (worse) stop
  saying it after someone added a status. `test_stranded_is_derived_from_coordinator_py`
  proves the parse by moving the answer.
- Epoch timestamps read as ISO. `events.created_at` is a float epoch. A subagent reading
  these as ISO reported "1785676039" as a date.
- Unbalanced markdown. Telegram legacy markdown rejects the whole message on one stray
  `_`, so a log line containing `EXECUTE_PROMPT` would turn the panel into a send failure.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest


COORDINATOR_SRC = '''
ACTIVE = ("open", "executing")
TERMINAL = ("done", "blocked")
FALLBACK_MARKERS = (
    "[executor-narrative-fallback",
    "[agentic-exec-fallback",
)
'''


def _estate(tmp_path, *, tasks=(), events=(), coordinator_src=COORDINATOR_SRC):
    """Build a minimal ~/.hermes on disk. Returns the home path."""
    home = tmp_path / "hermes"
    (home / "scripts").mkdir(parents=True)
    if coordinator_src is not None:
        (home / "scripts" / "coordinator.py").write_text(coordinator_src)

    if tasks or events:
        conn = sqlite3.connect(home / "coordinator.db")
        try:
            conn.execute(
                "create table tasks (id text, status text, result text, created_at real)"
            )
            conn.execute("create table events (kind text, created_at real)")
            conn.executemany("insert into tasks values (?,?,?,?)", tasks)
            conn.executemany("insert into events values (?,?)", events)
            conn.commit()
        finally:
            conn.close()
    return home


@pytest.fixture
def panel(monkeypatch):
    """The module with `launchctl` stubbed — the probe is a subprocess against the real
    machine, which no unit test may depend on."""
    from gateway.operator_shell import otto_panel as P

    monkeypatch.setattr(
        P, "_launchd",
        lambda: {"ai.hermes.coordinator": ("4242", "0"),
                 "ai.hermes.gateway": (None, "1")},
    )
    return P


def _use(monkeypatch, home):
    monkeypatch.setenv("HERMES_HOME", str(home))


# ── the empty estate ────────────────────────────────────────────────────────────────────


def test_an_empty_estate_renders_and_says_what_is_missing(panel, monkeypatch, tmp_path):
    """Nothing on disk must produce a panel that SAYS so. The alternative — rendering
    zeros — is the failure this whole file exists to prevent."""
    _use(monkeypatch, _estate(tmp_path, coordinator_src=None))

    text, buttons = panel.render_otto()

    assert buttons, "a panel with no buttons is a dead end"
    assert "coordinator.db not found" in text
    assert "the tuner has never run here" in text
    assert "0 landed" not in text, "no rsi log must not be reported as a measured zero"


def test_the_panel_writes_nothing(panel, monkeypatch, tmp_path):
    """`otto_health` appends a velocity row on every render (otto_health.py:220-225), so
    repeated taps grow a file. This panel must be safe to tap."""
    home = _estate(tmp_path, tasks=[("a", "done", "ok", time.time())])
    _use(monkeypatch, home)

    before = {p: p.stat().st_mtime_ns for p in sorted(home.rglob("*")) if p.is_file()}
    panel.render_otto()
    after = {p: p.stat().st_mtime_ns for p in sorted(home.rglob("*")) if p.is_file()}

    assert before == after, "render_otto mutated the estate"


# ── work ────────────────────────────────────────────────────────────────────────────────


def test_stranded_is_derived_from_coordinator_py_not_copied(panel, monkeypatch, tmp_path):
    """Move the answer and the panel must move with it.

    Same database both times. The only thing that changes is `coordinator.ACTIVE` — so a
    panel carrying its own copy of the tuple would give the same verdict twice, and this
    is the one assertion it could not pass.
    """
    now = time.time()
    home = _estate(
        tmp_path,
        tasks=[("a", "failed", "", now), ("b", "failed", "", now),
               ("c", "done", "real work", now), ("d", "executing", "", now)],
        events=[("tick", now - 300)],
    )
    _use(monkeypatch, home)

    text, _ = panel.render_otto()
    assert "STRANDED 2 (failed 2)" in text
    assert "4 tasks · 1 in flight" in text

    (home / "scripts" / "coordinator.py").write_text(
        COORDINATOR_SRC.replace('("open", "executing")', '("open", "executing", "failed")')
    )
    text, _ = panel.render_otto()
    assert "STRANDED" not in text, "the panel is holding its own copy of ACTIVE"
    assert "3 in flight" in text


def test_an_unreadable_coordinator_py_is_admitted_not_guessed(panel, monkeypatch, tmp_path):
    """With no lifecycle sets there is no such thing as a stranded status. Printing
    'STRANDED 0' would be an unearned all-clear."""
    _use(monkeypatch, _estate(
        tmp_path,
        tasks=[("a", "failed", "", time.time())],
        coordinator_src=None,
    ))

    text, _ = panel.render_otto()
    assert "could not read ACTIVE/TERMINAL" in text
    assert "STRANDED" not in text


def test_completions_are_split_by_the_codes_own_marker_constant(panel, monkeypatch, tmp_path):
    """"Done" is not "did the work". The audit's headline number — how many completions
    are narrated rather than executed — comes from `FALLBACK_MARKERS` in coordinator.py,
    and hand-written substrings gave a different count (66 vs 98) on the real database."""
    now = time.time()
    _use(monkeypatch, _estate(tmp_path, tasks=[
        ("a", "done", "ran the tests, 12 passed", now),
        ("b", "done", "[executor-narrative-fallback] I would run the tests", now),
        ("c", "done", "[agentic-exec-fallback] likewise", now),
        ("d", "blocked", "[executor-narrative-fallback] not counted, not done", now),
    ]))

    text, _ = panel.render_otto()
    assert "1 with tool work · 2 narrated" in text


def test_event_ages_are_epochs_not_iso(panel, monkeypatch, tmp_path):
    """`events.created_at` is a float epoch. Read as ISO it renders as 'never'."""
    now = time.time()
    _use(monkeypatch, _estate(
        tmp_path,
        tasks=[("a", "done", "x", now)],
        events=[("old", now - 86400), ("recent", now - 600)],
    ))

    text, _ = panel.render_otto()
    assert "last event 10m ago" in text


# ── services ────────────────────────────────────────────────────────────────────────────


def test_a_label_absent_from_launchctl_reads_as_not_loaded(panel, monkeypatch, tmp_path):
    """`ai.hermes.otto-server` prints NOTHING when unloaded — indistinguishable from
    healthy unless the panel knows the label should be there. It was unloaded for the
    whole audit and nobody could see it."""
    _use(monkeypatch, _estate(tmp_path))

    text, _ = panel.render_otto()
    assert "🟢 coordinator — pid 4242" in text
    assert "🔴 gateway — stopped (last exit 1)" in text
    assert "⚫ otto server — not loaded" in text
    assert "⚫ rsi — not loaded" in text


def test_a_failed_launchctl_is_admitted(panel, monkeypatch, tmp_path):
    _use(monkeypatch, _estate(tmp_path))
    monkeypatch.setattr(panel, "_launchd", dict)

    text, _ = panel.render_otto()
    assert "cannot judge service state" in text


# ── learning / self-improvement ─────────────────────────────────────────────────────────


def test_arm_polarity_follows_learning_switch(panel, monkeypatch, tmp_path):
    """`meta/OFF_SWITCH` PRESENT means ARMED (scripts/learning_switch.py:4-6). The name
    says the opposite, and `rsi_control.toggle_learning` writes a different file with the
    inverted meaning — which is why its pause button is quarantined."""
    home = _estate(tmp_path)
    _use(monkeypatch, home)

    text, _ = panel.render_otto()
    assert "DISARMED" in text

    (home / "meta").mkdir()
    (home / "meta" / "OFF_SWITCH").write_text("armed\n")
    text, _ = panel.render_otto()
    assert "🟢 ARMED" in text


def test_landed_improvements_counts_the_success_line(panel, monkeypatch, tmp_path):
    home = _estate(tmp_path)
    _use(monkeypatch, home)
    log = home / "logs" / "rsi-autorun.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "Attempt 1/3 generating prompt variant...\n"
        "prompt-tune(EXECUTE_PROMPT) exit=1\n"
        "Verification succeeded on attempt 2\n"
        "prompt-tune(DIAGNOSE_PROMPT) exit=0\n"
    )

    text, _ = panel.render_otto()
    assert "1 landed / 2 tune runs" in text
    assert "exit=0" in text, "the LAST verdict, not the first"


def test_a_log_line_cannot_break_telegram_markdown(panel, monkeypatch, tmp_path):
    """One unbalanced `_` and Telegram rejects the message — the panel would arrive as a
    send failure, which reads as "the button is broken"."""
    home = _estate(tmp_path)
    _use(monkeypatch, home)
    (home / "logs").mkdir(parents=True)
    (home / "logs" / "rsi-autorun.log").write_text("prompt-tune(EXECUTE_PROMPT) exit=1\n")

    text, _ = panel.render_otto()
    assert "EXECUTE_PROMPT" not in text
    assert "EXECUTEPROMPT exit=1" in text


def test_goals_that_never_measured_are_named(panel, monkeypatch, tmp_path):
    home = _estate(tmp_path)
    _use(monkeypatch, home)
    (home / "state").mkdir(parents=True)
    (home / "state" / "rsi-goals.json").write_text(json.dumps([
        {"id": "g1", "progress": "Pending first measurement"},
        {"id": "g2", "progress": "Pending first measurement"},
        {"id": "g3", "progress": "+4% on the held-out set"},
    ]))

    text, _ = panel.render_otto()
    assert "3 open · 2 never measured" in text


# ── the typed doors ─────────────────────────────────────────────────────────────────────


def test_otto_and_rsi_are_real_gateway_commands():
    """`rsi_control.py:5` claimed "Accessible via: /rsi" while no such command existed."""
    from hermes_cli.commands import is_gateway_known_command, resolve_command

    for word, canonical in (("otto", "otto"), ("autonomy", "otto"),
                            ("rsi", "rsi"), ("selfimprove", "rsi")):
        definition = resolve_command(word)
        assert definition is not None, f"/{word} does not resolve"
        assert definition.name == canonical
        assert is_gateway_known_command(word) is True


def test_the_typed_door_and_the_button_share_one_renderer():
    """Two handlers for one screen is how `/dashboard` nearly drifted. The `/otto`
    handler must import the same function `estate._PANELS` names."""
    import inspect

    from gateway.operator_shell.estate import _PANELS
    from gateway.slash_commands import GatewaySlashCommandsMixin

    module_name, func_name, _toast, arg_mode = _PANELS["otto"]
    assert (module_name, func_name) == ("otto_panel", "render_otto")
    assert arg_mode == "none"

    src = inspect.getsource(GatewaySlashCommandsMixin._handle_otto_command)
    assert f"from gateway.operator_shell.{module_name} import {func_name}" in src
