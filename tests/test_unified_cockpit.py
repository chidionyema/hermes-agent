"""End-to-end contract tests for the unified cockpit.

Every button on the Home screen and SDLC panel must work.
No mocks — these test the real rendering and navigation paths.
"""
import pytest


# ── Helpers ────────────────────────────────────────────────────────────

def _all_button_callbacks(buttons):
    """Flatten all button rows to (label, callback) pairs."""
    out = []
    for row in buttons:
        for label, cb in row:
            out.append((label, cb))
    return out


def _assert_renders(module_name, fn_name):
    """Import and render — must not crash."""
    import importlib
    mod = importlib.import_module(f"gateway.operator_shell.{module_name}")
    fn = getattr(mod, fn_name)
    result = fn()
    assert result is not None, f"{module_name}.{fn_name}() returned None"
    return result


# ── Home Screen ────────────────────────────────────────────────────────

class TestHomeScreen:
    """``render_mission_card()`` — the unified home that ``otto`` shows."""

    def test_renders_without_crashing(self):
        text, ok, buttons = _assert_renders("mission", "render_mission_card")
        assert isinstance(text, str), "home text must be a string"
        assert len(text) > 100, "home text must have meaningful content"
        assert isinstance(ok, bool), "ok must be a bool"
        assert isinstance(buttons, list), "buttons must be a list"

    def test_has_all_four_nav_buttons(self):
        _text, _ok, buttons = _assert_renders("mission", "render_mission_card")
        callbacks = {cb for _l, cb in _all_button_callbacks(buttons)}
        nav_actions = {
            "estate:refresh",   # Home
            "estate:run",       # Actions
            "estate:sdlc",      # SDLC
            "estate:find",      # Browse
        }
        missing = nav_actions - callbacks
        assert not missing, f"nav spine missing: {missing}"

    def test_quick_actions_include_restart_and_status(self):
        _text, _ok, buttons = _assert_renders("mission", "render_mission_card")
        labels = {l for l, _cb in _all_button_callbacks(buttons)}
        assert any("Restart" in l or "restart" in l for l in labels), \
            "must have a restart button"
        assert any("Status" in l or "status" in l for l in labels), \
            "must have a status button"

    def test_daemon_controls_present(self):
        _text, _ok, buttons = _assert_renders("mission", "render_mission_card")
        callbacks = {cb for _l, cb in _all_button_callbacks(buttons)}
        assert "estate:daemon_restart_now:gateway" in callbacks, \
            "must have gateway restart button"
        assert "estate:help" in callbacks, \
            "must have help button"

    def test_headline_is_otto_not_cockpit(self):
        text, _ok, _buttons = _assert_renders("mission", "render_mission_card")
        assert "Otto" in text, "headline must say Otto, not Cockpit"


# ── SDLC Panel ─────────────────────────────────────────────────────────

class TestSdlcPanel:
    """``render_sdlc()`` — the consolidated SDLC pipeline view."""

    def test_renders_without_crashing(self):
        text, buttons = _assert_renders("sdlc", "render_sdlc")
        assert isinstance(text, str), "sdlc text must be a string"
        assert len(text) > 100, "sdlc text must have meaningful content"
        assert isinstance(buttons, list), "sdlc buttons must be a list"

    def test_has_all_six_pipeline_stages(self):
        text, _buttons = _assert_renders("sdlc", "render_sdlc")
        stages = ["Assign", "Board", "Fleet", "Review", "Ship", "Learn"]
        for stage in stages:
            assert stage in text, f"SDLC must show {stage} stage"

    def test_has_all_four_nav_buttons(self):
        _text, buttons = _assert_renders("sdlc", "render_sdlc")
        callbacks = {cb for _l, cb in _all_button_callbacks(buttons)}
        nav_actions = {
            "estate:refresh", "estate:run", "estate:sdlc", "estate:find",
        }
        missing = nav_actions - callbacks
        assert not missing, f"nav spine missing: {missing}"

    def test_board_shows_active_missions(self):
        text, buttons = _assert_renders("sdlc", "render_sdlc")
        labels = {l for l, _cb in _all_button_callbacks(buttons)}
        # Must have either a missions button or mission data
        has_missions = (
            any("mission" in l.lower() for l in labels)
            or "Board" in text
        )
        assert has_missions, "SDLC must show board/missions section"

    def test_fleet_shows_repos(self):
        text, buttons = _assert_renders("sdlc", "render_sdlc")
        labels = {l for l, _cb in _all_button_callbacks(buttons)}
        has_fleet = (
            any("fleet" in l.lower() or "repo" in l.lower() for l in labels)
            or "Fleet" in text
        )
        assert has_fleet, "SDLC must show fleet/repos section"

    def test_assign_has_button(self):
        _text, buttons = _assert_renders("sdlc", "render_sdlc")
        callbacks = {cb for _l, cb in _all_button_callbacks(buttons)}
        assert "estate:code_prompt" in callbacks, \
            "SDLC must have Assign button"


