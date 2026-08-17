"""The fifteen dead buttons, after they were built, repointed or deleted.

WHAT THIS IS FOR

`test_every_button_dispatches` proves an action reaches a branch. It says so itself: "It is
static. It proves an action reaches a branch, not that the branch works." Emptying its
quarantine from 15 to 0 therefore proved routing and nothing else — and three of the fifteen
MUTATE. A green routing test on a button that restarts daemons is not a safe state.

So this file tests the part the ratchet cannot see:

1. **No first tap writes.** `fix_all`, `rsi_run` and `onboard:add` each show a card and then
   execute on the SECOND tap. The first tap must reach the fixer/runner/registry either not at
   all or in dry-run — and the test asserts on the call itself, not on the words rendered,
   because a card that says "would restart" while restarting is exactly the defect.
2. **A report that cannot run renders a panel.** These five wrap scripts that touch launchd,
   git and logs. When one raises, the operator must get a panel naming the failure — not the
   dispatcher's generic "Action failed", which does not say which report broke.
3. **Underscores are neutralised.** Every value in these reports is a snake_case identifier
   (`policy_firings`, `tcc_permission`) and MarkdownV2 reads `_` as emphasis. One unbalanced
   pair draws a 400 and the operator sees nothing at all.
4. **The deleted buttons stay deleted.** Five actions had no implementation to wire. A future
   panel re-adding one would pass the routing ratchet only by re-quarantining it, so the
   deletions are pinned by name here where the reason is written down.
"""
from __future__ import annotations

import pytest

from gateway.operator_shell import estate_intel as EI


def _stub_coordinator():
    return object()


@pytest.fixture
def dispatch(monkeypatch):
    """`handle_estate_action` with the coordinator stubbed — the real dispatch path."""
    from gateway.operator_shell import estate as E

    monkeypatch.setattr(E, "_load_coordinator", _stub_coordinator)
    return E.handle_estate_action


# ── 1. No first tap writes ──────────────────────────────────────────────────────────────

def test_the_fix_all_card_runs_the_fixer_in_dry_run_only(dispatch, monkeypatch):
    """Screen one IS the dry run. `auto_fix_all(dry_run=False)` on a first tap would restart
    daemons under a card that says "would restart"."""
    seen = []

    def _fake(dry_run):
        seen.append(dry_run)
        return {"fixed": [{"problem": "cron", "detail": {"action": "would_restart"}}],
                "skipped": [], "failed": [], "verified": []}, ""

    monkeypatch.setattr(EI, "auto_fix", _fake)
    view = dispatch("fix_all", "t-fix-preview")

    assert seen == [True], f"first tap called the fixer as {seen}"
    assert "Unknown action" not in view.text
    assert "would restart" in view.text
    assert any(cb == "estate:fix_all_confirm" for row in view.buttons for _l, cb in row)


def test_the_second_tap_is_the_one_that_runs_it(dispatch, monkeypatch):
    seen = []

    def _fake(dry_run):
        seen.append(dry_run)
        return {"fixed": [{"problem": "cron", "detail": {"action": "restarted"}}],
                "skipped": [], "failed": [],
                "verified": [{"problem": "cron", "verify": {"verified": True,
                                                            "evidence": "job alive"}}]}, ""

    monkeypatch.setattr(EI, "auto_fix", _fake)
    view = dispatch("fix_all_confirm", "t-fix-run")

    assert seen == [False], f"the confirm tap called the fixer as {seen}"
    assert view.ok is True
    assert "job alive" in view.text


def test_a_failed_fix_is_not_reported_as_done(dispatch, monkeypatch):
    monkeypatch.setattr(EI, "auto_fix", lambda dry_run: (
        {"fixed": [], "skipped": [], "failed": [{"problem": "coordinator",
                                                 "detail": {"action": "error"}}],
         "verified": []}, ""))
    view = dispatch("fix_all_confirm", "t-fix-fail")
    assert view.ok is False


def test_nothing_stuck_offers_no_confirm_button(dispatch, monkeypatch):
    """A confirm button on an empty plan is a tap that does nothing and says it did."""
    monkeypatch.setattr(EI, "auto_fix", lambda dry_run: (
        {"fixed": [], "skipped": [], "failed": [], "verified": []}, ""))
    view = dispatch("fix_all", "t-fix-empty")
    assert "Nothing is stuck" in view.text
    assert not [cb for row in view.buttons for _l, cb in row if cb == "estate:fix_all_confirm"]


