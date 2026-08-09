"""P2 + L3 — the Brains panel, its fenced writer, and generalised undo.

EVERY test here runs against a temp config. `~/.hermes/config.yaml` is live estate
state and this suite writes config for a living; the repo has been burned four times
by tests mutating production stores (the audit log, the durable ledger, the founder's
cockpit home card). `_env` autouse below is the fence: it redirects load/save, the
config path, and the hermes home used for backups + the audit log. A test that forgets
it would rewrite the founder's real auxiliary block.
"""

import json
from pathlib import Path

import pytest

from hermes_cli.config import AUXILIARY_TASK_KEYS
from gateway.operator_shell import brains


AGENT_MODEL = "claude-sonnet-5"


def _base_cfg():
    return {
        "model": {"default": AGENT_MODEL, "provider": "anthropic"},
        "auxiliary": {
            role: {"provider": "auto", "model": "", "timeout": 30, "extra_body": {}}
            for role in AUXILIARY_TASK_KEYS
        },
    }


# What the real resolver returns for each picker alias. Stubbed because
# `switch_model` is a live credential check that reaches the provider registry;
# the contract under test is "brains writes whatever the resolver resolved",
# not the resolver itself.
_RESOLVES = {
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5-20251001",
    "deepseek-v4-pro": "deepseek-v4-pro",
    "MiniMax-M3": "MiniMax-M3",
}


class _FakeResult:
    def __init__(self, ok, model="", provider="", error=""):
        self.success = ok
        self.new_model = model
        self.target_provider = provider
        self.error_message = error
        self.base_url = ""


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    """Redirect every path and every read/write away from the live estate."""
    state = {"cfg": _base_cfg(), "resolver_ok": True}
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("# stand-in for the real config\n")

    import hermes_cli.config as hc
    import hermes_cli.model_switch as ms

    monkeypatch.setattr(hc, "load_config", lambda *a, **k: json.loads(json.dumps(state["cfg"])))
    monkeypatch.setattr(hc, "save_config", lambda cfg: state.update(cfg=json.loads(json.dumps(cfg))))
    monkeypatch.setattr(hc, "get_config_path", lambda *a, **k: str(cfg_file))
    monkeypatch.setattr(brains, "_hermes_home", lambda: tmp_path)

    def _fake_switch(raw_input="", explicit_provider="", **_kw):
        if not state["resolver_ok"]:
            return _FakeResult(False, error="no credential for that model")
        return _FakeResult(
            True, _RESOLVES.get(raw_input, raw_input), explicit_provider or "anthropic"
        )

    monkeypatch.setattr(ms, "switch_model", _fake_switch)
    return state


def _audit_rows(tmp_path):
    p = Path(tmp_path) / "meta" / "config_audit.jsonl"
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# render — state before verb (Principle 3)
# ---------------------------------------------------------------------------

def test_panel_renders_every_role_when_expanded():
    text, _buttons = brains.render_brains_panel(show_all=True)
    for role in AUXILIARY_TASK_KEYS:
        assert role in text, f"role {role} missing from the expanded panel"


def test_preview_collapses_behind_an_explicit_expander_not_silent_truncation():
    text, buttons = brains.render_brains_panel(show_all=False)
    flat = [cb for row in buttons for _label, cb in row]
    assert "estate:brains:all" in flat, "no expander button — the list just stops"
    assert "more" in text, "collapsed list does not say that it is collapsed"


def test_inheriting_role_renders_the_inherit_target_not_an_empty_string():
    """R5: `model: ''` is not information the operator can act on."""
    text, _ = brains.render_brains_panel(show_all=True)
    assert f"auto → {AGENT_MODEL}" in text
    assert "→ ''" not in text and "→  " not in text


def test_header_counts_overridden_versus_inheriting(_env):
    _env["cfg"]["auxiliary"]["vision"] = {"provider": "anthropic", "model": "haiku"}
    text, _ = brains.render_brains_panel(show_all=True)
    n = len(AUXILIARY_TASK_KEYS)
    assert f"({n} · 1 overridden, {n - 1} inheriting)" in text


