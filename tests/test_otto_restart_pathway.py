"""Contract tests for the Otto restart/rescue pathway.

Proves that every entry-point the user might reach for when Otto is
unresponsive actually triggers the right action.  No mocks — these
test the real command resolution and natural-language parsing that
the gateway runs on every inbound message.
"""
import pytest

# ── Slash commands ─────────────────────────────────────────────────────

class TestSlashRestart:
    """``/restart`` reaches the restart handler in both cold and busy paths."""

    def test_restart_is_registered_command(self):
        from hermes_cli.commands import resolve_command
        cmd = resolve_command("restart")
        assert cmd is not None, "/restart must be a recognised command"
        assert cmd.name == "restart"
        assert cmd.gateway_only is True

    def test_restart_is_not_cli_only(self):
        from hermes_cli.commands import resolve_command
        cmd = resolve_command("restart")
        assert not cmd.cli_only, "restart must work from messaging platforms"


class TestSlashStop:
    """``/stop`` — handled inline, not via command resolver."""

    def test_stop_handled_in_active_session_path(self):
        # /stop is handled inline in the active-session fast path
        # (gateway/run.py) with _interrupt_and_clear_session().
        # It is NOT registered as a CommandDef — the handler is hardcoded.
        # Verify the source contains the stop handler.
        import gateway.run
        src_file = gateway.run.__file__ or ""
        if src_file:
            with open(src_file) as f:
                text = f.read()
            assert '"stop"' in text, "/stop must be handled in gateway/run.py"


# ── Natural-language ops (operator shell) ──────────────────────────────

class TestNaturalRestart:
    """Every phrase the user might type when Otto is unresponsive."""

    def _assert_matches(self, text: str, expected_action: str,
                        expected_context: str):
        from gateway.operator_shell.natural_ops import match_natural_op
        result = match_natural_op(text)
        assert result is not None, f"\"{text}\" must match a natural op"
        assert result.action == expected_action, (
            f"\"{text}\" should trigger {expected_action}, got {result.action}"
        )
        assert result.args == expected_context, (
            f"\"{text}\" should have context {expected_context}, got {result.args}"
        )

    # Basic commands
    def test_restart_gateway(self):
        self._assert_matches("restart gateway", "daemon_restart", "gateway")

    def test_bounce_gateway(self):
        self._assert_matches("bounce gateway", "daemon_restart", "gateway")

    # New simplified triggers — the "otto is stuck" vocabulary
    def test_restart_otto(self):
        self._assert_matches("restart otto", "daemon_restart", "gateway")

    def test_fix_otto(self):
        self._assert_matches("fix otto", "daemon_restart", "gateway")

    def test_bounce_otto(self):
        self._assert_matches("bounce otto", "daemon_restart", "gateway")

    def test_kick_otto(self):
        self._assert_matches("kick otto", "daemon_restart", "gateway")

    # Bare state words — one word, no prefix
    def test_stuck(self):
        self._assert_matches("stuck", "daemon_restart", "gateway")

    def test_hung(self):
        self._assert_matches("hung", "daemon_restart", "gateway")

    def test_frozen(self):
        self._assert_matches("frozen", "daemon_restart", "gateway")

    def test_unresponsive(self):
        self._assert_matches("unresponsive", "daemon_restart", "gateway")

    def test_dead(self):
        self._assert_matches("dead", "daemon_restart", "gateway")

    def test_broken(self):
        self._assert_matches("broken", "daemon_restart", "gateway")

    # "otto <state>" without "is"
    def test_otto_stuck(self):
        self._assert_matches("otto stuck", "daemon_restart", "gateway")

    def test_otto_hung(self):
        self._assert_matches("otto hung", "daemon_restart", "gateway")

    def test_otto_dead(self):
        self._assert_matches("otto dead", "daemon_restart", "gateway")

    # "otto is <state>"
    def test_otto_is_stuck(self):
        self._assert_matches("otto is stuck", "daemon_restart", "gateway")

    def test_otto_is_frozen(self):
        self._assert_matches("otto is frozen", "daemon_restart", "gateway")


# ── Cockpit / panel entry ──────────────────────────────────────────────

class TestCockpitEntry:
    """One word gets you to the panel where every action is a button."""

    def test_cockpit_reachable(self):
        from gateway.operator_shell.natural_ops import match_natural_op
        result = match_natural_op("cockpit")
        assert result is not None, "\"cockpit\" must be reachable"

    def test_panel_reachable(self):
        from gateway.operator_shell.natural_ops import match_natural_op
        result = match_natural_op("panel")
        assert result is not None, "\"panel\" must be reachable"

    def test_mission_reachable(self):
        from gateway.operator_shell.natural_ops import match_natural_op
        # "mission" refreshes the mission card, which is the cockpit entry.
        result = match_natural_op("mission")
        assert result is not None, "\"mission\" must be reachable"


# ── Non-interference: free chat must never restart Otto ────────────────

class TestFreeChatNeverRestarts:
    """Normal messages must NOT trigger a gateway restart."""

    def _assert_not_restart(self, text: str):
        from gateway.operator_shell.natural_ops import match_natural_op
        result = match_natural_op(text)
        # Free chat may match non-restart ops like "find" or "refresh".
        # Those are fine. It must NOT match daemon_restart.
        if result is not None:
            assert result.action != "daemon_restart", (
                f"\"{text}\" must NOT trigger a gateway restart"
            )

    def test_free_chat_not_restart(self):
        self._assert_not_restart("what is the weather today")
        self._assert_not_restart("how do I deploy this")
        self._assert_not_restart("tell me about stuck projects")
        self._assert_not_restart("the gateway is stuck on something")
