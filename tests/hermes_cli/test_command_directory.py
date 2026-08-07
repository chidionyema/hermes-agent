"""
Tests for the user-facing categorized command directory.

The ``/help`` command should produce a directory grouped by what users DO
(cockpit / control / agent / sessions / schedule / system) instead of a
flat wall of 50+ commands in registry order.

These tests assert behavioral invariants of the directory, not frozen
output text. New commands can be added to any group; the structure must
remain.
"""
from __future__ import annotations

import pytest

from hermes_cli.command_directory import (
    category_keys,
    render_category_section,
    render_help_directory,
)


# ── DISPLAY-GROUP STRUCTURE ──────────────────────────────────────────────────


def test_six_display_groups():
    """The directory has exactly six user-facing groups in canonical order."""
    keys = category_keys()
    assert keys == ["home", "control", "agent", "session", "schedule", "system"]


def test_panel_lives_in_cockpit_group():
    """``/panel`` MUST be in Cockpit & Overview (the home group)."""
    lines = render_category_section("home")
    assert any("/panel" in line for line in lines), (
        f"/panel missing from home section: {lines}"
    )


def test_model_lives_in_agent_group():
    """``/model`` MUST be in Agent & Model group."""
    lines = render_category_section("agent")
    assert any("/model" in line for line in lines)


def test_cron_lives_in_schedule_group():
    """``/cron`` MUST be in Schedule & Skills group."""
    lines = render_category_section("schedule")
    assert any("/cron" in line for line in lines)


def test_no_command_appears_in_two_groups():
    """Every command belongs to exactly one display group."""
    seen: dict[str, str] = {}
    duplicates: list[tuple[str, str, str]] = []
    for key in category_keys():
        section = render_category_section(key)
        for line in section:
            stripped = line.lstrip()
            if stripped.startswith("`/"):
                # extract command name: `cmd` or `cmd `...
                name = stripped.split("`")[1].lstrip("/").split()[0]
                if name in seen and seen[name] != key:
                    duplicates.append((name, seen[name], key))
                seen[name] = key
    assert not duplicates, f"Commands appearing in multiple groups: {duplicates}"


def test_every_user_facing_command_is_in_some_group():
    """Every command in the registry that is user-facing appears somewhere."""
    from hermes_cli.commands import COMMAND_REGISTRY, _is_gateway_available

    rendered_names: set[str] = set()
    for key in category_keys():
        section = render_category_section(key)
        for line in section:
            stripped = line.lstrip()
            if stripped.startswith("`/"):
                name = stripped.split("`")[1].lstrip("/").split()[0]
                rendered_names.add(name)

    missing: list[str] = []
    for cmd in COMMAND_REGISTRY:
        if not _is_gateway_available(cmd):
            continue
        if cmd.cli_only:
            continue
        if cmd.name not in rendered_names:
            missing.append(cmd.name)
    assert not missing, f"User-facing commands missing from directory: {missing}"


# ── DOOR PROMINENCE ──────────────────────────────────────────────────────────


def test_help_directory_puts_panel_first():
    """The first user-actionable hint in /help MUST be /panel (the door)."""
    lines = render_help_directory(show_door=True)
    # The first non-blank, non-header line that mentions a command name
    for line in lines:
        if line.strip().startswith("👉") or "Start here" in line:
            assert "/panel" in line, (
                f"Expected /panel in door hint, got: {line!r}"
            )
            return
    pytest.fail("Door hint not found in directory")


def test_help_directory_can_hide_door():
    """When show_door=False, no door hint is present."""
    lines = render_help_directory(show_door=False)
    assert not any("Start here" in line for line in lines)


# ── ALIASES ARE SHOWN ────────────────────────────────────────────────────────


def test_aliases_visible_for_panel():
    """``/panel`` aliases (/menu, /cockpit, /control, /mission) MUST appear."""
    lines = render_help_directory(show_door=False)
    section = render_category_section("home")
    full = "\n".join(lines + section)
    for alias in ("/menu", "/cockpit", "/control", "/mission"):
        assert alias in full, f"Alias {alias} missing from /panel line"


def test_aliases_visible_for_brief():
    """``/brief`` alias /sitrep MUST appear."""
    section = render_category_section("home")
    full = "\n".join(section)
    assert "/sitrep" in full


# ── ARGS HINTS SURVIVE ───────────────────────────────────────────────────────


def test_args_hints_preserved():
    """Args hints (e.g. ``[name]`` for ``/new``) appear in the rendered output."""
    section = render_category_section("session")
    full = "\n".join(section)
    assert "/new" in full and "[name]" in full


# ── INCLUDE SKILL LINES ──────────────────────────────────────────────────────


def test_skill_lines_passed_through():
    """Skill section, when provided, appears after the categorized list."""
    skill_lines = ["⚡ **Skill Commands**:", "`/foo` — does a foo"]
    lines = render_help_directory(include_skill_lines=skill_lines)
    joined = "\n".join(lines)
    # Skill lines come after the categorized sections
    home_idx = next(i for i, l in enumerate(lines) if "Cockpit & Overview" in l)
    foo_idx = next(i for i, l in enumerate(lines) if "/foo" in l)
    assert foo_idx > home_idx


def test_no_skill_lines_omits_skill_section():
    """When no skill_lines, the pro-tip still surfaces."""
    lines = render_help_directory(include_skill_lines=None)
    joined = "\n".join(lines)
    assert "Pro tip" in joined


# ── COUNT PARITY WITH REGISTRY ───────────────────────────────────────────────


def test_total_commands_in_directory_matches_registry():
    """The sum of commands across all groups matches the registry size."""
    from hermes_cli.commands import COMMAND_REGISTRY, _is_gateway_available

    total_in_groups = 0
    for key in category_keys():
        section = render_category_section(key)
        total_in_groups += sum(
            1 for line in section
            if line.lstrip().startswith("`/")
        )

    total_in_registry = sum(
        1 for cmd in COMMAND_REGISTRY
        if _is_gateway_available(cmd) and not cmd.cli_only
    )
    assert total_in_groups == total_in_registry, (
        f"Group total {total_in_groups} != registry total {total_in_registry}"
    )