"""Smart home — context-aware mission card that shows ONLY what matters right now.

The old mission card showed everything all the time. This one adapts:
- All clear: "✅ Estate healthy · $1.64 spent" + 3 quick actions
- Issues: Red banner with the #1 problem + direct fix button
- Incidents: Incident count + quick access
- Always: Max 8 buttons total (spine + 2-3 context actions)
"""

from typing import List, Tuple, Optional
from gateway.operator_shell.panel_chrome import nav, with_nav
import json, os, time
from pathlib import Path

ButtonRow = List[Tuple[str, str]]

def _quick_status() -> dict:
    """Ultra-fast status check. Returns in <100ms."""
    status = {"engine": "?", "prospector": "?", "incidents": 0, "decisions": 0}
    # Prospector (fast file check)
    try:
        ticks = Path.home()/"Documents/code/prospector/store/scheduler/ticks.jsonl"
        if ticks.is_file():
            lines = ticks.read_text().splitlines()
            recent = lines[-3:]
            errs = sum(1 for ln in recent if '"error": "' in ln)
            status["prospector"] = "🔴 moat down" if errs >= 2 else ("🟡 degraded" if errs == 1 else "🟢 healthy")
            # Spend
            for ln in reversed(lines):
                try:
                    t = json.loads(ln)
                    if t.get("today_spend_usd"):
                        status["spend"] = float(t["today_spend_usd"])
                        break
                except: pass
    except: pass
    
    # Incidents
    try:
        inc_dir = Path.home()/".hermes"/"state"/"incidents"
        if inc_dir.is_dir():
            status["incidents"] = sum(1 for f in inc_dir.glob("*.json")
                                      if json.loads(f.read_text()).get("status") not in ("resolved","postmortem_done"))
    except: pass
    
    return status

def render_smart_home() -> Tuple[str, bool, List[ButtonRow]]:
    """The redesigned home screen. Adapts to estate state."""
    st = _quick_status()
    spend = st.get("spend", 0)
    incidents = st.get("incidents", 0)
    prospector = st.get("prospector", "?")
    
    buttons = []
    
    # ── Determine the primary state ──
    if "🔴" in prospector:
        # CRITICAL: Moat is down
        lines = [
            "🔴 *Prospector moat down*",
            "",
            f"_AI providers unavailable. Pipeline blocked._",
            f"_Spend today: ${spend:.2f}_" if spend else "",
            "",
        ]
        buttons = [
            [("🛠 Restart stuck jobs", "estate:fix_all"), ("🔍 Diagnose", "estate:diagnose_panel")],
            [("⏸ Pause Prospector", "estate:pd_pause"), ("💳 Fix credits", "estate:fix_guide:credits")],
        ]
        paused = False
        
    elif incidents > 0:
        # WARNING: Active incidents
        lines = [
            f"🟡 *{incidents} active incident{'s' if incidents > 1 else ''}*",
            "",
            f"_Prospector: {prospector}_",
            f"_Spend today: ${spend:.2f}_" if spend else "",
            "",
        ]
        buttons = [
            [("🚨 View incidents", "estate:incidents"), ("🛠 Restart stuck jobs", "estate:fix_all")],
        ]
        paused = False
        
    else:
        # ALL CLEAR
        lines = [
            "🟢 *All clear*",
            "",
            f"_Prospector: {prospector}_",
            f"_Spend today: ${spend:.2f}_" if spend else "",
            "",
            "_What would you like to do?_",
            "",
        ]
        buttons = [
            [("📊 Status", "estate:status"), ("🔍 Diagnose", "estate:diagnose_panel")],
            [("🎛 All commands", "estate:commands"), ("📋 Brief", "estate:brief")],
        ]
        paused = False
    
    # ── Always show: quick nav ──
    lines = [l for l in lines if l]  # remove empty lines
    text = "\n".join(lines)
    
    # Spine at bottom (adds 4 buttons)
    buttons = with_nav(buttons, "home")
    
    return text, paused, buttons
