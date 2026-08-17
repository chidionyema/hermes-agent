"""The cockpit has a way back — and the ways it could be a fake way back are fenced.

Before 2026-08-14 it did not. `nav_stack.py` existed, its docstring claimed "the nav()
function reads this stack and adds ← Back / → Forward buttons", and `panel_chrome.py`
contained zero references to it: no module in the repo imported `nav_stack` at all. 63
panels, no Back button on any of them, and a file beside them asserting otherwise.

That is why these tests assert the WIRING and not just the helper. A unit test of
`go_back()` would have passed for the entire period the feature was unreachable — the
functions were always correct; nothing called them. Memory:
`built-and-unreachable-is-the-cockpit-defect-class`.

`HERMES_HOME` is a per-test tempdir (`tests/conftest.py:360`), so each test starts with no
history and the real `~/.hermes/state/nav-stack.json` is never touched.
"""

from __future__ import annotations

import json

import pytest

from gateway.operator_shell import nav_stack, panel_chrome


@pytest.fixture(autouse=True)
def _clean_history():
    nav_stack.reset()
    yield
    nav_stack.reset()


# ---------------------------------------------------------------- the wiring


def test_nav_stack_is_actually_imported_by_the_chrome_that_claims_to_use_it():
    """The regression that matters: the spine must REFERENCE the history module.

    Asserting on the rendered row alone is not enough — that is what `_clean_history`
    makes empty, so a row with no ← looks identical to a row that can never have one.
    This reads the source of the module that draws the spine.
    """
    import inspect

    src = inspect.getsource(panel_chrome.nav)
    assert "nav_stack" in src, (
        "panel_chrome.nav() no longer consults nav_stack — the ← button is unreachable "
        "again, exactly as it was from the file's creation until 2026-08-14."
    )


def test_back_appears_only_once_there_is_somewhere_to_go_back_to():
    labels_at_root = [l for l, _cb in panel_chrome.nav()]
    assert "←" not in labels_at_root, (
        "a Back button on the root card points at nothing; a control that does nothing is "
        "the defect the spine exists to remove"
    )

    nav_stack.push_nav("refresh", "🏠 Home")
    nav_stack.push_nav("st_health", "Store health")

    row = panel_chrome.nav()
    assert row[0] == ("←", "estate:back"), f"← must lead the spine, got {row[:2]}"


def test_back_does_not_disturb_the_six_spine_buttons():
    """Adding ← must not push a destination off the row.

    `test_panel_chrome_spine` and `test_cockpit_activity` assert exact spine membership;
    this states the same invariant from the Back side so a future change to ← cannot
    quietly cost a destination.
    """
    nav_stack.push_nav("refresh", "🏠 Home")
    nav_stack.push_nav("tune", "⚙️ Tune")
    cbs = [cb for _l, cb in panel_chrome.nav()]
    for declared in (
        panel_chrome._NOW,
        panel_chrome._PROJECTS,
        panel_chrome._RUN,
        panel_chrome._SDLC,
        panel_chrome._TUNE,
        panel_chrome._MAP,
    ):
        assert declared[1] in cbs, f"{declared[0]} fell off the spine when ← was added"


# ---------------------------------------------------------------- the behaviour


def test_back_walks_the_trail_in_reverse():
    for act in ("refresh", "run", "st_health"):
        nav_stack.push_nav(act, panel_chrome.label_for(act))

    assert nav_stack.go_back()["action"] == "run"
    assert nav_stack.go_back()["action"] == "refresh"
    assert nav_stack.go_back() is None, "at the root, Back must report there is nowhere to go"


def test_forward_returns_to_the_panel_you_backed_away_from():
    nav_stack.push_nav("refresh")
    nav_stack.push_nav("run")
    nav_stack.go_back()
    assert nav_stack.go_forward()["action"] == "run"


def test_navigating_somewhere_new_clears_the_forward_history():
    """Browser semantics: a new destination invalidates the branch you backed out of."""
    nav_stack.push_nav("refresh")
    nav_stack.push_nav("run")
    nav_stack.go_back()
    nav_stack.push_nav("tune")
    assert nav_stack.go_forward() is None


def test_refreshing_the_same_panel_does_not_stack_duplicates():
    """Five taps of 🔄 must not cost five taps of ← to escape."""
    nav_stack.push_nav("refresh")
    for _ in range(5):
        nav_stack.push_nav("st_health")
    assert nav_stack.go_back()["action"] == "refresh"


def test_the_navigation_verbs_never_enter_history():
    """Pushing `back` would make Back walk into itself."""
    nav_stack.push_nav("refresh")
    nav_stack.push_nav("back")
    nav_stack.push_nav("forward")
    assert nav_stack.current()["action"] == "refresh"


# ---------------------------------------------------------------- the fences


