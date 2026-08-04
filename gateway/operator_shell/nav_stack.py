"""Navigation stack — persistent back/forward history for the operator shell.

The core UX fix: every panel dispatch pushes to a history stack stored on disk.
The nav() function reads this stack and adds ← Back / → Forward buttons.
Breadcrumbs show where you are. The operator never has to "restart the flow."

Architecture:
- push_nav(action, label) — called by estate.py on every panel dispatch
- pop_nav() — removes current, returns previous (used by Back)
- forward_nav() — returns the panel we backed away from
- nav_history() — last N entries for breadcrumbs
- File: ~/.hermes/state/nav-stack.json
"""

import json, os, time
from pathlib import Path
from typing import List, Tuple, Optional

HERMES = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
STACK_FILE = HERMES / "state" / "nav-stack.json"
MAX_STACK = 50

def _load():
    STACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not STACK_FILE.is_file():
        return {"stack": [], "forward_stack": [], "current": None}
    try:
        return json.loads(STACK_FILE.read_text())
    except:
        return {"stack": [], "forward_stack": [], "current": None}

def _save(data):
    STACK_FILE.write_text(json.dumps(data))

def push_nav(action: str, label: str = ""):
    """Called on every panel dispatch. Pushes to history stack."""
    data = _load()
    # Don't push duplicate consecutive entries
    if data.get("current") and data["current"].get("action") == action:
        data["current"]["label"] = label or action
        data["current"]["ts"] = time.time()
        _save(data)
        return
    
    # Push current to stack if it exists
    if data.get("current"):
        data["stack"].append(data["current"])
        # Trim old entries
        if len(data["stack"]) > MAX_STACK:
            data["stack"] = data["stack"][-MAX_STACK:]
    
    # Clear forward stack when navigating to new panel
    data["forward_stack"] = []
    
    # Set new current
    data["current"] = {"action": action, "label": label or action, "ts": time.time()}
    _save(data)

def go_back() -> Optional[dict]:
    """Navigate back. Returns the previous panel, or None if at root."""
    data = _load()
    if not data["stack"]:
        return None
    
    # Push current to forward stack
    if data.get("current"):
        data["forward_stack"].append(data["current"])
    
    # Pop previous
    prev = data["stack"].pop()
    data["current"] = prev
    _save(data)
    return prev

def go_forward() -> Optional[dict]:
    """Navigate forward after going back. Returns the panel we backed away from."""
    data = _load()
    if not data["forward_stack"]:
        return None
    
    # Push current to stack
    if data.get("current"):
        data["stack"].append(data["current"])
    
    # Pop forward
    nxt = data["forward_stack"].pop()
    data["current"] = nxt
    _save(data)
    return nxt

def can_go_back() -> bool:
    data = _load()
    return len(data.get("stack", [])) > 0

def can_go_forward() -> bool:
    data = _load()
    return len(data.get("forward_stack", [])) > 0

def breadcrumb(max_depth: int = 3) -> str:
    """Return breadcrumb text like '🏠 Home > 🔍 Diagnose > Moat'."""
    data = _load()
    parts = []
    
    # Add stack entries (ancestors, oldest first)
    for entry in data.get("stack", [])[-max_depth:]:
        label = entry.get("label", entry.get("action", "?"))
        short = _short_label(label)
        parts.append(short)
    
    # Add current
    current = data.get("current", {})
    label = current.get("label", current.get("action", "?"))
    parts.append(_short_label(label))
    
    # Only show last N
    if len(parts) > max_depth + 1:
        parts = ["…"] + parts[-(max_depth):]
    
    return " > ".join(parts) if parts else ""

def _short_label(label: str) -> str:
    """Shorten labels for breadcrumbs."""
    # Remove emoji prefixes
    short = label
    for prefix in ["🏠 ", "⚡ ", "💻 ", "⚙️ ", "🗺 ", "🔍 ", "🔭 ", "📊 ", "📋 ", "🚀 ",
                    "🧠 ", "💰 ", "🛠 ", "💹 ", "🎛 ", "🎙 ", "📥 ", "📜 ", "📸 ", "🏗 ",
                    "🔮 ", "💳 ", "🚨 ", "🔗 ", "👥 ", "🔧 ", "⏸ ", "▶️ "]:
        if short.startswith(prefix):
            short = short[len(prefix):]
            break
    if len(short) > 20:
        short = short[:17] + "…"
    return short
