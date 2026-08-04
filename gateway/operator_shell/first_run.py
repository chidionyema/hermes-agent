"""First-run experience + error humanizer."""
from typing import List, Tuple
from gateway.operator_shell.panel_chrome import nav, panel_stamp, with_nav
ButtonRow = List[Tuple[str, str]]
import os, json
from pathlib import Path
from datetime import datetime, timezone

HERMES = Path(os.path.expanduser("~/.hermes"))
FIRST_RUN_FLAG = HERMES / "meta" / ".first-run-complete"

def is_first_run():
    return not FIRST_RUN_FLAG.is_file()

def mark_first_run_complete():
    FIRST_RUN_FLAG.parent.mkdir(parents=True, exist_ok=True)
    FIRST_RUN_FLAG.write_text(datetime.now(timezone.utc).isoformat())

def render_welcome():
    lines = [
        "👋 *Welcome to Otto!*",
        "",
        "I'm your autonomous estate manager. I monitor your projects, diagnose problems, and fix them — often before you notice.",
        "",
        "*Here's what I found:*",
    ]
    # Auto-discover projects
    try:
        from scripts.estate_migrator import discover_projects
        projects = discover_projects()
        if projects:
            lines.append(f"  📦 {len(projects)} projects detected")
            for p in projects[:5]:
                lines.append(f"    • {p['name']} — {p.get('repo','?')}")
        else:
            lines.append("  📦 No projects auto-detected. Run `otto setup` to add yours.")
    except:
        lines.append("  📦 Run `otto setup` to configure your estate.")
    
    lines += [
        "",
        "*Get started:*",
        "• Type `?` to see everything I can do",
        "• Type `status` for a quick overview",
        "• Type `diagnose` to check for problems",
        "• Type `help` for the full directory",
        "",
        "_I'll send a morning digest at 9am with yesterday's summary._",
        "",
        panel_stamp("welcome"),
    ]
    buttons = [
        [("🎛 See all commands", "estate:commands"), ("📊 Status", "estate:status")],
        [("🔍 Diagnose", "estate:diagnose_panel"), ("❓ Help", "estate:help")],
        [("⚙️ Setup wizard", "estate:setup_wizard")],
    ]
    buttons = with_nav(buttons, "welcome")
    mark_first_run_complete()
    return "\n".join(lines), buttons

# ── Error Humanizer ──
ERROR_MAP = {
    "ProviderExhaustedError": "API credits exhausted",
    "credit balance is too low": "Account balance is too low — add credits",
    "usage limit reached": "Usage limit reached — upgrade your plan",
    "RateLimitError": "Rate limited — wait and retry",
    "Connection error": "Network connection failed — check internet",
    "timed out": "Request timed out — service may be down",
    "certificate verify failed": "SSL certificate error — check system clock",
    "getaddrinfo ENOTFOUND": "DNS resolution failed — check internet connection",
    "Address already in use": "Port conflict — another process is using this port",
    "No such process": "Process not found — it may have already stopped",
    "moat_preflight": "Verification pipeline cannot reach AI providers",
    "no trusted moat brain": "All AI providers (Cursor, Claude) are unavailable",
    "cursor_cli": "Cursor CLI",
    "claude_cli": "Claude CLI",
}

def humanize_error(raw_error):
    """Convert raw error string to plain English."""
    result = raw_error
    for pattern, replacement in ERROR_MAP.items():
        if pattern.lower() in raw_error.lower():
            result = result.replace(pattern, replacement)
    # Truncate very long errors
    if len(result) > 200:
        result = result[:197] + "..."
    return result