def test_the_fix_card_states_what_it_does_not_cover(dispatch, monkeypatch):
    """The 11 sites include the moat-down screen and the incidents panel. The fixer restarts
    cron, the coordinator and a config push — none of those. Saying so is the whole reason
    this button was safe to wire at all."""
    monkeypatch.setattr(EI, "auto_fix", lambda dry_run: (
        {"fixed": [], "skipped": [], "failed": [], "verified": []}, ""))
    text = dispatch("fix_all", "t-fix-scope").text
    assert "Does not cover" in text
    for missing in ("credits", "incidents", "policy", "CI"):
        assert missing in text, f"the card does not disclaim {missing}"


def test_the_rsi_card_does_not_start_a_cycle(dispatch, monkeypatch):
    """`trigger_cycle()` shells out to a real code-generating run."""
    from gateway.operator_shell import rsi_control

    called = []
    monkeypatch.setattr(rsi_control, "trigger_cycle",
                        lambda: called.append(1) or {"gaps_found": 0})
    view = dispatch("rsi_run", "t-rsi-card")

    assert called == [], "the confirm card ran the cycle"
    assert "WRITES" in view.text, "a mutating card must say it mutates"
    assert any(cb == "estate:rsi_run_confirm" for row in view.buttons for _l, cb in row)


def test_the_rsi_confirm_runs_it_and_reports_the_numbers(dispatch, monkeypatch):
    from gateway.operator_shell import rsi_control

    monkeypatch.setattr(rsi_control, "trigger_cycle", lambda: {
        "elapsed": 12.5, "gaps_found": 3, "velocity": 0.4,
        "regression_pass": 9, "regression_fail": 0})
    view = dispatch("rsi_run_confirm", "t-rsi-run")
    assert view.ok is True
    assert "3" in view.text and "12.5" in view.text


def test_a_crashing_cycle_is_reported_not_raised(dispatch, monkeypatch):
    from gateway.operator_shell import rsi_control

    def _boom():
        raise RuntimeError("runner missing")

    monkeypatch.setattr(rsi_control, "trigger_cycle", _boom)
    view = dispatch("rsi_run_confirm", "t-rsi-boom")
    assert view.ok is False
    assert "runner missing" in view.text
    assert "Traceback" not in view.text


# ── onboarding: the registry write ──────────────────────────────────────────────────────

_DISCOVERED = [{"name": "alpha", "branch": "main", "commit_age": "2d ago", "has_ci": True},
               {"name": "beta", "branch": "dev", "commit_age": "1w ago", "has_ci": False}]


@pytest.fixture
def onboard(monkeypatch):
    """Discovery stubbed to a fixed pair; `onboard_project` records instead of writing."""
    from gateway.operator_shell import projects

    written: list[str] = []
    monkeypatch.setattr(projects, "_discover_unregistered", lambda: list(_DISCOVERED))
    monkeypatch.setattr(projects, "onboard_project",
                        lambda name, project_type="incubating": written.append(name) or {
                            "key": name, "name": name.title(), "status": "incubating",
                            "risk": "low", "ci_provider": None})
    return written


def test_adding_one_repo_does_not_write_on_the_first_tap(dispatch, onboard):
    view = dispatch("onboard:add:alpha", "t-onb-card")
    assert onboard == [], "the confirm card wrote to the registry"
    assert any(cb == "estate:onboard:add_confirm:alpha"
               for row in view.buttons for _l, cb in row)


def test_the_confirm_writes_exactly_the_repo_named(dispatch, onboard):
    view = dispatch("onboard:add_confirm:alpha", "t-onb-write")
    assert onboard == ["alpha"]
    assert view.ok is True


def test_add_all_does_not_write_on_the_first_tap(dispatch, onboard):
    view = dispatch("onboard:add_all", "t-onb-all-card")
    assert onboard == []
    assert "alpha" in view.text and "beta" in view.text, "the card must list what it will add"


def test_add_all_confirm_writes_every_discovered_repo(dispatch, onboard):
    view = dispatch("onboard:add_all_confirm", "t-onb-all-write")
    assert onboard == ["alpha", "beta"]
    assert view.ok is True


def test_a_repo_that_is_not_on_the_list_is_refused(dispatch, onboard):
    """The callback carries a repo NAME. A stale panel, or a hand-typed callback, must not
    reach `onboard_project` with something discovery never offered."""
    view = dispatch("onboard:add:not_a_repo", "t-onb-unknown")
    assert onboard == []
    assert view.ok is False


