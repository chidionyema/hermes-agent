"""The Brains panels are reachable through the real estate dispatcher.

test_brains_panel.py proves the renderers and the writer. It does NOT prove that
tapping a button reaches them: every callback goes through
``estate.handle_estate_action``, and a renderer that emits ``estate:brains_role:vision``
while the dispatcher has no ``brains_role`` branch is a dead button that renders
"Unknown action" — the cockpit's own documented defect class ("built and unreachable").

So this file asserts at the GATE's scope: it drives the dispatcher, not the module.

Everything that touches real estate state is fenced. ``_dispatch`` records activity,
stores idempotency keys and appends undo records to ``~/.hermes/undo.jsonl`` — all
production files. This suite patches each one; without that, running the tests would
write rows into the founder's live operator history.
"""

import json

import pytest

from hermes_cli.config import AUXILIARY_TASK_KEYS


AGENT_MODEL = "claude-sonnet-5"


class _FakeResult:
    success = True
    new_model = "claude-haiku-4-5-20251001"
    target_provider = "anthropic"
    error_message = ""
    base_url = ""


@pytest.fixture
def dispatch(tmp_path, monkeypatch):
    """The real dispatcher, with every production side effect redirected."""
    from gateway.operator_shell import activity, brains, estate, proof
    import hermes_cli.config as hc
    import hermes_cli.model_switch as ms

    state = {
        "cfg": {
            "model": {"default": AGENT_MODEL, "provider": "anthropic"},
            "auxiliary": {
                role: {"provider": "auto", "model": "", "timeout": 30}
                for role in AUXILIARY_TASK_KEYS
            },
        },
        "undo": [],
    }

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("# stand-in\n")
    monkeypatch.setattr(hc, "load_config", lambda *a, **k: json.loads(json.dumps(state["cfg"])))
    monkeypatch.setattr(hc, "save_config", lambda c: state.update(cfg=json.loads(json.dumps(c))))
    monkeypatch.setattr(hc, "get_config_path", lambda *a, **k: str(cfg_file))
    monkeypatch.setattr(brains, "_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(ms, "switch_model", lambda **_k: _FakeResult())

    # `_dispatch` loads the 2938-line coordinator before ANY branch runs and returns
    # "Estate bridge down" if that fails (estate.py:437-445). Under the per-test
    # HERMES_HOME (tests/conftest.py:360) there is no coordinator on disk, so without
    # this every action below would assert against the bridge-down card instead of the
    # panel. Same sentinel the pre-existing suite uses
    # (test_every_button_dispatches.py::_stub_coordinator) — nothing on these paths
    # calls a method on it.
    monkeypatch.setattr(estate, "_load_coordinator", lambda: object())

    # Production side effects of _dispatch itself.
    monkeypatch.setattr(activity, "record", lambda *a, **k: None)
    monkeypatch.setattr(proof, "check_idempotent", lambda *a, **k: None)
    monkeypatch.setattr(proof, "store_idempotent", lambda *a, **k: None)

    def _fake_push(action, reverse, summary):
        state["undo"].append({"action": action, "reverse": reverse, "summary": summary})
        return "tok123456789"

    monkeypatch.setattr(proof, "push_undo", _fake_push)

    def _run(action):
        return estate.handle_estate_action(action, request_id=f"test-{action}")

    _run.state = state
    return _run


def _callbacks(view):
    return [cb for row in (view.buttons or []) for _label, cb in row]


def test_brains_action_is_routed_not_unknown(dispatch):
    view = dispatch("brains")
    assert "Unknown action" not in view.text
    assert "BRAINS" in view.text


def test_brains_all_expands_to_every_role(dispatch):
    view = dispatch("brains:all")
    for role in AUXILIARY_TASK_KEYS:
        assert role in view.text, f"{role} unreachable from the expanded panel"


def test_role_picker_is_routed(dispatch):
    view = dispatch("brains_role:vision")
    assert "Unknown action" not in view.text
    assert "vision" in view.text


def test_every_button_the_brains_panel_emits_is_a_handled_action(dispatch):
    """No dead ends (Principle 6) and no dead buttons."""
    seen = set()
    for entry in ("brains:all", "brains_role:vision", "brains_role:approval"):
        view = dispatch(entry)
        for cb in _callbacks(view):
            if cb.startswith("estate:"):
                seen.add(cb[len("estate:") :])

    assert seen, "panel emitted no callbacks at all"
    for cb in sorted(seen):
        # `undo:<token>` needs a live record; everything else must render.
        if cb.startswith("undo"):
            continue
        view = dispatch(cb)
        assert "Unknown action" not in view.text, f"dead button: estate:{cb}"


def test_the_agent_model_door_leads_to_the_roles(dispatch):
    """`/agent_model` is one of the 14 live menu entries — it must reach the roles."""
    view = dispatch("agent_model")
    assert "estate:brains" in _callbacks(view)


def test_setting_a_role_through_the_dispatcher_writes_and_offers_undo(dispatch):
    view = dispatch("brains_set:vision|haiku")
    assert view.ok
    assert dispatch.state["cfg"]["auxiliary"]["vision"]["model"] == "claude-haiku-4-5-20251001"
    # The undo record was pushed with a reverse the registry understands...
    assert dispatch.state["undo"], "a state change recorded no undo"
    reverse = dispatch.state["undo"][-1]["reverse"]
    assert "aux_model" in reverse
    from gateway.operator_shell import undo_ops

    assert "aux_model" in undo_ops.known_keys()
    # ...and the operator is offered it on the same screen.
    assert any(cb.startswith("estate:undo:") for cb in _callbacks(view))


def test_a_fenced_role_refuses_through_the_dispatcher(dispatch):
    dispatch.state["cfg"]["operator_shell"] = {"role_model_allowlist": {"approval": ["opus"]}}
    view = dispatch("brains_set:approval|haiku")
    assert view.ok is False
    assert dispatch.state["cfg"]["auxiliary"]["approval"]["model"] == ""
    assert not dispatch.state["undo"], "a refused write still recorded an undo"