def test_panel_states_when_a_change_takes_effect():
    """Principle 5, honest effect. R1: next dispatch, never in-flight."""
    text, _ = brains.render_brains_panel(show_all=True)
    assert "NEXT dispatch" in text


# ---------------------------------------------------------------------------
# the writer
# ---------------------------------------------------------------------------

def test_set_role_model_writes_only_the_routing_fields(_env):
    ok, detail, reverse = brains.set_role_model("vision", "haiku")
    assert ok, detail
    block = _env["cfg"]["auxiliary"]["vision"]
    assert block["provider"] == "anthropic"
    assert block["model"] == "claude-haiku-4-5-20251001"
    # The role's own tuning must survive a model switch.
    assert block["timeout"] == 30, "a picker rewrote hand-tuned config it does not own"
    assert reverse == {"aux_model": {"role": "vision", "provider": "auto", "model": ""}}


def test_set_role_model_takes_a_timestamped_backup(tmp_path):
    ok, _detail, _rev = brains.set_role_model("vision", "haiku")
    assert ok
    backups = list((tmp_path / "meta" / "config_backups").glob("config.yaml.*.bak"))
    assert len(backups) == 1, f"expected exactly one backup, got {backups}"


def test_set_role_model_writes_an_audit_row_with_old_and_new(tmp_path):
    brains.set_role_model("vision", "haiku", actor="tester")
    rows = [r for r in _audit_rows(tmp_path) if r["event"] == "role_model_set"]
    assert len(rows) == 1
    row = rows[0]
    assert row["role"] == "vision"
    assert row["actor"] == "tester"
    assert row["old"] == {"provider": "auto", "model": ""}
    assert row["new"]["model"] == "claude-haiku-4-5-20251001"
    assert row["backup"], "audit row claims no backup was taken"


def test_unknown_role_and_unknown_model_are_refused(_env):
    ok, detail, _ = brains.set_role_model("not_a_role", "haiku")
    assert not ok and "unknown role" in detail
    ok, detail, _ = brains.set_role_model("vision", "not_a_model")
    assert not ok and "unknown model" in detail
    assert _env["cfg"]["auxiliary"]["vision"]["model"] == ""


def test_a_model_the_estate_cannot_reach_is_refused_before_the_write(_env):
    """The resolver doubles as the credential check (brain.set_model does the same).

    Without this, tapping an unreachable model writes a role that fails on its next
    dispatch — a control that reports success and breaks something later.
    """
    _env["resolver_ok"] = False
    ok, detail, reverse = brains.set_role_model("vision", "opus")
    assert not ok and reverse is None
    assert "no credential" in detail
    assert _env["cfg"]["auxiliary"]["vision"]["model"] == ""


def test_the_written_model_is_the_resolved_id_not_the_picker_alias(_env):
    """`Choice.alias` is `haiku`; config must carry what the agent scope carries."""
    brains.set_role_model("vision", "haiku")
    assert _env["cfg"]["auxiliary"]["vision"]["model"] == "claude-haiku-4-5-20251001"


def test_no_write_when_the_value_is_unchanged(tmp_path):
    ok, detail, reverse = brains.set_role_model("vision", "auto")
    assert ok and reverse is None and "no write" in detail
    assert not (tmp_path / "meta" / "config_backups").exists() or not list(
        (tmp_path / "meta" / "config_backups").glob("*.bak")
    )


# ---------------------------------------------------------------------------
# R2 — the fence is in the WRITER, not the keyboard
# ---------------------------------------------------------------------------

def test_allowlist_refuses_in_the_writer_even_when_the_keyboard_never_offered_it(_env, tmp_path):
    """A keyboard that omits an option is not a fence: the callback can be replayed."""
    _env["cfg"]["operator_shell"] = {"role_model_allowlist": {"approval": ["opus"]}}

    # The picker does not render haiku for `approval`...
    _text, buttons = brains.render_role_picker("approval")
    flat = [cb for row in buttons for _l, cb in row]
    assert not any("|haiku" in cb for cb in flat)

    # ...and replaying it anyway is still refused, by the writer.
    ok, detail, _ = brains.set_role_model("approval", "haiku")
    assert not ok
    assert "fenced" in detail and "haiku" in detail
    assert _env["cfg"]["auxiliary"]["approval"]["model"] == ""

    rows = [r for r in _audit_rows(tmp_path) if r["event"] == "role_model_refused"]
    assert len(rows) == 1 and rows[0]["requested"] == "haiku"


