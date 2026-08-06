"""Every button the cockpit renders must reach a handler.

The defect this gates: 81 buttons across 14 panels emitted an `estate:<action>` that no
branch in the dispatcher tested for, so tapping any of them rendered `⚠️ Unknown action`
(`estate.py:1434`). 29 distinct actions were affected — most of them panels that had been
written, given a renderer, and never wired.

**Why this is a test and not a hand-maintained registry.** `telegram.py:279-281` already
rejects that design for labels, in as many words: *"Derived, never mapped: a hand-maintained
action→label table is the same drift the cockpit already suffers from."* The same objection
applies here, so both sides of this check are derived from the source at test time:

  DECLARED — every literal `"estate:<head>"` in a panel module, i.e. what a button can emit.
  HANDLED  — every `action == "x"` / `action in (...)` / `action.startswith("p")` the three
             dispatch modules actually test against.

Neither side is typed out by hand, so neither can drift. Adding a button for an unwired
action fails this test; wiring an action makes it pass with no bookkeeping.

The check is deliberately one-directional. HANDLED contains ~41 actions no button emits —
`sitrep`, `overview`, `map`, `self_improve` and other typed-command aliases. Those are
reachable by typing, which the cockpit treats as a first-class ingress, so asserting the
reverse containment would be wrong.

Two things this does NOT prove, stated so nobody reads more into a green run:

- It is static. It proves an action reaches a branch, not that the branch works. A branch
  that raises renders "Action failed" (`telegram.py:4595`) rather than "Unknown action" —
  a different defect this cannot see.
- Callbacks built by f-string with a runtime head (`f"estate:{act}"`, ~8 sites) are not
  checked, because their head is not knowable statically. Every one found so far resolves
  to a value already in DECLARED.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

OPERATOR_SHELL = Path(__file__).resolve().parents[3] / "gateway" / "operator_shell"
DISPATCH_MODULES = ("estate.py", "estate_pd.py", "estate_se.py")

# A literal callback string: "estate:<head>[:args]". The head is what the dispatcher branches
# on — `_dispatch` splits on the first colon (`estate.py:284-291`) and matches only `parts[0]`.
_LITERAL = re.compile(r'"estate:([a-zA-Z_][a-zA-Z0-9_.\-]*)')


def _registry() -> dict[str, tuple]:
    """`estate._PANELS`. A function, not a module-level import, so a broken `estate` shows up
    as a failing test rather than a collection error that takes the whole directory down."""
    from gateway.operator_shell.estate import _PANELS

    return _PANELS


def _handled() -> tuple[frozenset[str], frozenset[str]]:
    """Every action head the dispatch chain tests for: (exact literals, prefixes).

    Two sources, because the dispatcher has two: the hand-written `if action == ...` chain,
    and `estate._PANELS`, the table `_dispatch` consults just before giving up
    (`estate.py:1502-1507`). `_PANELS` is imported, not parsed — it is real dispatch state,
    so a key here is a handler in exactly the same sense a branch is.
    """
    from gateway.operator_shell.estate import _PANELS

    exact: set[str] = set(_PANELS)
    prefixes: set[str] = set()
    for name in DISPATCH_MODULES:
        src = (OPERATOR_SHELL / name).read_text(encoding="utf-8")
        exact.update(re.findall(r'action\s*==\s*"([^"]+)"', src))
        for group in re.finditer(r"action\s+in\s*\(([^)]*)\)", src, re.S):
            exact.update(re.findall(r'"([^"]*)"', group.group(1)))
        prefixes.update(re.findall(r'action\.startswith\(\s*"([^"]+)"', src))
    return frozenset(exact), frozenset(prefixes)


def _declared() -> dict[str, list[str]]:
    """Every action head a literal button can emit → the `file:line` sites that emit it."""
    sites: dict[str, list[str]] = {}
    for path in sorted(OPERATOR_SHELL.glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # `"estate:"` and `"estate:room:"` also appear as `.startswith()` arguments —
            # a prefix check is not a button.
            if ".startswith(" in line:
                continue
            for match in _LITERAL.finditer(line):
                sites.setdefault(match.group(1), []).append(f"{path.name}:{lineno}")
    return sites


# ── Quarantine ──────────────────────────────────────────────────────────────────────────
# These buttons exist and nothing behind them does. Unlike the 11 actions now in
# `estate._PANELS`, these have no working renderer to wire — so "fix the test" here means
# BUILDING a feature or DELETING a button, which is a product decision, not a wiring one.
# They are listed with the evidence rather than left failing, so the gate can protect the
# 41 buttons that were fixed instead of being permanently red and therefore ignored.
#
# This is not a suppression list that can quietly absorb new breakage: every entry must
# still be emitted by a live button (`test_the_quarantine_has_no_stale_entries`) and the
# count is ratcheted (`test_the_quarantine_does_not_grow`), so adding to it is a visible,
# deliberate edit in the diff.
_UNBUILT: dict[str, str] = {
    "fix_all": (
        "12 buttons, 4+ unrelated problem domains (moat checks, incidents, Otto policy, "
        "per-project CI) and no arg to tell them apart. The only callable, "
        "auto_fixer.py:177 auto_fix_all(), fixes cron/coordinator/config-push — none of "
        "those domains. A shared handler would be silently wrong at most of the 12 sites."
    ),
    "fix_all_safe": (
        "Nothing on disk. feature_registry.py:36 claims built:2026-08-02 citing test "
        "`test_fix_all_safe`, which does not exist anywhere in the repo."
    ),
    "onboard": (
        "Root renderer exists (projects.py:345) but sub-verbs new_product/client/template "
        "have no handler at all, and add/add_all call onboard_project() which writes "
        "projects.json (projects.py:41-44) with no confirm screen."
    ),
    "score": "score_driver.py:84/191 return dicts; needs a renderer and a choice of which.",
    "dependencies": "cross_project.py:70 dependency_map() returns a dict; needs a renderer.",
    "correlate": (
        "Ambiguous: two non-overlapping candidates, predictor.py:129 correlate_failures() "
        "and cross_project.py:54 correlate_estate(). Which one 'correlate' means is a decision."
    ),
    "compliance": (
        "auto_close_identity.py:671 compliance_report() returns a dict; needs a renderer."
    ),
    "logs": (
        "Three render_logs() exist (daemons.py:391, prospector_daemon.py:789, "
        "signal_engine.py:816) but each needs a unit prefix the bare button does not send. "
        "Needs a chooser panel or a declared default."
    ),
    "estate_health": "No renderer of any name.",
    "dashboard": (
        "No render_dashboard(). The namesake render_project_dashboard() needs a project_key "
        "that the bare `estate:dashboard` callback never supplies."
    ),
    "project_config": "No render_project_config() anywhere.",
    "operator_mode": (
        "commercial_ui.py:267 ClientMode.set_operator() exists but ClientMode is never "
        "instantiated anywhere — grep for 'ClientMode(' returns nothing."
    ),
    "setup_wizard": (
        "Only hermes_cli/setup.py:2899 run_setup_wizard(), an interactive TTY-prompt CLI "
        "that would block on a stdin Telegram cannot give it."
    ),
    # The four below are MUTATING. Wiring them is not just a renderer — each needs the
    # two-screen confirm pattern the daemon-stop path already uses (estate.py:934-961).
    "rsi_run": (
        "MUTATES. rsi_control.py:176 trigger_cycle() shells out to self_improve_runner.py "
        "--all, a real code-generating cycle. Needs the confirm pattern, not a wire."
    ),
    "rsi_pause": (
        "MUTATES THE WRONG FILE. rsi_control.py:163 toggle_learning() writes "
        "logs/meta-improver/OFF_SWITCH, but the live switch is meta/OFF_SWITCH "
        "(rsi_panel.py:19, the only one present on disk) — and with opposite polarity. "
        "Wiring as-is would toast 'paused' while learning stayed live. Also duplicates "
        "the already-working estate:disarm_learning."
    ),
    "rsi_resume": "MUTATES THE WRONG FILE — same toggle_learning() as rsi_pause; duplicates estate:arm_learning.",
    "idle_start": (
        "MUTATES. No start function exists at all — rsi_control.py only ever pgreps for "
        "idle_engine. Writing one means launching a persistent daemon from a tap."
    ),
    "deploy": (
        "MUTATES. No deploy function exists; the only deploy-adjacent code is read-only CI "
        "status. Triggering a real deployment from a tap needs the confirm pattern."
    ),
}


def _dead() -> dict[str, list[str]]:
    exact, prefixes = _handled()
    return {
        head: where
        for head, where in _declared().items()
        if head not in exact and not any(head.startswith(p) for p in prefixes)
    }


def test_the_scanners_find_something_at_all():
    """A parse that silently matched nothing would make every check below vacuous."""
    exact, prefixes = _handled()
    assert len(exact) > 60, f"dispatch parse found only {len(exact)} exact branches"
    assert {"st_", "se_", "pd_", "daemon_"} <= prefixes, f"missing prefixes: {prefixes}"
    assert len(_declared()) > 80, "button scan found suspiciously few callbacks"


def test_every_registered_panel_resolves_to_a_real_function():
    """`_PANELS` names its renderers as strings, so a typo would only surface on a tap."""
    import importlib

    from gateway.operator_shell.estate import _PANELS

    assert len(_PANELS) >= 11, f"registry shrank to {len(_PANELS)} entries"
    for action, (module_name, func_name, toast, arg_mode) in _PANELS.items():
        module = importlib.import_module(f"gateway.operator_shell.{module_name}")
        render = getattr(module, func_name, None)
        assert callable(render), f"estate:{action} -> {module_name}.{func_name} is not callable"
        assert toast, f"estate:{action} has no toast"
        assert arg_mode in ("none", "optional", "required"), f"estate:{action}: bad arg mode {arg_mode!r}"


def test_the_quarantine_has_no_stale_entries():
    """An entry whose buttons are all gone must leave, or the list becomes a graveyard."""
    declared = _declared()
    stale = sorted(head for head in _UNBUILT if head not in declared)
    assert not stale, (
        f"{len(stale)} quarantined action(s) no longer have any button — delete the entry: "
        + ", ".join(stale)
    )


def test_the_quarantine_does_not_grow():
    """A ratchet. New unwired buttons must not be absorbed by widening the exemption."""
    assert len(_UNBUILT) <= 18, (
        f"quarantine grew to {len(_UNBUILT)}. Build it or delete the button; if you are "
        f"deliberately deferring, lower this number in the same commit and say why."
    )


# ── Dispatch, not just declaration ──────────────────────────────────────────────────────
# Everything above is static: it proves an action reaches a branch. These go through
# `handle_estate_action` — the real entry point — with the renderers stubbed, so they prove
# the routing and the arg handling without doing any of the panels' I/O.


def _stub_coordinator():
    """`_dispatch` bails out with "Estate bridge down" if this raises; nothing else uses it
    on the registry path."""
    return object()


@pytest.fixture
def _routed(monkeypatch):
    """Replace every registered renderer with a sentinel that records how it was called."""
    import importlib

    from gateway.operator_shell import estate as E

    calls: dict[str, tuple] = {}
    monkeypatch.setattr(E, "_load_coordinator", _stub_coordinator)
    for action, (module_name, func_name, _toast, _arg) in E._PANELS.items():
        module = importlib.import_module(f"gateway.operator_shell.{module_name}")

        def _sentinel(*args, _action=action, _func=func_name, **kwargs):
            calls[_action] = args
            return f"SENTINEL {_action} via {_func}", [[("x", "estate:refresh")]]

        monkeypatch.setattr(module, func_name, _sentinel)
    return calls


@pytest.mark.parametrize("action", sorted(_registry()))
def test_a_registered_action_reaches_its_renderer(action, _routed):
    """The defect, at the level the operator meets it: this used to be "Unknown action"."""
    from gateway.operator_shell import estate as E

    arg = ":target" if E._PANELS[action][3] == "required" else ""
    view = E.handle_estate_action(f"{action}{arg}", f"t-{action}")
    assert "Unknown action" not in view.text, f"estate:{action} still falls through"
    assert view.text.startswith(f"SENTINEL {action}"), f"estate:{action} routed elsewhere: {view.text[:80]}"
    assert view.ok is not False
    assert view.buttons, f"estate:{action} rendered no buttons"


def test_the_arg_is_forwarded_only_where_the_renderer_takes_one(_routed):
    """`render_features()` takes no argument; forwarding one would TypeError on every tap."""
    from gateway.operator_shell import estate as E

    E.handle_estate_action("diagnose_panel:moat", "t-arg-opt")
    assert _routed["diagnose_panel"] == ("moat",), "an optional arg was not forwarded"

    E.handle_estate_action("features_panel", "t-arg-none")
    assert _routed["features_panel"] == (), "an arg was forwarded to a renderer taking none"

    E.handle_estate_action("diagnose_panel", "t-arg-bare")
    assert _routed["diagnose_panel"] == (), "a bare tap passed an empty arg positionally"


def test_a_required_arg_missing_is_answered_not_crashed(monkeypatch):
    """`render_project_dashboard` has no default (projects.py:275), so a bare
    `estate:project` would TypeError into the generic "Action failed"."""
    from gateway.operator_shell import estate as E

    monkeypatch.setattr(E, "_load_coordinator", _stub_coordinator)
    view = E.handle_estate_action("project", "t-project-bare")
    assert "needs a target" in view.text.lower()
    assert "Traceback" not in view.text
    assert view.ok is False


def test_an_explicit_branch_still_outranks_the_registry(_routed):
    """The registry is consulted last. If it ever shadowed a real branch, adding an entry
    would silently change working behaviour — so `refresh` must stay the mission card."""
    from gateway.operator_shell import estate as E

    view = E.handle_estate_action("refresh", "t-precedence")
    assert not view.text.startswith("SENTINEL")


def test_no_button_emits_an_action_nothing_handles():
    """The gate. A failure here means a tap renders `⚠️ Unknown action`."""
    dead = {h: w for h, w in _dead().items() if h not in _UNBUILT}
    if dead:
        report = "\n".join(
            f"  estate:{head} — {len(where)} button(s): {', '.join(where[:4])}"
            + (f" +{len(where) - 4} more" if len(where) > 4 else "")
            for head, where in sorted(dead.items(), key=lambda kv: -len(kv[1]))
        )
        pytest.fail(
            f"{sum(len(w) for w in dead.values())} buttons emit an action with no handler "
            f"({len(dead)} distinct):\n{report}"
        )
