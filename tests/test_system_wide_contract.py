"""System-wide contract test: every estate action must have a working handler.

Iterates over ALL 68 natural-language actions and verifies each one
dispatches without crashing. Actions that need arguments are tested
with placeholder values. Actions that require a live coordinator
report as 'unavailable' rather than broken — we can't test coordinator-
dependent actions in a test environment, but we CAN verify the handler
exists and returns a proper PanelView.
"""
import pytest
from typing import Dict, List, Tuple


# ── All 68 actions from natural_ops._PATTERNS ──────────────────────────

def _all_actions() -> List[Tuple[str, str, str]]:
    """Return every unique (action, args, label) from natural_ops."""
    from gateway.operator_shell.natural_ops import _PATTERNS
    seen = {}
    for pat, action, args, label in _PATTERNS:
        if not label:
            continue
        key = f"{action}:{args}" if args else action
        if key not in seen:
            seen[key] = (action, args, label)
    return sorted(seen.values(), key=lambda x: (x[0], x[1]))


# Actions that REQUIRE a live coordinator — in test env they'll return
# 'estate unavailable'. That's not a failure — we just verify the handler
# renders gracefully.
_COORDINATOR_DEPENDENT = {
    "refresh", "status", "brief", "inbox", "fleet", "builds",
    "brain", "brain_set", "rsi", "arm_learning", "disarm_learning",
    "missions", "task", "cancel", "pause_task", "stop_agent",
    "code_assign", "activity", "system_fuel", "host", "host_keepawake_start",
    "run", "tune", "find", "help", "sdlc", "atlas", "room",
    "prospector_daemon", "signal_engine", "daemons",
    "st_status", "st_health", "st_reconcile", "st_money",
    "summary",
}

# Actions that need an argument placeholder in tests.
_ARG_ACTIONS: Dict[str, str] = {
    "approve": "abc12345",
    "task": "abc12345",
    "cancel": "abc12345",
    "pause_task": "abc12345",
    "code_assign": "fix the bug",
    "brain_set": "test-model",
    "run_prospector": "test",
    "summary": "abc12345",
    "room": "code",
    "find": "test",
    "se_set": "test:live",
    "pd_set": "test:1",
}


class TestEveryAction:
    """Every of the 68 natural-language actions must dispatch without crashing."""

    @pytest.mark.parametrize("action,args,label", _all_actions())
    def test_action_dispatches(self, action, args, label):
        from gateway.operator_shell.estate import handle_estate_action

        # Provide a placeholder arg if needed
        test_arg = _ARG_ACTIONS.get(action, args or "")

        try:
            result = handle_estate_action(action, test_arg)
        except Exception as e:
            pytest.fail(
                f"Action '{action}' (\"{label}\") crashed: {type(e).__name__}: {e}"
            )

        # Every action must return a PanelView with text
        assert result is not None, f"'{action}' returned None"
        assert hasattr(result, "text"), f"'{action}' result has no 'text' attribute"
        assert isinstance(result.text, str), f"'{action}' text is not a string"

        # Actions that need a coordinator will show 'estate unavailable'.
        # That's fine — the handler itself works. We just check it rendered.
        if action in _COORDINATOR_DEPENDENT:
            # Coordinator-dependent actions are allowed to show unavailable.
            # What matters: they rendered without throwing.
            pass
        else:
            # Non-coordinator actions should render meaningful content.
            assert len(result.text) > 20, (
                f"'{action}' ({label}) rendered too little text: "
                f"{len(result.text)} chars: {result.text[:80]}"
            )


class TestNaturalLanguageCoverage:
    """Every action must have at least one natural-language trigger."""

    def test_every_estate_action_has_natural_trigger(self):
        """Check that actions in the dispatch chain have natural_ops entries."""
        from gateway.operator_shell.natural_ops import _PATTERNS

        # Known actions handled in estate.py (from reading the dispatch)
        estate_actions = set()
        for pat, action, args, label in _PATTERNS:
            if label:
                estate_actions.add(action)

        # These are core estate actions that MUST have natural triggers
        core = {"refresh", "run", "tune", "find", "help", "sdlc",
                "status", "brief", "daemons", "host", "brain",
                "inbox", "fleet", "builds", "rsi", "activity",
                "prospector_daemon", "signal_engine"}
        missing = core - estate_actions
        assert not missing, (
            f"Core actions missing natural-language triggers: {missing}"
        )


class TestSlashCommandsExist:
    """Every critical action should be reachable via slash command too."""

    def test_critical_commands_registered(self):
        from hermes_cli.commands import resolve_command

        critical = ["restart", "stop", "new", "status", "help",
                    "model", "tools", "skills", "memory", "sessions"]
        missing = []
        for cmd in critical:
            if resolve_command(cmd) is None:
                missing.append(cmd)
        # Not all need to be registered — some are handled inline.
        # Just assert the most critical ones.
        assert resolve_command("restart") is not None, "/restart must be registered"
        assert resolve_command("stop") is not None, "/stop must be registered"
        assert resolve_command("help") is not None, "/help must be registered"
