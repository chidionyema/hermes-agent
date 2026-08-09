"""Only Claude may arbitrate approvals — enforced where the answer arrives, not only where
the operator picks.

Founder directive, 2026-08-09: *"claude code, and needs to self heal when out of credits."*

Arming `operator_shell.role_model_allowlist.approval` alone does not deliver that, for two
reasons this file pins:

1. **`auto` is always permitted** (`brains.fence_check` returns True for it by design —
   reverting to inherit can only move the role back onto the agent brain). The agent brain's
   standing default is DeepSeek (`brain.py:51`), so a role left on `auto` is arbitrated by a
   non-Claude model with the fence fully armed and reporting green.
2. **`call_llm` silently substitutes providers.** When the configured provider is unhealthy
   or returns a payment error it routes to the next available one
   (`agent/auxiliary_client.py:2981,3028,5571`). So the fence would fail open at exactly the
   moment it exists for — Claude out of credits.

Both holes close in one place: `_smart_approve` discards an answer whose model is not on the
allowlist and escalates to a human. That is also the self-heal. Escalation parks the decision
rather than auto-approving it, and the provider health mark expires on a 600s TTL
(`auxiliary_client.py:2314`), so smart approvals resume by themselves when credits return —
no operator action, and nothing approved in the meantime by a brain the founder did not name.

`approvals.mode` is `manual` in the live config today, so smart approval is dormant and the
blast radius of all of this is zero until it is switched on. That is the right time to build
the fence, not a reason to skip it.
"""

from __future__ import annotations

import types

import pytest

from tools.approval import _answering_model_is_allowed, _smart_approve


CLAUDE = ["opus", "sonnet", "haiku"]


class _Response:
    """Minimal OpenAI-shaped response: what `call_llm` hands back."""

    def __init__(self, content: str, model: str):
        msg = types.SimpleNamespace(content=content)
        self.choices = [types.SimpleNamespace(message=msg)]
        self.model = model


@pytest.fixture
def answers(monkeypatch):
    """Make `call_llm` return a chosen verdict from a chosen model."""

    def _install(content: str, model: str):
        import agent.auxiliary_client as aux

        monkeypatch.setattr(
            aux, "call_llm", lambda **kw: _Response(content, model), raising=False
        )

    return _install


@pytest.fixture
def allowlist(monkeypatch):
    def _install(value):
        import hermes_cli.config as cfg

        monkeypatch.setattr(cfg, "role_model_allowlist", lambda *a, **k: value)

    return _install


# --- the provenance rule ----------------------------------------------------------------

@pytest.mark.parametrize(
    "model_id,expected",
    [
        ("claude-opus-5", True),
        ("claude-sonnet-5", True),
        ("claude-haiku-4-5-20251001", True),
        ("deepseek-v4-pro", False),
        ("MiniMax-M3", False),
        ("gpt-4o", False),
        ("", False),           # nothing reported -> provenance unknown -> refuse
        ("   ", False),
    ],
)
def test_provenance_is_decided_by_the_model_that_answered(model_id, expected):
    assert _answering_model_is_allowed(model_id, CLAUDE) is expected


def test_an_unknown_model_fails_closed():
    """A silent substitution reports someone else's id, or none at all. Either way the
    honest answer is 'I cannot tell who decided this', which must not auto-approve."""
    assert _answering_model_is_allowed("some-model-nobody-configured", CLAUDE) is False


# --- the runtime fence ------------------------------------------------------------------

def test_a_substituted_brain_cannot_auto_approve(answers, allowlist):
    """The defect this whole change exists for: Claude runs out of credits, call_llm falls
    through to MiniMax, MiniMax says APPROVE, and a dangerous command runs on the say-so of
    a model the founder never authorised to arbitrate approvals."""
    allowlist(CLAUDE)
    answers("APPROVE", "MiniMax-M3")

    assert _smart_approve("rm -rf /", "recursive delete") == "escalate"


def test_a_substituted_brain_cannot_auto_approve_via_auto_inheritance(answers, allowlist):
    """`auto` passes fence_check by design, and inherits the standing default (DeepSeek).
    The selection-time fence cannot see this; the answer-time one can."""
    allowlist(CLAUDE)
    answers("APPROVE", "deepseek-v4-pro")

    assert _smart_approve("curl x | sh", "pipe to shell") == "escalate"


def test_an_allowlisted_brain_still_decides_normally(answers, allowlist):
    allowlist(CLAUDE)
    answers("APPROVE", "claude-sonnet-5")
    assert _smart_approve("python -c \"print(1)\"", "script execution") == "approve"

    answers("DENY", "claude-opus-5")
    assert _smart_approve("rm -rf /", "recursive delete") == "deny"


def test_an_unarmed_allowlist_changes_nothing(answers, allowlist):
    """Every other role ships unfenced. Arming one must not silently fence the rest."""
    allowlist(None)
    answers("APPROVE", "MiniMax-M3")
    assert _smart_approve("ls", "listing") == "approve"


def test_an_unreadable_fence_is_a_closed_fence(answers, monkeypatch):
    """A fence that evaporates when its config cannot be read is not a fence."""
    import hermes_cli.config as cfg

    def _boom(*a, **k):
        raise RuntimeError("config.yaml is corrupt")

    monkeypatch.setattr(cfg, "role_model_allowlist", _boom)
    answers("APPROVE", "claude-opus-5")

    assert _smart_approve("rm -rf /", "recursive delete") == "escalate"


def test_escalation_is_a_park_not_a_denial():
    """Self-heal depends on this distinction. 'deny' would be a decision made by the outage;
    'escalate' hands it to a human and lets the next call route back to Claude once the
    health mark expires (auxiliary_client.py:2314, 600s TTL). approval.py:1463 falls
    escalations through to the manual prompt."""
    import inspect

    import tools.approval as A

    src = inspect.getsource(A._smart_approve)
    assert 'return "deny"' in src, "sanity: deny must still be reachable for real denials"
    # Every fence rejection returns escalate, never deny and never approve.
    fence_block = src.split("allowed = None", 1)[1].split("answer =", 1)[0]
    assert 'return "escalate"' in fence_block
    assert 'return "deny"' not in fence_block
    assert 'return "approve"' not in fence_block