def test_a_corrupt_history_file_costs_the_back_button_not_the_panel():
    """The failure mode that must never happen: a panel that will not draw.

    A truncating write leaves a char-0 read, not valid JSON (memory
    `a-truncating-write-is-an-empty-read-not-bad-json`), so this writes exactly that.
    """
    path = nav_stack._stack_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"stack": [{"action": "run"')  # truncated mid-object

    assert nav_stack.can_go_back() is False
    assert nav_stack.breadcrumb() == ""
    row = panel_chrome.nav()  # must not raise
    assert any(cb == "estate:refresh" for _l, cb in row)


def test_history_is_written_atomically():
    """A rename, not a truncating write — so a reader never sees half a stack."""
    nav_stack.push_nav("refresh")
    nav_stack.push_nav("run")
    data = json.loads(nav_stack._stack_file().read_text())
    assert data["current"]["action"] == "run"
    leftovers = list(nav_stack._stack_file().parent.glob(".nav-stack-*.tmp"))
    assert not leftovers, f"temp files left behind: {leftovers}"


def test_the_stack_file_path_is_resolved_per_call_not_at_import(monkeypatch, tmp_path):
    """Otherwise the suite writes the founder's live history on every run.

    `HERMES = Path(os.environ.get("HERMES_HOME", ...))` at module scope is read once, at
    import — before any test's monkeypatch. Same class as memory
    `tests-polluted-the-production-audit-log`.
    """
    elsewhere = tmp_path / "other-home"
    monkeypatch.setenv("HERMES_HOME", str(elsewhere))
    assert nav_stack._stack_file().is_relative_to(elsewhere)


def test_back_is_bounded_so_history_cannot_grow_without_limit():
    for i in range(nav_stack.MAX_STACK + 20):
        nav_stack.push_nav(f"panel_{i}")
    depth = len(json.loads(nav_stack._stack_file().read_text())["stack"])
    assert depth <= nav_stack.MAX_STACK


# ---------------------------------------------------------------- the funnel


@pytest.fixture
def funnel(monkeypatch):
    """`handle_estate_action` with the renderers stubbed.

    `test_every_button_dispatches` says of itself: *"It is static. It proves an action
    reaches a branch, not that the branch works."* These tests are the other half — they
    run the real funnel and assert on what it did to history. `_dispatch` is stubbed
    because the question is the WIRING, and a real render would drag in the coordinator
    bridge, launchd probes and a 6s cold path.
    """
    from gateway.operator_shell import estate

    rendered = []

    def _stub(action, request_id="", source=None):
        rendered.append(action)
        return estate.PanelView(text=f"panel:{action}", buttons=[])

    monkeypatch.setattr(estate, "_dispatch", _stub)
    return estate, rendered


def test_a_tap_enters_history(funnel):
    estate, _rendered = funnel
    estate.handle_estate_action("run", "t-1")
    assert nav_stack.current()["action"] == "run"


def test_back_renders_the_previous_panel_not_the_word_back(funnel):
    """The end-to-end claim: tapping ← draws the screen you came from."""
    estate, rendered = funnel
    estate.handle_estate_action("run", "t-1")
    estate.handle_estate_action("tune", "t-2")
    rendered.clear()

    view = estate.handle_estate_action("back", "t-3")

    assert rendered == ["run"], f"← dispatched {rendered}, expected the previous panel"
    assert view.text == "panel:run"


def test_repeated_back_walks_outward_and_never_bounces(funnel):
    """The bug this fence exists for: a Back that re-enters history becomes a bounce.

    If the funnel pushed the panel ← just rendered, the stack would regrow by one on every
    tap and the operator would oscillate between two screens forever. The invariant is that
    N taps of ← visit N *distinct* ancestors and then reach the root.
    """
    estate, rendered = funnel
    for act in ("run", "tune", "sdlc"):
        estate.handle_estate_action(act, f"t-{act}")
    rendered.clear()

    for i in range(3):
        estate.handle_estate_action("back", f"b{i}")

    assert rendered == ["tune", "run", "refresh"], (
        f"← did not walk outward; it rendered {rendered}"
    )
    assert nav_stack.can_go_back() is False


def test_back_at_the_root_lands_on_home_rather_than_doing_nothing(funnel):
    estate, rendered = funnel
    estate.handle_estate_action("back", "t-root")
    assert rendered == ["refresh"], f"expected the home card, got {rendered}"


def test_a_failed_render_never_enters_history(funnel, monkeypatch):
    """Otherwise ← walks the operator back INTO the failure they were escaping."""
    from gateway.operator_shell import estate as E

    estate, _rendered = funnel
    estate.handle_estate_action("run", "t-ok")

    monkeypatch.setattr(
        E, "_dispatch",
        lambda a, r="", s=None: E.PanelView(text="⚠️ estate unavailable", ok=False),
    )
    estate.handle_estate_action("st_health", "t-degraded")

    assert nav_stack.current()["action"] == "run", (
        "a degraded card entered history; ← would now lead back into the error"
    )


# ---------------------------------------------------------------- the breadcrumb


def test_one_screen_is_not_a_trail():
    """A breadcrumb naming only the screen you are on repeats the header for no gain."""
    nav_stack.push_nav("refresh", "🏠 Home")
    assert nav_stack.breadcrumb() == ""


def test_breadcrumb_names_the_path_in_words_without_glyphs():
    for act in ("refresh", "run", "tune"):
        nav_stack.push_nav(act, panel_chrome.label_for(act))
    crumb = nav_stack.breadcrumb()
    assert crumb == "Home › Actions › Tune", crumb


def test_short_label_strips_glyphs_it_was_never_told_about():
    """The old implementation carried a hardcoded list of 28 emoji and missed the rest."""
    assert nav_stack.short_label("🫖 Kettle") == "Kettle"
    assert nav_stack.short_label("🛰️ Satellites") == "Satellites"


def test_an_unlabelled_action_shows_its_raw_name_rather_than_a_blank():
    """The fallback is the finding. A blank crumb hides the gap; `st_reconcile` reports it."""
    assert panel_chrome.label_for("st_reconcile") == "st_reconcile"
    assert panel_chrome.label_for("estate:tune") == "⚙️ Tune"
