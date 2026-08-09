"""Command palette — searchable, grouped directory of ALL 77 estate actions.

The fix for "97 commands and nobody can remember them." Type ? or 'commands'
to open this. Every action is one tap away, grouped by what you want to DO.
"""
from typing import List, Tuple
from gateway.operator_shell.panel_chrome import nav, panel_stamp, with_nav
ButtonRow = List[Tuple[str, str]]

# Derived from natural_ops._PATTERNS — the canonical action registry.
# Grouped by user intent, not by implementation module.
COMMAND_GROUPS = [
    ("🏠 See what's happening", [
        ("📊 Status", "estate:status"),
        ("🏠 Home", "estate:refresh"),
        ("📋 Brief", "estate:brief"),
        ("📥 Inbox", "estate:inbox"),
        ("🚀 Fleet", "estate:fleet"),
        ("📋 Missions", "estate:missions"),
        ("📜 Activity", "estate:activity:7"),
    ]),
    ("🔍 Diagnose problems", [
        ("🔍 Diagnose", "estate:diagnose_panel"),
        ("🔮 Predict", "estate:predict_panel"),
        ("💳 Fix credits", "estate:fix_guide:credits"),
        ("🛠 Fix all", "estate:fix_all"),
        ("🧠 Otto health", "estate:otto_health"),
        ("📈 Score", "estate:score"),
        ("🔗 Estate health", "estate:estate_health"),
        ("🔗 Dependencies", "estate:dependencies"),
        ("🔗 Correlate failures", "estate:correlate"),
    ]),
    ("⚡ Take action", [
        ("⚡ Actions", "estate:run"),
        ("⏸ Pause estate spend", "estate:pause"),
        ("▶️ Resume estate spend", "estate:resume"),
        ("♻️ Restart coord", "estate:restart"),
        ("🛠 Fix all safe", "estate:fix_all_safe"),
        ("🧠 RSI", "estate:rsi"),
    ]),
    ("🔭 Inspect projects", [
        ("🔭 Prospector", "estate:prospector_daemon"),
        # The daemon panel above says whether the process is alive; this one says what it is
        # producing (last tick, spend, providers, backlog), read from the engine's own
        # status_snapshot().
        ("🎛 Now", "estate:prospector_now"),
        ("💹 Engine", "estate:signal_engine"),
        ("🛒 Store", "estate:st_status"),
        ("🏗 CI", "estate:builds"),
        ("📸 Diff", "estate:diff"),
    ]),
    ("⚙️ Configure", [
        ("⚙️ Tune", "estate:tune"),
        ("🗓 Cron", "estate:pd_cron"),
        ("🧠 Brain", "estate:brain"),
        ("🗓 Cron delivery", "estate:setup_cron_topic"),
    ]),
    ("🛠 Machine", [
        ("⚙️ Daemons", "estate:daemons"),
        ("🖥 Host", "estate:host"),
        ("📜 Log search", "estate:logs"),
    ]),
    ("💻 Code", [
        ("💻 SDLC", "estate:sdlc"),
        ("📝 Assign", "estate:code_prompt"),
        ("🗺 Browse", "estate:find"),
    ]),
    ("📋 Info", [
        ("📋 Features", "estate:features_panel"),
        ("❓ Help", "estate:help"),
        ("🎛 All commands", "estate:commands"),
    ]),
]

def render_commands():
    lines = ["🎛 *Command Palette* — tap anything", "",
             "_Type a word to filter, or tap a group:_", ""]
    buttons = []
    for group_name, items in COMMAND_GROUPS:
        row = []
        for label, cb in items[:3]:  # max 3 per row to fit phone
            row.append((label, cb))
        if row:
            lines.append(f"*{group_name}*")
            buttons.append(row)
    lines.append(""); lines.append(panel_stamp("commands"))
    buttons = with_nav(buttons, "commands")
    return "\n".join(lines), buttons