def test_a_bare_onboard_tap_lands_on_the_root_card(dispatch, onboard):
    view = dispatch("onboard", "t-onb-root")
    assert "Onboard" in view.text
    assert any(cb == "estate:onboard:discover" for row in view.buttons for _l, cb in row)


def test_the_root_card_offers_only_the_flow_that_exists(dispatch, onboard):
    """new_product / client / template were deleted, not wired: no renderer, no handler, no
    template store. If one comes back it must come back with an implementation."""
    view = dispatch("onboard", "t-onb-deleted")
    callbacks = [cb for row in view.buttons for _l, cb in row]
    for gone in ("new_product", "client", "template"):
        assert not any(gone in cb for cb in callbacks), f"onboard:{gone} is back without a handler"


# ── 2. A report that cannot run renders a panel ─────────────────────────────────────────

_READ_ONLY = [
    ("estate_health", EI.render_estate_health),
    ("dependencies", EI.render_dependencies),
    ("compliance", EI.render_compliance),
    ("score", EI.render_score),
]


@pytest.mark.parametrize("name,render", _READ_ONLY, ids=[n for n, _ in _READ_ONLY])
def test_a_broken_report_renders_a_panel_naming_the_failure(name, render, monkeypatch):
    def _raise(*_a, **_k):
        raise OSError("script not found")

    monkeypatch.setattr(EI, "_call", lambda *a, **k: (None, "OSError: script not found"))
    monkeypatch.setattr(EI, "_identity_report", lambda: (None, "OSError: script not found"))
    text, buttons = render()
    assert "Could not run" in text, f"{name} hid the failure"
    assert "script not found" in text
    assert buttons, f"{name} rendered a dead end"


def test_correlate_survives_one_correlator_dying(monkeypatch):
    """It reads two. Losing one must cost half the panel, not all of it."""
    def _call(module, func, *args):
        if module == "predictor":
            return None, "ImportError: no predictor"
        return {"clusters": [{"count": 4, "shared_cause": "API credits",
                              "failure_types": ["ops_moat", "log_error"]}]}, ""

    monkeypatch.setattr(EI, "_call", _call)
    text, _buttons = EI.render_correlate()
    assert "unavailable" in text, "the dead correlator was not disclosed"
    assert "API credits" in text, "the live correlator's finding was lost with it"


def test_a_report_full_of_nothing_does_not_read_as_a_measurement(monkeypatch):
    """`dependency_map` returns `status: unknown` when it has no signal to read. Printing
    that as a bare word invites it to be read as 'fine'."""
    monkeypatch.setattr(EI, "_call", lambda *a, **k: ({"dependencies": {
        "prospector": {"depends_on": ["api_credits"], "status": "unknown",
                       "blocked_by": []}}}, ""))
    text, _ = EI.render_dependencies()
    assert "⚪" in text
    assert "no signal" in text


# ── 3. Report values cannot be read as markup ───────────────────────────────────────────
#
# The panel source these renderers return is NOT MarkdownV2 — `render_panel` converts it on
# the way out, and it escapes. Measured on the real send path:
#
#     "id: policy_firings"  -> plain "id: policy_firings"   (underscore escaped, safe)
#     "worst: a*b*c"        -> plain "worst: a*b*c"         (asterisk literal, safe)
#     "cause: fix[1](2)"    -> plain "cause: fix1"          (LOST — parsed as a text_link)
#
# So the escaping layer already covers `_` and `*`. Brackets it does not: a value carrying
# them loses characters silently, and a report of shell output or a URL is exactly where
# brackets come from. That is the one `_words` must own, and it is what this section proves —
# not the broader "no underscore reaches Telegram", which the renderer handles and which an
# earlier version of this test wrongly credited to `_words`.

def test_bracketed_data_would_swallow_text_without_the_scrubber():
    """The defect first, so the guard below is not pinning a no-op."""
    from tests.gateway.operator_shell.test_mdv2_panel_rendering import parse, render_panel

    raw = "cause: fix[1](2)"
    plain, _ents = parse(render_panel(raw))
    assert plain != raw, "brackets are safe unescaped now — re-check whether _words is needed"

    scrubbed = f"cause: {EI._words('fix[1](2)')}"
    plain, ents = parse(render_panel(scrubbed))
    assert plain == scrubbed, f"the scrubbed value still changed: {plain!r}"
    assert not ents, f"the scrubbed value still parsed as markup: {ents}"


