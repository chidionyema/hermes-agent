"""Usage tracker + smart suggestions engine.

Tracks which commands the user actually uses, shows top commands on home,
and suggests "you might also want" after each action.
"""
import json, os, time
from pathlib import Path
from typing import List, Tuple, Optional

HERMES = Path(os.path.expanduser("~/.hermes"))
USAGE_FILE = HERMES / "state" / "command-usage.json"
SUGGESTIONS_FILE = HERMES / "state" / "command-suggestions.json"

# What to suggest after each action (curated, not auto-generated)
SUGGESTIONS = {
    "diagnose": ["fix_guide:credits", "predict_panel", "fix_all"],
    "diagnose_panel": ["fix_guide:credits", "predict_panel", "fix_all"],
    "prospector_daemon": ["diagnose_panel:moat", "pd_pause", "pd_cron"],
    "status": ["diagnose_panel", "brief", "inbox"],
    "inbox": ["approve", "missions", "refresh"],
    "fleet": ["builds", "diff", "missions"],
    "run": ["tune", "status", "daemons"],
    "tune": ["run", "status", "brain"],
    "fix_all": ["diagnose_panel", "status", "otto_health"],
    "predict_panel": ["fix_guide:credits", "diagnose_panel"],
    "fix_guide": ["diagnose_panel", "predict_panel"],
    "otto_health": ["score", "rsi", "fix_all"],
    "estate_health": ["correlate", "dependencies", "diagnose_panel"],
}

def record_usage(action: str):
    """Record that the user used this action."""
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if USAGE_FILE.is_file():
        try: data = json.loads(USAGE_FILE.read_text())
        except: pass
    data[action] = data.get(action, 0) + 1
    # Keep only last 100 entries
    if len(data) > 100:
        sorted_items = sorted(data.items(), key=lambda x: -x[1])
        data = dict(sorted_items[:100])
    USAGE_FILE.write_text(json.dumps(data))

def get_top_commands(n=5) -> List[Tuple[str, int]]:
    """Get the user's most-used commands."""
    if not USAGE_FILE.is_file(): return []
    try:
        data = json.loads(USAGE_FILE.read_text())
        return sorted(data.items(), key=lambda x: -x[1])[:n]
    except: return []

def get_suggestions(action: str) -> List[Tuple[str, str]]:
    """Get suggested next actions. Returns [(label, callback), ...]."""
    base = action.split(":")[0] if ":" in action else action
    suggestions = SUGGESTIONS.get(base, SUGGESTIONS.get(action, []))
    result = []
    for s in suggestions:
        # Convert to label
        label_map = {
            "fix_guide:credits": ("💳 Fix credits", "estate:fix_guide:credits"),
            "predict_panel": ("🔮 Predict", "estate:predict_panel"),
            "fix_all": ("🛠 Restart stuck jobs", "estate:fix_all"),
            "pd_pause": ("⏸ Pause Prospector", "estate:pd_pause"),
            "pd_cron": ("🗓 Cron", "estate:pd_cron"),
            "diagnose_panel": ("🔍 Diagnose", "estate:diagnose_panel"),
            "diagnose_panel:moat": ("🔍 Diagnose moat", "estate:diagnose_panel:moat"),
            "brief": ("📋 Brief", "estate:brief"),
            "inbox": ("📥 Inbox", "estate:inbox"),
            "missions": ("📋 Missions", "estate:missions"),
            "refresh": ("🏠 Home", "estate:refresh"),
            "status": ("📊 Status", "estate:status"),
            "builds": ("🏗 CI", "estate:builds"),
            "diff": ("📸 Diff", "estate:diff"),
            "tune": ("⚙️ Tune", "estate:tune"),
            "run": ("⚡ Actions", "estate:run"),
            "daemons": ("⚙️ Daemons", "estate:daemons"),
            "brain": ("🧠 Brain", "estate:brain"),
            "otto_health": ("🧠 Otto health", "estate:otto_health"),
            "score": ("📈 Score", "estate:score"),
            "rsi": ("🧠 RSI", "estate:rsi"),
            "correlate": ("🔗 Linked failures", "estate:correlate"),
            "dependencies": ("🔗 Dependencies", "estate:dependencies"),
            "approve": ("📥 Inbox", "estate:inbox"),
        }
        mapped = label_map.get(s, (s, f"estate:{s}"))
        result.append(mapped)
    return result
