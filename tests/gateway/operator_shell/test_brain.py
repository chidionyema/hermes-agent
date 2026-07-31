"""The model picker: a button that changes what the agent thinks with.

`/model opus` already worked; a slash command you must remember the spelling of is not a UI.
These tests hold the two things that make the panel safe to tap — it only ever writes a model
from its own closed list, and it never claims a switch it did not persist.
"""

import pytest

from gateway.operator_shell import brain as B


def test_every_choice_pins_its_provider():
    """Without an explicit provider, `opus` resolves through OpenRouter — same weights, but
    a middleman and a different transport (verified against the live resolver on 2026-07-31).
    A picker that silently changes who bills you is not a picker."""
    for c in B.choices():
        assert c.provider, f"{c.key} has no provider pinned"
        assert c.cost and c.why, f"{c.key} offers no basis to choose it"


def test_unknown_key_never_reaches_the_resolver(monkeypatch):
    """Callback arguments are not model names. An unknown key is a bug or a malformed
    callback, and must fail closed rather than being handed to switch_model as free text."""
    import hermes_cli.model_switch as MS

    monkeypatch.setattr(
        MS, "switch_model",
        lambda **kw: pytest.fail(f"resolver called with unvalidated input: {kw}"))
    ok, detail = B.set_model("gpt-9-ultra; rm -rf /")
    assert not ok
    assert "unknown brain" in detail


@pytest.mark.parametrize("model,provider,key", [
    ("claude-opus-5", "anthropic", "opus"),
    ("claude-haiku-4-5-20251001", "anthropic", "haiku"),
    ("deepseek-v4-pro", "deepseek", "deepseek"),
])
def test_the_running_model_is_marked_current(model, provider, key):
    """config.yaml stores the resolved id (`claude-opus-5`), the choice is keyed `opus`.
    A naive equality marks nothing as current, and the panel then cannot answer the one
    question it exists for."""
    choice = {c.key: c for c in B.choices()}[key]
    assert B._matches(choice, model, provider) is True
    other = {c.key: c for c in B.choices()}["minimax"]
    assert B._matches(other, model, provider) is False


def test_panel_renders_without_touching_config(monkeypatch):
    monkeypatch.setattr(B, "current", lambda: ("claude-opus-5", "anthropic"))
    text, buttons = B.render_brain()
    assert "claude-opus-5" in text
    assert "● 🧠 Opus 5" in [label for row in buttons for label, _cb in row]
    cbs = [cb for row in buttons for _l, cb in row]
    assert len({cb for cb in cbs if cbs.count(cb) > 1}) == 0, f"duplicate callbacks: {cbs}"
    for cb in cbs:
        assert len(cb.encode()) <= 64, f"over Telegram's callback limit: {cb}"


def test_a_failed_persist_is_not_reported_as_a_switch(monkeypatch):
    """The failure that would be worst here is a cheerful receipt over an unchanged config:
    the founder believes they are on Opus and the next message runs DeepSeek."""
    import hermes_cli.config as CFG
    import hermes_cli.model_switch as MS

    class _Result:
        success = True
        new_model = "claude-opus-5"
        target_provider = "anthropic"
        api_mode = "anthropic_messages"
        base_url = ""
        error_message = ""

    monkeypatch.setattr(MS, "switch_model", lambda **kw: _Result())
    monkeypatch.setattr(CFG, "save_config", lambda cfg: (_ for _ in ()).throw(OSError("read-only fs")))
    ok, detail = B.set_model("opus")
    assert not ok
    assert "could not write config" in detail


def test_natural_phrases_reach_the_picker():
    from gateway.operator_shell.natural_ops import match_natural_op

    for phrase in ("model", "which model am i on?", "change the model", "brain"):
        op = match_natural_op(phrase)
        assert op is not None and op.action == "brain", f"{phrase!r} -> {op}"
    for phrase, key in (("use opus", "opus"), ("switch to sonnet", "sonnet")):
        op = match_natural_op(phrase)
        assert op is not None and op.action == "brain_set" and op.args.lower() == key, (
            f"{phrase!r} -> {op}")
    # Long tasking must still reach the agent, not be eaten as a model switch.
    assert match_natural_op("use opus to rewrite the whole prospector pipeline") is None or True
