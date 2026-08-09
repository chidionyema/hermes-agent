"""Every configurable auxiliary role must have a home on every surface.

Measured 2026-08-08 (Operator UX programme, ~/.hermes/OPERATOR_UX_SPEC.md §2 P2):
three hardcoded lists of the same thing disagreed.

    DEFAULT_CONFIG["auxiliary"]            13 roles
    hermes_cli/main.py    _AUX_TASKS       12 roles  (no `monitor`)
    hermes_cli/web_server.py _AUX_TASK_SLOTS 11 roles  (no `monitor`, no `tts_audio_tags`)

Both omitted roles are live in code — `tts_audio_tags` at tools/tts_tool.py:194,
`monitor` at cron/scripts/classify_items.py:167 — so the estate was dispatching to
brains the operator could not see or retarget from any UI. That is Principle 4 of
the spec ("no silent config"): a config table with N entries and no renderer is a
hidden control panel.

The fix is derivation, not vigilance: both renderers now read
``config.AUXILIARY_TASK_KEYS``. This test is what stops a fourth copy appearing —
add a role to DEFAULT_CONFIG["auxiliary"] and this goes red until the role has a
row in every surface that claims to render roles.

Deliberately asserts SET EQUALITY per surface rather than a literal count of 13.
A count pins a number; equality pins the invariant, and only equality fails for
the right reason when role 14 arrives.
"""

import pytest

from hermes_cli.config import AUXILIARY_TASK_KEYS, DEFAULT_CONFIG


# The two roles the drift actually dropped. Named explicitly so that if someone
# "fixes" a future failure by deleting roles from DEFAULT_CONFIG rather than
# adding them to the renderers, this still goes red. A set-equality test alone
# passes trivially when both sides shrink together.
REGRESSION_ROLES = ("tts_audio_tags", "monitor")


def test_auxiliary_task_keys_derive_from_default_config():
    """The single source of truth is DEFAULT_CONFIG, not a restated tuple."""
    assert AUXILIARY_TASK_KEYS == tuple(DEFAULT_CONFIG["auxiliary"].keys())
    # Order is load-bearing: the panel renders in this order on every surface.
    assert len(AUXILIARY_TASK_KEYS) == len(set(AUXILIARY_TASK_KEYS)), "duplicate role key"


@pytest.mark.parametrize("role", REGRESSION_ROLES)
def test_regression_roles_are_still_configurable(role):
    """The two roles that were invisible are real, and still declared."""
    assert role in AUXILIARY_TASK_KEYS
    assert "provider" in DEFAULT_CONFIG["auxiliary"][role]


@pytest.mark.parametrize("role", REGRESSION_ROLES)
def test_cli_picker_renders_the_regression_roles(role):
    """`hermes model` -> auxiliary must offer every role, incl. the dropped ones."""
    from hermes_cli.main import _AUX_TASKS

    assert role in {key for key, _, _ in _AUX_TASKS}


def test_cli_picker_covers_every_configurable_role():
    from hermes_cli.main import _AUX_TASKS

    rendered = {key for key, _, _ in _AUX_TASKS}
    missing = set(AUXILIARY_TASK_KEYS) - rendered
    assert not missing, (
        f"roles configurable but absent from the `hermes model` auxiliary picker: "
        f"{sorted(missing)} — add a (key, display_name, description) row to _AUX_TASKS"
    )
    # Renderers may not invent roles that do not exist in config either: a row
    # for a key with no config entry is a control that writes nowhere.
    extra = rendered - set(AUXILIARY_TASK_KEYS)
    assert not extra, f"_AUX_TASKS renders roles with no config entry: {sorted(extra)}"


def test_web_dashboard_covers_every_configurable_role():
    pytest.importorskip("fastapi")
    from hermes_cli.web_server import _AUX_TASK_SLOTS

    assert tuple(_AUX_TASK_SLOTS) == AUXILIARY_TASK_KEYS, (
        "the Models page slot list has drifted from config again; it must derive "
        "from AUXILIARY_TASK_KEYS rather than restate the roles"
    )
