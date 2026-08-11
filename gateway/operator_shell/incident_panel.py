"""Incident panel — Telegram-native incident list and detail views."""
from typing import List, Tuple
from gateway.operator_shell.panel_chrome import nav, panel_stamp, with_nav
ButtonRow = List[Tuple[str, str]]
import json, subprocess, sys
from pathlib import Path
SCRIPTS = Path("~/.hermes/scripts").expanduser()

def _run(cmd):
    r = subprocess.run([sys.executable, str(SCRIPTS/"incident_manager.py")] + cmd + ["--json"],
                      capture_output=True, text=True, timeout=15)
    try: return json.loads(r.stdout)
    except: return []

def render_incidents():
    active = _run(["--list"])
    history = _run(["--stats"])
    lines = ["🚨 *Incidents*", ""]
    if isinstance(active, list) and active:
        lines.append(f"*Active ({len(active)}):*")
        for inc in active[:5]:
            sev = {"critical":"🔴","error":"🔴","warning":"🟡","info":"🟢"}.get(inc.get("severity",""),"⚪")
            dur = ""
            if inc.get("duration_minutes"):
                dur = f" · {inc['duration_minutes']}m"
            lines.append(f"  {sev} {inc.get('title','?')[:60]}{dur}")
    else:
        lines.append("✅ No active incidents.")
    
    if isinstance(history, dict):
        lines.append(f"\n*This week:* {history.get('total',0)} total · {history.get('resolved',0)} resolved · {history.get('auto_resolved',0)} auto")
        lines.append(f"Avg MTTR: {history.get('avg_mttr_min',0)} min")
    
    lines.append(""); lines.append(panel_stamp("incidents"))
    buttons = [[("🔍 Diagnose", "estate:diagnose_panel"), ("🛠 Restart stuck jobs", "estate:fix_all")]]
    buttons = with_nav(buttons, "incidents")
    return "\n".join(lines), buttons