def test_allowlist_permits_a_listed_model(_env):
    _env["cfg"]["operator_shell"] = {"role_model_allowlist": {"approval": ["opus"]}}
    ok, detail, _ = brains.set_role_model("approval", "opus")
    assert ok, detail
    assert _env["cfg"]["auxiliary"]["approval"]["model"] == "claude-opus-5"


def test_reverting_to_inherit_is_always_permitted(_env):
    _env["cfg"]["operator_shell"] = {"role_model_allowlist": {"approval": ["opus"]}}
    _env["cfg"]["auxiliary"]["approval"] = {"provider": "anthropic", "model": "claude-opus-5"}
    ok, detail, _ = brains.set_role_model("approval", "auto")
    assert ok, detail
    assert _env["cfg"]["auxiliary"]["approval"]["provider"] == "auto"


def test_sensitive_role_routes_through_a_confirm_step():
    _text, buttons = brains.render_role_picker("approval")
    flat = [cb for row in buttons for _l, cb in row]
    assert any(cb.startswith("estate:brains_confirm:approval|") for cb in flat)
    assert not any(cb.startswith("estate:brains_set:approval|") for cb in flat)


def test_non_sensitive_role_changes_in_one_tap():
    """Spec metric: change one role's model = 2 taps from the door."""
    _text, buttons = brains.render_role_picker("vision")
    flat = [cb for row in buttons for _l, cb in row]
    assert any(cb.startswith("estate:brains_set:vision|") for cb in flat)


def test_confirm_screen_leads_to_the_real_setter():
    _text, buttons = brains.render_confirm("approval", "haiku")
    flat = [cb for row in buttons for _l, cb in row]
    assert "estate:brains_set:approval|haiku" in flat
    assert "estate:brains_role:approval" in flat  # cancel returns, never dead-ends


# ---------------------------------------------------------------------------
# reset + L3 undo
# ---------------------------------------------------------------------------

def test_reset_all_clears_overrides_and_is_reversible(_env):
    brains.set_role_model("vision", "haiku")
    brains.set_role_model("curator", "opus")
    ok, detail, reverse = brains.reset_all_roles()
    assert ok and "2 role(s)" in detail
    assert all(e["overridden"] is False for e in brains.role_state())

    brains.restore_role_table(reverse["aux_table"])
    after = {e["role"]: e["model"] for e in brains.role_state()}
    assert after["vision"] == "claude-haiku-4-5-20251001"
    assert after["curator"] == "claude-opus-5"


def test_undo_registry_reverses_a_role_change(_env):
    from gateway.operator_shell import undo_ops

    _ok, _detail, reverse = brains.set_role_model("vision", "haiku")
    assert _env["cfg"]["auxiliary"]["vision"]["model"] == "claude-haiku-4-5-20251001"

    undone, applied = undo_ops.apply_reverse_record(reverse)
    assert undone and applied == ["aux_model"]
    assert _env["cfg"]["auxiliary"]["vision"]["model"] == ""
    assert _env["cfg"]["auxiliary"]["vision"]["provider"] == "auto"


def test_undo_registry_reports_an_unreversible_record_instead_of_claiming_success():
    """The defect the registry replaced: an unknown reverse rendered 'Undone'."""
    from gateway.operator_shell import undo_ops

    undone, applied = undo_ops.apply_reverse_record({"something_nobody_registered": 1})
    assert undone is False and applied == []


def test_undo_registry_still_knows_the_two_original_families():
    from gateway.operator_shell import undo_ops

    assert "set_paused" in undo_ops.known_keys()
    assert "aux_model" in undo_ops.known_keys()
    assert "aux_table" in undo_ops.known_keys()
