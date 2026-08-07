"""Features panel — Telegram-native feature registry with grouped buttons."""
from typing import List, Tuple
from gateway.operator_shell.panel_chrome import nav, panel_stamp, with_nav
ButtonRow = List[Tuple[str, str]]

FEATURES = {
    "Monitor": [("📊 Status", "estate:status"), ("🔭 Prospector", "estate:prospector_daemon"),
                ("🚀 Fleet", "estate:fleet"), ("📥 Inbox", "estate:inbox"),
                ("📋 Missions", "estate:missions"), ("⚙️ Daemons", "estate:daemons")],
    "Diagnose": [("🔍 Diagnose", "estate:diagnose_panel"), ("🔮 Predict", "estate:predict_panel"),
                 ("💳 Fix credits", "estate:fix_guide:credits"), ("🛠 Fix all", "estate:fix_all")],
    "Improve": [("🧠 Health", "estate:otto_health"), ("📈 Score", "estate:score"),
                ("🧠 RSI", "estate:rsi"), ("📜 Activity", "estate:activity:7")],
    "Info": [("📋 Features", "estate:features_panel"), ("❓ Help", "estate:help"),
             ("🗺 Browse", "estate:find"), ("📸 Diff", "estate:diff")],
    "Actions": [("🎛 Run", "estate:run"), ("⚙️ Tune", "estate:tune"),
                ("💻 SDLC", "estate:sdlc"), ("🏠 Home", "estate:refresh")],
}

def render_features():
    lines = ["📋 *Features* — 30+ built", "", "_Tap any group:_", ""]
    buttons = []
    for group, items in FEATURES.items():
        lines.append(f"*{group}*")
        row = []
        for label, cb in items:
            row.append((label, cb))
        if row:
            buttons.append(row)
    lines.append(""); lines.append(panel_stamp("features"))
    buttons = with_nav(buttons, "features_panel")
    return "\n".join(lines), buttons