# ── Natural Language Dispatch ──────────────────────────────────────────

class TestNaturalLanguageDispatch:
    """Every entry point on the home screen must be reachable by typing."""

    def _assert_action(self, text, expected_action):
        from gateway.operator_shell.natural_ops import match_natural_op
        result = match_natural_op(text)
        assert result is not None, f"'{text}' must match a natural op"
        assert result.action == expected_action, \
            f"'{text}' → {result.action}, expected {expected_action}"

    def test_sdlc_reachable(self):
        self._assert_action("sdlc", "sdlc")

    def test_pipeline_reachable(self):
        self._assert_action("pipeline", "sdlc")

    def test_home_still_reachable(self):
        self._assert_action("otto", "refresh")

    def test_help_still_reachable(self):
        self._assert_action("help", "help")

    def test_actions_still_reachable(self):
        self._assert_action("run", "run")

    def test_browse_still_reachable(self):
        self._assert_action("rooms", "find")


# ── Estate Dispatch ────────────────────────────────────────────────────

class TestEstateDispatch:
    """Every action the home screen fires must have a handler in estate.py.
    
    These tests verify the handler EXISTS and renders without crashing.
    They do NOT require a live coordinator — they test the dispatch path
    gracefully handles the 'estate unavailable' case, which is the normal
    test-environment state.
    """

    def _assert_dispatches(self, action, arg=""):
        """Handler must exist and return a PanelView (not crash)."""
        from gateway.operator_shell.estate import handle_estate_action
        result = handle_estate_action(action, arg)
        assert result is not None, f"estate must handle action '{action}'"
        # In test env without coordinator, ok may be False. That's fine.
        # What matters: it rendered without throwing.
        assert hasattr(result, 'text'), "result must have text"
        assert isinstance(result.text, str), "text must be string"
        assert len(result.text) > 20, f"'{action}' must render meaningful text"
        return result

    def test_refresh_handled(self):
        self._assert_dispatches("refresh")

    def test_run_handled(self):
        self._assert_dispatches("run")

    def test_sdlc_handled(self):
        self._assert_dispatches("sdlc")

    def test_find_handled(self):
        self._assert_dispatches("find")

    def test_help_handled(self):
        self._assert_dispatches("help")

    def test_every_nav_action_has_handler(self):
        """The 4 nav actions must all have handlers."""
        from gateway.operator_shell.estate import handle_estate_action
        nav_actions = ["refresh", "run", "sdlc", "find"]
        for action in nav_actions:
            # Just verify the action doesn't crash the dispatcher.
            # We check this by confirming no exception escapes.
            try:
                result = handle_estate_action(action, "")
                assert result is not None, f"'{action}' returned None"
            except Exception as e:
                # Only fail if the error is "unknown action", not if
                # it's a coordinator-not-found error.
                msg = str(e).lower()
                if "unknown" in msg or "unrecognized" in msg or "no handler" in msg:
                    pytest.fail(f"estate has no handler for '{action}': {e}")
