"""
User-facing categorized command directory.

Renders ``/help`` and ``/commands`` as a navigable directory with logical
groups, instead of a flat alphabetical wall of 50+ commands.

Why this exists
---------------
The registry-level categories (Session / Configuration / Info) were shaped
around code organization, not user mental models. A founder complaint
(2026-07-31) — "how do i access the menu? is there one menu are there
multiple menus?" — surfaced because ``/panel`` lands mid-list among 58
peers, looking no more important than ``/rollback``. We add a single
display layer that re-groups commands by *what users actually do with them*:

* **Cockpit & Overview** — the home view; everything you might check first.
* **Control & Approvals** — pausing, approving, gating dangerous actions.
* **Agent & Model** — switch model, check usage, set behavior.
* **Sessions & History** — start / resume / undo / branch the conversation.
* **Schedule & Skills** — cron, blueprints, suggested automations.
* **System & Setup** — channels, profiles, restart, debug.

Each group is small (4-10 entries), with the cockpit pinned at the top.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from hermes_cli.commands import CommandDef, COMMAND_REGISTRY, _is_gateway_available, _resolve_config_gates


# Display order (matters — first group is the "door").
_DISPLAY_GROUPS: list[tuple[str, str, str]] = [
    ("🎛", "Cockpit & Overview",   "home"),
    ("⚙️",  "Control & Approvals",  "control"),
    ("🤖", "Agent & Model",         "agent"),
    ("💬", "Sessions & History",    "session"),
    ("📅", "Schedule & Skills",     "schedule"),
    ("🛠", "System & Setup",        "system"),
]


# Map every command to a display group by NAME.
# Names not in this map fall through to "System & Setup".
_DISPLAY_GROUP_BY_NAME: dict[str, str] = {
    # Cockpit & Overview — the things you check first
    "panel": "home",
    "brief": "home",
    "status": "home",
    "fleet": "home",
    "inbox": "home",
    "missions": "home",
    "help": "home",
    "commands": "home",
    "summary": "home",
    "insights": "home",
    "usage": "home",

    # Control & Approvals
    "stop": "control",
    "approve": "control",
    "deny": "control",
    "yolo": "control",
    "notify": "control",
    "busy": "control",
    "revert": "control",
    "platform": "control",
    "rollback": "control",

    # Agent & Model
    "model": "agent",
    "personality": "agent",
    "fast": "agent",
    "reasoning": "agent",
    "verbose": "agent",
    "agents": "agent",
    "codex-runtime": "agent",
    "gquota": "agent",
    "credits": "agent",

    # Sessions & History
    "start": "session",
    "new": "session",
    "topic": "session",
    "retry": "session",
    "undo": "session",
    "title": "session",
    "branch": "session",
    "compress": "session",
    "resume": "session",
    "sessions": "session",
    "background": "session",
    "queue": "session",
    "steer": "session",
    "goal": "session",
    "subgoal": "session",

    # Schedule & Skills
    "cron": "schedule",
    "blueprint": "schedule",
    "suggestions": "schedule",
    "memory": "schedule",
    "skills": "schedule",
    "bundles": "schedule",
    "kanban": "schedule",
    "curator": "schedule",

    # System & Setup
    "whoami": "system",
    "profile": "system",
    "sethome": "system",
    "footer": "system",
    "voice": "system",
    "restart": "system",
    "version": "system",
    "debug": "system",
    "update": "system",
    "reload-mcp": "system",
    "reload-skills": "system",
    "config": "system",
    "tools": "system",
    "toolsets": "system",
    "skin": "system",
    "indicator": "system",
    "statusbar": "system",
}


def _group_for(name: str) -> str:
    """Return the display-group key for *name*. Default to 'system'."""
    return _DISPLAY_GROUP_BY_NAME.get(name, "system")


def _display_group_meta(key: str) -> tuple[str, str, str]:
    for emoji, title, k in _DISPLAY_GROUPS:
        if k == key:
            return emoji, title, k
    return ("🛠", "System & Setup", "system")


def _format_command_line(cmd: CommandDef) -> str:
    """Render a single command as ``/name [args] — description (alias: …)``."""
    args = f" `{cmd.args_hint}`" if cmd.args_hint else ""
    alias_parts: list[str] = []
    for a in cmd.aliases:
        # Skip internal aliases like reload_mcp (underscore variant)
        if a.replace("-", "_") == cmd.name.replace("-", "_") and a != cmd.name:
            continue
        alias_parts.append(f"/{a}")
    alias_note = f" _↪ `{', '.join(alias_parts)}`_" if alias_parts else ""
    return f"`/{cmd.name}`{args} — {cmd.description}{alias_note}"


def render_help_directory(
    include_skill_lines: Iterable[str] | None = None,
    *,
    show_door: bool = True,
) -> list[str]:
    """Render the user-facing ``/help`` directory.

    Returns a list of lines ready for telegramize + send.

    Args:
        include_skill_lines: Extra lines to append (skill commands section).
        show_door: When True, prepend the 🎛 /panel door hint.
    """
    overrides = _resolve_config_gates()
    by_group: dict[str, list[CommandDef]] = defaultdict(list)
    for cmd in COMMAND_REGISTRY:
        if not _is_gateway_available(cmd, overrides):
            continue
        by_group[_group_for(cmd.name)].append(cmd)

    lines: list[str] = []

    if show_door:
        lines.append("🎛 **Hermes Command Directory**")
        lines.append("")
        lines.append(
            "👉 **Start here:** `/panel` — opens the cockpit "
            "(one card, every operation a tap)"
        )
        lines.append(
            "   Aliases: `/menu`, `/cockpit`, `/control`, `/mission`"
        )
        lines.append(
            "   Inside `/panel`, the 🔎 button searches every command by name — "
            "you rarely need the list below."
        )
        lines.append("")
        lines.append("───")
        lines.append("")

    for key, (emoji, title, _) in [(k, _display_group_meta(k)) for k in
                                     (g[2] for g in _DISPLAY_GROUPS)]:
        cmds = by_group.get(key, [])
        if not cmds:
            continue
        lines.append(f"{emoji} **{title}** _({len(cmds)})_")
        for cmd in cmds:
            lines.append(f"  {_format_command_line(cmd)}")
        lines.append("")

    lines.append("───")
    lines.append("")
    if include_skill_lines:
        lines.extend(include_skill_lines)
        lines.append("")
    lines.append(
        "💡 **Pro tip:** type `/panel` to see the cockpit — every command "
        "above is one tap away from there."
    )
    lines.append("   Type `/commands N` for the flat alphabetical list (page N).")

    return lines


def render_category_section(category_key: str) -> list[str]:
    """Render a single category — used by future 'open category' affordances."""
    overrides = _resolve_config_gates()
    lines: list[str] = []
    emoji, title, _ = _display_group_meta(category_key)
    lines.append(f"{emoji} **{title}**")
    lines.append("")
    for cmd in COMMAND_REGISTRY:
        if not _is_gateway_available(cmd, overrides):
            continue
        if _group_for(cmd.name) != category_key:
            continue
        lines.append(f"  {_format_command_line(cmd)}")
    return lines


def category_keys() -> list[str]:
    """Display-group keys in render order (used by tests / affordances)."""
    return [k for _, _, k in _DISPLAY_GROUPS]