@pytest.mark.parametrize("raw", ["policy_firings", "tcc_permission", "a*b", "x[y](z)", "e_f_g_h"])
def test_the_scrubber_strips_every_markup_character(raw):
    out = EI._words(raw)
    assert "_" not in out, "snake_case must read as words, not as identifiers"
    for ch in "*[]()~`>#+=|{}\\":
        assert ch not in out, f"{ch!r} survived in {out!r}"


def test_an_identifier_is_rendered_as_words(monkeypatch):
    """Readability — the other half of `_words`, and the founder's "less cryptic"."""
    monkeypatch.setattr(EI, "_call", lambda module, func, *a: (
        {"overall": 62, "breakdown": {"signal_engine": 40, "hermes_gateway": 84}}, ""))
    text, _ = EI.render_estate_health()
    assert "signal engine" in text and "signal_engine" not in text


def test_every_new_panel_survives_the_real_send_path(monkeypatch):
    """The estate-wide sweep (`test_mdv2_panel_rendering`) discovers all seven of these, but
    it skips any panel that raises — and these raise without the live estate scripts. Fed
    data, they must still survive the converter."""
    from tests.gateway.operator_shell.test_mdv2_panel_rendering import parse, render_panel

    monkeypatch.setattr(EI, "_call", lambda module, func, *a: (
        {"overall": 62, "breakdown": {"signal_engine": 40},
         "dependencies": {"signal_engine": {"depends_on": ["api_credits"],
                                            "status": "blocked",
                                            "blocked_by": ["api_credits"]}},
         "clusters": [{"count": 2, "shared_cause": "api_credits",
                       "failure_types": ["ops_moat"]}],
         "windows": [{"window": "02:00-04:00", "count": 3, "types": ["cron_fail"]}],
         "fixed": [], "skipped": [], "failed": [], "verified": [],
         "current_score": 71, "target_score": 90, "trend": "up",
         "passed": True, "details": "no regression"}, ""))
    monkeypatch.setattr(EI, "_identity_report", lambda: (
        {"identity": {"agent_id": "hermes_1", "compliant": True}}, ""))

    panels = list(EI._RENDERERS.items()) + [("fix_preview", EI.render_fix_preview)]
    assert len(panels) == 7, f"a panel was added or lost: {[n for n, _ in panels]}"
    for name, render in panels:
        text, _buttons = render()
        assert text, f"{name} rendered nothing"
        plain, _ents = parse(render_panel(text))  # a ParseError here == a 400 on the phone
        assert plain, f"{name} rendered to empty plain text"


# ── 4. The deleted buttons stay deleted ─────────────────────────────────────────────────

@pytest.mark.parametrize("action", ["fix_all_safe", "setup_wizard", "operator_mode", "deploy"])
def test_a_deleted_action_is_emitted_by_no_button(action):
    """Deleted because nothing implemented them — a TTY-only wizard, a class never
    instantiated, a deploy function that does not exist, and a feature-registry entry citing a
    test that was never written. Re-adding a button would only pass the routing ratchet by
    re-quarantining it, so the ban is pinned here where the reason is."""
    import re
    from pathlib import Path

    shell = Path(__file__).resolve().parents[3] / "gateway" / "operator_shell"
    pattern = re.compile(rf'"estate:{action}\b')
    offenders = [
        f"{path.name}:{n}"
        for path in sorted(shell.glob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line) and ".startswith(" not in line
    ]
    assert not offenders, f"estate:{action} is emitted again at {offenders}"


def test_the_learning_toggle_points_at_the_switch_that_is_actually_read():
    """`rsi_pause`/`rsi_resume` called `toggle_learning()`, which writes
    logs/meta-improver/OFF_SWITCH. The live switch is meta/OFF_SWITCH (rsi_panel.py:19) with
    the OPPOSITE polarity, so the panel would have toasted "paused" while learning stayed
    live. The buttons now point at arm/disarm_learning, which estate.py:861/886 already
    implement correctly."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[3] / "gateway" / "operator_shell"
           / "rsi_control.py").read_text(encoding="utf-8")
    assert "estate:rsi_pause" not in src and "estate:rsi_resume" not in src
    assert "estate:disarm_learning" in src and "estate:arm_learning" in src
