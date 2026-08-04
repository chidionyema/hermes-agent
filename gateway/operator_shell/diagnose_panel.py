"""Diagnose panel — Telegram-native diagnostic card with pass/fail checks and fix buttons."""
from typing import List, Tuple
from gateway.operator_shell.panel_chrome import nav, panel_stamp, with_nav
ButtonRow = List[Tuple[str, str]]
import json, os, subprocess, sys
from pathlib import Path
SCRIPTS = Path(os.path.expanduser("~/.hermes/scripts"))

def _run_diagnostic(target=None):
    args = [sys.executable, str(SCRIPTS/"diagnostics.py")]
    if target: args.extend(["--" + target] if not target.startswith("-") else [target])
    else: args.append("--diagnose")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=20)
        try: return json.loads(r.stdout)
        except: return {"error": r.stdout[:200]}
    except Exception as e: return {"error": str(e)}

def render_diagnose(target=None):
    data = _run_diagnostic(target)
    lines = [f"🔍 *Diagnostic{f': {target}' if target else ''}*", ""]
    buttons = []
    if "error" in data:
        lines.append(f"⚠️ Diagnostic failed: {data['error'][:100]}")
    elif "moat" in data:
        m = data["moat"]
        lines.append(f"*Moat:* {'🟢' if m.get('status')=='ok' else '🔴'} {m.get('status','?')}")
        for check in m.get("checks", []):
            emoji = "🟢" if check.get("status")=="pass" else "🔴"
            lines.append(f"  {emoji} {check.get('check','?')}: {check.get('detail','?')[:80]}")
        if m.get("root_cause"):
            lines.append(f"\n*Root cause:* {m['root_cause']}")
        if m.get("fix"):
            lines.append(f"\n*Fix:* {m['fix'][:200]}")
            buttons.append([("📋 Fix guide", "estate:fix_guide:credits"), ("🛠 Auto-fix", "estate:fix_all")])
    
    if "engine" in data:
        e = data["engine"]
        lines.append(f"\n*Engine:* {'🟢' if e.get('status')=='ok' else '🔴'} {e.get('status','?')}")
        for check in e.get("checks", []):
            emoji = "🟢" if check.get("status")=="pass" else "🔴"
            lines.append(f"  {emoji} {check.get('check','?')}: {check.get('detail','?')[:80]}")

    lines.append(""); lines.append(panel_stamp("diagnose"))
    buttons.append([("🔍 Full diagnose", "estate:diagnose_panel"), ("🛠 Fix all", "estate:fix_all")])
    buttons = with_nav(buttons, "diagnose_panel")
    return "\n".join(lines), buttons

def render_fix_guide(target="credits"):
    if target == "credits":
        lines = ["💳 *Fix Credits* — step by step", "",
                 "Step 1: Open Cursor account", "  → [Open cursor.sh/account](https://cursor.sh/account)",
                 "Step 2: Top up or upgrade plan",
                 "Step 3: Add Anthropic credits", "  → [Open console.anthropic.com](https://console.anthropic.com)",
                 "Step 4: Verify by running `diagnose moat`", "",
                 "_Estimated time: 2 minutes_", "", panel_stamp("fix_guide")]
        buttons = [[("🔍 Verify fix", "estate:diagnose_panel:moat")],
                   [("🛠 Auto-fix", "estate:fix_all")]]
    else:
        lines = [f"📋 *Fix Guide: {target}*", "", "_No guide available for this target yet._", "", panel_stamp("fix_guide")]
        buttons = []
    buttons = with_nav(buttons, "fix_guide")
    return "\n".join(lines), buttons
