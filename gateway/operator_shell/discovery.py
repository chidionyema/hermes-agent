"""
Discovery & navigation layer — the missing welcome mat.

Every panel now includes contextual hints about what you can do.
First-time users get a guided intro. Every message ends with
"Try typing..." suggestions.
"""

from typing import List, Tuple, Optional

ButtonRow = List[Tuple[str, str]]


# ═══════════════════════════════════════════════
# Contextual hints — appended to every panel
# ═══════════════════════════════════════════════

PANEL_HINTS = {
    "home": (
        "\n\n💡 *Try typing:*\n"
        "• `what's broken` — see what needs attention\n"
        "• `deploy prospector` — trigger a deploy\n"
        "• `health` — see Otto's self-improvement score\n"
        "• `client tie` — switch to client view\n"
        "• `onboard` — add a new project"
    ),
    "project": (
        "\n\n💡 *Try typing:*\n"
        "• `deploy` — trigger deployment\n"
        "• `fix ci` — fix CI issues\n"
        "• `activity` — see recent work\n"
        "• `health` — project health details"
    ),
    "health": (
        "\n\n💡 *Try typing:*\n"
        "• `what did otto learn` — weekly digest\n"
        "• `compliance` — compliance report\n"
        "• `policies` — active policy list\n"
        "• `fix all` — run auto-fixes"
    ),
    "projects": (
        "\n\n💡 *Try typing:*\n"
        "• `show <project>` — open a project\n"
        "• `onboard` — add new project\n"
        "• `client <project>` — client view"
    ),
    "onboarding": (
        "\n\n💡 *Just type your answers!*\n"
        "I'll guide you through each step."
    ),
    "default": (
        "\n\n💡 *Try typing:*\n"
        "• `help` — see everything I can do\n"
        "• `home` — go back to home\n"
        "• `what's broken` — see issues"
    ),
}


def get_hint(panel: str) -> str:
    """Get the contextual hint for a panel type."""
    return PANEL_HINTS.get(panel, PANEL_HINTS["default"])


# ═══════════════════════════════════════════════
# Welcome / first-time experience
# ═══════════════════════════════════════════════

WELCOME_MESSAGE = """👋 *Welcome to Otto!*

I'm your AI operator. I manage 14 projects, monitor CI, track self-improvement, and keep the estate healthy.

*What you can do right now:*

📊 *See what's happening*
• Tap 🏠 Home or type `home`
• Type `what's broken` for urgent issues
• Type `status` for the full picture

📁 *Work with projects*
• Type `prospector` to open any project
• Type `deploy prospector` to trigger a deploy
• Type `all projects` to browse everything

🧠 *Check self-improvement*
• Type `health` for Otto's learning score
• Type `what did otto learn` for weekly digest

➕ *Add new projects*
• Type `onboard` — I'll guide you through it

👤 *Client mode*
• Type `client tie` to see what a client sees
• Type `operator mode` to switch back

🛠 *Fix things*
• Type `fix all` to auto-fix known issues
• Type `logs error` to search error logs

💬 *Just talk to me*
• I understand natural language — no need to memorize commands
• Try: "show me what's broken" or "deploy the introduction exchange"

───────────────────────
*Quick keyboard shortcuts are below ↓*"""


# ═══════════════════════════════════════════════
# Help / capability discovery
# ═══════════════════════════════════════════════

HELP_SECTIONS = {
    "🏠 Home & Status": [
        ("`home` / `refresh`", "Go to home screen"),
        ("`what's broken`", "See urgent issues"),
        ("`status`", "Estate overview"),
        ("`all projects`", "Browse all projects"),
    ],
    "📁 Projects": [
        ("`<project name>`", "Open project dashboard"),
        ("`deploy <project>`", "Trigger deployment"),
        ("`show <project>`", "View project details"),
        ("`client <project>`", "Switch to client view"),
        ("`operator mode`", "Back to operator view"),
    ],
    "🛠 Actions": [
        ("`fix all`", "Auto-fix known issues"),
        ("`fix ci <project>`", "Fix CI for a project"),
        ("`pause <project>`", "Pause a project"),
        ("`resume <project>`", "Resume a project"),
    ],
    "🧠 Self-Improvement": [
        ("`health`", "Otto health score"),
        ("`what did otto learn`", "Weekly learning digest"),
        ("`compliance`", "Compliance report"),
        ("`policies`", "Active policy list"),
    ],
    "🔍 Discovery": [
        ("`logs <query>`", "Search error logs"),
        ("`who is working`", "Active missions"),
        ("`onboard`", "Add new project"),
        ("`help`", "Show this help"),
    ],
}


def render_help() -> str:
    """Render the full help/capability directory."""
    lines = ["🎛 *What can Otto do?*", "", "Just type any of these:", ""]
    
    for section, items in HELP_SECTIONS.items():
        lines.append(f"*{section}*")
        for cmd, desc in items:
            lines.append(f"• {cmd} — {desc}")
        lines.append("")
    
    lines.append("💡 *You can also just ask naturally:*")
    lines.append('_"deploy the prospector project"_, _"show me what\'s on fire"_, _"how healthy is otto?"_')
    
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# Natural language fallback — when nothing matches
# ═══════════════════════════════════════════════

FALLBACK_SUGGESTIONS = [
    "I didn't quite catch that. Try:",
    "",
    "• `what's broken` — see urgent issues",
    "• `health` — self-improvement score",
    "• `deploy <project>` — trigger a deploy",
    "• `help` — see everything I can do",
    "• `home` — go back to home",
    "",
    "Or just describe what you want naturally!",
]


def render_fallback() -> str:
    """When natural language doesn't match, show suggestions."""
    return "\n".join(FALLBACK_SUGGESTIONS)
